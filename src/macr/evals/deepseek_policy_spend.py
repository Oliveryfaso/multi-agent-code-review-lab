"""Durable local USD reservations for a separately approved DeepSeek pilot.

This ledger gates dispatch locally. It cannot impose a provider-side hard cap:
an unknown remote outcome retains its full reservation and halts the batch.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import os
from pathlib import Path
import re

from macr.evals.deepseek_policy_batch import (
    PolicyBatchError, _digest, _load_batch, _locked, _read, _read_json, _registry,
    _scan_names, _ticket, _write_new,
)
from macr.evals.deepseek_policy_runner import ARM_REQUESTS
from macr.investigation.scope import open_directory
from macr.investigation.validation import ContractError


SCHEMA = "macr.deepseek.policy.spend.v1"
PRICE_SNAPSHOT = {
    "model_id": "deepseek-flash",
    "checked_date": "2026-10-28",
    "price_unit": "USD_per_1M_tokens",
    "peak_input_cache_miss_usd": "0.30",
    "peak_input_cache_hit_usd": "0.006",
    "peak_output_usd": "1.20",
    "context_limit_tokens": 1_000_000,
    "max_output_tokens_per_request": 6_000,
    "reservation_usd": "0.33",
}
_MICRO = 1_000_000
_RESERVATION = 330_000
_CELL = re.compile(r"[0-9a-f]{32}\Z")
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?\Z")
_ENTRY = re.compile(r"([0-9a-f]{32})-(0[1-3])\.(reservation|outcome)\.json\Z")
_MAX_RECORD = 4096
_MAX_POLICY = 64 * 1024
_MAX_RECORDS = 2 * 84


class PolicySpendError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PolicySpendError(code)


def _usd(value: int) -> str:
    return f"{value // _MICRO}.{value % _MICRO:06d}"


def _parse_usd(value: object, *, code: str) -> int:
    _require(type(value) is str and _MONEY.fullmatch(value) is not None, code)
    try:
        scaled = Decimal(value) * _MICRO
        result = int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise PolicySpendError(code) from None
    _require(scaled == result, code)
    return result


def _batch(control_root, batch_sha256=None):
    try:
        root, batch = _load_batch(control_root)
    except PolicyBatchError:
        raise PolicySpendError("spend_batch_invalid") from None
    if batch_sha256 is not None:
        _require(type(batch_sha256) is str and
                 batch_sha256 == batch["batch_sha256"], "spend_batch_mismatch")
    return root, batch


def _policy(root: Path, batch: dict, *, allow_carry=True) -> dict:
    try:
        policy = _read_json(root, "spend/policy.json", _MAX_POLICY,
                            "spend_policy_invalid")
    except PolicyBatchError:
        raise PolicySpendError("spend_policy_invalid") from None
    expected = {"schema", "batch_sha256", "max_usd", "max_requests", "price_snapshot"}
    if "carry_forward" in policy:
        _require(allow_carry, "spend_predecessor_chain")
        expected.add("carry_forward")
    if "recovery_forward" in policy:
        _require(allow_carry and "carry_forward" not in policy,
                 "spend_predecessor_chain")
        expected.add("recovery_forward")
    _require(set(policy) == expected and
             policy["schema"] == SCHEMA and
             policy["batch_sha256"] == batch["batch_sha256"] and
             type(policy["max_requests"]) is int and
             1 <= policy["max_requests"] <= 84 and
             policy["price_snapshot"] == PRICE_SNAPSHOT and
             batch["model"]["model_id"] == PRICE_SNAPSHOT["model_id"],
             "spend_policy_invalid")
    _require(0 < _parse_usd(policy["max_usd"], code="spend_policy_invalid") <= 3 * _MICRO,
             "spend_policy_invalid")
    if "carry_forward" in policy:
        carry = policy["carry_forward"]
        _require(type(carry) is dict and set(carry) == {
            "control_root", "batch_sha256", "request_count", "spent_usd",
            "evidence_sha256"}, "spend_predecessor_invalid")
        actual = _derive_carry(root, batch, carry["control_root"], carry["batch_sha256"])
        _require(actual == carry, "spend_predecessor_drift")
        _check_successor(root, batch, actual)
    _require(("recovery_forward" in policy) == ("recovery_plan" in batch),
             "spend_recovery_missing")
    if "recovery_forward" in policy:
        from macr.evals.deepseek_policy_recovery import RecoveryError, check_recovery
        forward = policy["recovery_forward"]
        _require(type(forward) is dict and set(forward) == {"plan", "confirmation"} and
                 policy["max_requests"] == 84 and policy["max_usd"] == "3.000000",
                 "spend_recovery_invalid")
        try:
            check_recovery(root, batch, forward["plan"], forward["confirmation"])
        except RecoveryError as error:
            raise PolicySpendError(error.code) from None
    return policy


def _records(root: Path, batch: dict, policy: dict) -> tuple[dict, dict]:
    try:
        names = _scan_names(root / "spend" / "entries", _MAX_RECORDS)
    except PolicyBatchError:
        raise PolicySpendError("spend_entries_invalid") from None
    _require(len(names) <= _MAX_RECORDS and
             all(_ENTRY.fullmatch(name) for name in names),
             "spend_entries_invalid")
    cells = {cell["cell_id"]: cell for cell in batch["cells"]}
    reservations, outcomes = {}, {}
    for name in sorted(names):
        match = _ENTRY.fullmatch(name)
        cell_id, decision, kind = match.group(1), int(match.group(2)), match.group(3)
        key = (cell_id, decision)
        _require(cell_id in cells and
                 decision <= ARM_REQUESTS[cells[cell_id]["arm"]],
                 "spend_entries_invalid")
        try:
            row = _read_json(root, f"spend/entries/{name}", _MAX_RECORD,
                             "spend_entries_invalid")
        except PolicyBatchError:
            raise PolicySpendError("spend_entries_invalid") from None
        _require(row.get("schema") == SCHEMA + "." + kind and
                 row.get("batch_sha256") == batch["batch_sha256"] and
                 row.get("cell_id") == cell_id and
                 type(row.get("decision")) is int and
                 row["decision"] == decision,
                 "spend_entries_invalid")
        if kind == "reservation":
            _require(set(row) == {"schema", "batch_sha256", "cell_id",
                                  "decision", "state", "max_output_tokens",
                                  "reserved_usd"} and
                     row["state"] == "reserved_unknown_outcome" and
                     type(row["max_output_tokens"]) is int and
                     1 <= row["max_output_tokens"] <= 6000 and
                     row["reserved_usd"] == "0.330000",
                     "spend_entries_invalid")
            reservations[key] = row
        else:
            _require(set(row) == {"schema", "batch_sha256", "cell_id",
                                  "decision", "state", "charged_usd", "usage"} and
                     type(row["state"]) is str and
                     row["state"] in ("settled", "unknown", "usage_unknown"),
                     "spend_entries_invalid")
            outcomes[key] = row
    _require(set(outcomes) <= set(reservations) and
             len(reservations) <= policy["max_requests"],
             "spend_entries_invalid")
    for key, row in outcomes.items():
        charged = _parse_usd(row["charged_usd"], code="spend_entries_invalid")
        if row["state"] == "settled":
            computed = _normalized_charge(row["usage"],
                                          reservations[key]["max_output_tokens"])
            _require(computed is not None and computed == charged and
                     charged <= _RESERVATION, "spend_entries_invalid")
        else:
            _require(charged == 0 and row["usage"] is None,
                     "spend_entries_invalid")
    for cell_id, decision in reservations:
        _require(all((cell_id, n) in reservations and
                     (cell_id, n) in outcomes for n in range(1, decision)),
                 "spend_entries_invalid")
    return reservations, outcomes


def _successor_record(root: Path, batch: dict, carry: dict) -> dict:
    return {"schema": SCHEMA + ".successor", "predecessor_batch_sha256":
            carry["batch_sha256"], "predecessor_control_root": carry["control_root"],
            "successor_control_root": str(root), "successor_batch_sha256":
            batch["batch_sha256"]}


def _successor_path(carry: dict) -> Path:
    _, previous = _batch(carry["control_root"], carry["batch_sha256"])
    _require(type(previous.get("output_root")) is str,
             "spend_predecessor_layout_invalid")
    return Path(previous["output_root"]).parent.parent / "cumulative-successor-lock.json"


def _check_successor(root: Path, batch: dict, carry: dict) -> None:
    try:
        path = _successor_path(carry)
        stored = _read_json(path.parent, path.name, _MAX_RECORD,
            "spend_successor_invalid")
    except PolicyBatchError:
        raise PolicySpendError("spend_successor_invalid") from None
    _require(stored == _successor_record(root, batch, carry), "spend_successor_invalid")


def _derive_carry(root: Path, batch: dict, predecessor_control_root,
                  predecessor_batch_sha256) -> dict:
    """Recheck the stopped predecessor's exact request and usage evidence.

    Only one predecessor is supported. No manifest loading or source execution
    is needed, so a v1 batch can be audited by the v2 ledger without reinterpretation.
    """
    _require(type(predecessor_control_root) in (str, Path) or
             isinstance(predecessor_control_root, Path), "spend_predecessor_invalid")
    old_root, old_batch = _batch(predecessor_control_root, predecessor_batch_sha256)
    _require(type(old_batch.get("output_root")) is str and
             old_root == Path(old_batch["output_root"]).parent / "control",
             "spend_predecessor_layout_invalid")
    _require(old_root != root and not old_root.is_relative_to(root) and
             not root.is_relative_to(old_root) and
             old_batch["batch_sha256"] != batch["batch_sha256"],
             "spend_predecessor_invalid")
    try:
        with _locked(old_root):
            policy = _policy(old_root, old_batch, allow_carry=False)
            reservations, outcomes = _records(old_root, old_batch, policy)
            claims, closed = _registry(old_root, old_batch)
            _require(claims and set(claims) == set(closed) and
                     any(row["state"].startswith("batch_halted") for row in closed.values()),
                     "spend_predecessor_not_stopped")
            expected = {(cell_id, decision) for cell_id, row in closed.items()
                        for decision in range(1, row["request_count"] + 1)}
            _require(expected and set(reservations) == set(outcomes) == expected and
                     all(row["state"] == "settled" for row in outcomes.values()),
                     "spend_predecessor_unsettled")
            proof = {}

            def bind(directory, relative, limit):
                data = _read(directory, relative, limit, "spend_predecessor_invalid")
                proof[str(directory / relative)] = sha256(data).hexdigest()

            bind(old_root, "batch.json", 256 * 1024)
            bind(old_root, "spend/policy.json", _MAX_RECORD)
            for name in _scan_names(old_root / "spend" / "entries", _MAX_RECORDS):
                bind(old_root, "spend/entries/" + name, _MAX_RECORD)
            for name in _scan_names(old_root / "registry", 288):
                bind(old_root, "registry/" + name, _MAX_RECORD)
            for cell_id, ticket in claims.items():
                output = Path(ticket["out"])
                count = closed[cell_id]["request_count"]
                names = {"claim.json", "result.json"}
                for decision in range(1, count + 1):
                    names.update(f"decision-{decision:02d}.{kind}.json" for kind in
                                 ("reservation", "response", "outcome"))
                    response = _read_json(output, f"decision-{decision:02d}.response.json",
                        old_batch["budget"]["max_record_bytes"], "spend_predecessor_invalid")
                    evidence = _usage_evidence(response.get("raw_usage"),
                        reservations[(cell_id, decision)]["max_output_tokens"])
                    settled = outcomes[(cell_id, decision)]
                    _require(response.get("state") == "received" and evidence is not None and
                             evidence[1] == settled["usage"] and
                             _usd(evidence[0]) == settled["charged_usd"],
                             "spend_predecessor_usage_mismatch")
                for name in names:
                    bind(output, name, old_batch["budget"]["max_record_bytes"])
            spent = sum(_parse_usd(row["charged_usd"], code="spend_predecessor_invalid")
                        for row in outcomes.values())
            _require(len(expected) <= 84 and spent <= 3 * _MICRO,
                     "spend_predecessor_invalid")
            return {"control_root": str(old_root), "batch_sha256": old_batch["batch_sha256"],
                    "request_count": len(expected), "spent_usd": _usd(spent),
                    "evidence_sha256": _digest(proof)}
    except PolicyBatchError:
        raise PolicySpendError("spend_predecessor_invalid") from None


def _summary(batch: dict, policy: dict, reservations: dict, outcomes: dict) -> dict:
    batch_spent = sum(_parse_usd(row["charged_usd"], code="spend_entries_invalid")
                for row in outcomes.values() if row["state"] == "settled")
    carry = policy.get("carry_forward", {})
    recovery = policy.get("recovery_forward")
    retained_unknown = 0
    if recovery is not None:
        plan = recovery["plan"]
        carry = {"request_count": plan["opening"]["request_count"],
                 "spent_usd": plan["opening"]["spent_usd"],
                 "batch_sha256": plan["predecessor_batch_sha256"]}
        retained_unknown = _parse_usd(plan["opening"]["retained_unknown_usd"],
                                     code="spend_recovery_invalid")
    carried_count = carry.get("request_count", 0)
    carried_spent = _parse_usd(carry.get("spent_usd", "0.000000"),
                             code="spend_predecessor_invalid")
    spent = batch_spent + carried_spent
    count = carried_count + len(reservations)
    held = retained_unknown + _RESERVATION * sum(key not in outcomes or
                              outcomes[key]["state"] != "settled"
                              for key in reservations)
    unknown = any(row["state"] != "settled" for row in outcomes.values())
    pending = len(reservations) > len(outcomes)
    _require(count <= policy["max_requests"] and
             spent + held <= _parse_usd(policy["max_usd"],
                                        code="spend_entries_invalid"),
             "spend_entries_invalid")
    return {"schema": SCHEMA + ".state", "batch_sha256": batch["batch_sha256"],
            "state": "halted_unknown" if unknown else
                     "awaiting_outcome" if pending else "open",
            "max_usd": policy["max_usd"], "max_requests": policy["max_requests"],
            "request_count": count, "spent_usd": _usd(spent),
            "batch_request_count": len(reservations), "batch_spent_usd": _usd(batch_spent),
            "carried_request_count": carried_count, "carried_spent_usd": _usd(carried_spent),
            "carried_batch_sha256": carry.get("batch_sha256"),
            "reserved_usd": _usd(held), "retained_unknown_usd": _usd(retained_unknown),
            "recovery_plan_sha256": recovery["plan"]["plan_sha256"] if recovery else None,
            "price_snapshot": policy["price_snapshot"]}


def initialize_spend_ledger(control_root, *, batch_sha256, max_usd="3.00",
                            max_requests=84, price_snapshot=PRICE_SNAPSHOT,
                            predecessor_control_root=None, predecessor_batch_sha256=None,
                            recovery_plan=None, recovery_confirmation=None):
    """Freeze approval price and local limits in a new control-plane directory."""
    root, batch = _batch(control_root, batch_sha256)
    _require(type(price_snapshot) is dict and price_snapshot == PRICE_SNAPSHOT,
             "spend_price_snapshot_invalid")
    _require(batch["model"]["model_id"] == PRICE_SNAPSHOT["model_id"],
             "spend_model_mismatch")
    _require(type(max_requests) is int and 1 <= max_requests <= 84,
             "spend_request_ceiling_invalid")
    amount = _parse_usd(max_usd, code="spend_budget_invalid")
    _require(0 < amount <= 3 * _MICRO, "spend_budget_invalid")
    policy = {"schema": SCHEMA, "batch_sha256": batch_sha256,
              "max_usd": _usd(amount), "max_requests": max_requests,
              "price_snapshot": dict(PRICE_SNAPSHOT)}
    _require((predecessor_control_root is None) == (predecessor_batch_sha256 is None),
             "spend_predecessor_invalid")
    _require(("recovery_plan" in batch) == (recovery_plan is not None) and
             (recovery_plan is None) == (recovery_confirmation is None),
             "spend_recovery_missing")
    if recovery_plan is not None:
        from macr.evals.deepseek_policy_recovery import (RecoveryError,
            validate_recovery_plan, validate_recovery_confirmation)
        _require(predecessor_control_root is None and max_requests == 84 and
                 amount == 3 * _MICRO, "spend_recovery_invalid")
        try:
            plan = validate_recovery_plan(recovery_plan)
            confirmation = validate_recovery_confirmation(plan, recovery_confirmation,
                batch_sha256=batch_sha256)
        except RecoveryError as error:
            raise PolicySpendError(error.code) from None
        policy["recovery_forward"] = {"plan": plan, "confirmation": confirmation}
    if predecessor_control_root is not None:
        carry = _derive_carry(root, batch, predecessor_control_root,
                              predecessor_batch_sha256)
        _require(carry["request_count"] <= max_requests and
                 _parse_usd(carry["spent_usd"], code="spend_predecessor_invalid") <= amount,
                 "spend_predecessor_exceeds_budget")
        policy["carry_forward"] = carry
    try:
        with _locked(root), open_directory(root) as root_fd:
            _require(not (root / "spend").exists() and
                     not (root / "spend").is_symlink(), "spend_already_initialized")
            if "carry_forward" in policy:
                carry = policy["carry_forward"]
                _write_new(_successor_path(carry), _successor_record(root, batch, carry),
                    _MAX_RECORD)
            if "recovery_forward" in policy:
                from macr.evals.deepseek_policy_recovery import RecoveryError, claim_recovery
                forward = policy["recovery_forward"]
                try:
                    claim_recovery(root, batch, forward["plan"], forward["confirmation"])
                except RecoveryError as error:
                    raise PolicySpendError(error.code) from None
            os.mkdir("spend", 0o700, dir_fd=root_fd)
            os.fsync(root_fd)
            with open_directory(root / "spend") as spend_fd:
                os.mkdir("entries", 0o700, dir_fd=spend_fd)
                os.fsync(spend_fd)
            _write_new(root / "spend" / "policy.json", policy, _MAX_POLICY)
    except (OSError, ContractError, PolicyBatchError):
        raise PolicySpendError("spend_initialize_invalid") from None
    return policy


def spend_state(control_root) -> dict:
    """Read a bounded, validated ledger snapshot without changing it."""
    root, batch = _batch(control_root)
    try:
        with _locked(root):
            policy = _policy(root, batch)
            reservations, outcomes = _records(root, batch, policy)
            return _summary(batch, policy, reservations, outcomes)
    except PolicyBatchError:
        raise PolicySpendError("spend_entries_invalid") from None


def reserve_spend(control_root, *, batch_sha256, cell_id, decision,
                  max_output_tokens) -> dict:
    """Persist a full conservative reservation before any provider dispatch."""
    root, batch = _batch(control_root, batch_sha256)
    _require(type(cell_id) is str and _CELL.fullmatch(cell_id) is not None and
             type(decision) is int and 1 <= decision <= 3,
             "spend_request_invalid")
    _require(type(max_output_tokens) is int and
             1 <= max_output_tokens <= PRICE_SNAPSHOT["max_output_tokens_per_request"],
             "spend_output_cap")
    with _locked(root):
        policy = _policy(root, batch)
        reservations, outcomes = _records(root, batch, policy)
        cell = next((row for row in batch["cells"] if row["cell_id"] == cell_id), None)
        if cell is None:
            raise PolicySpendError("spend_cell_unclaimed")
        _require(decision <= ARM_REQUESTS[cell["arm"]], "spend_decision_invalid")
        try:
            claim = _read_json(root, f"registry/{cell_id}.claim.json", 4096,
                               "spend_cell_unclaimed")
        except PolicyBatchError:
            raise PolicySpendError("spend_cell_unclaimed") from None
        _require(claim == _ticket(batch, cell), "spend_cell_unclaimed")
        key = (cell_id, decision)
        _require(key not in reservations, "spend_duplicate_request")
        state = _summary(batch, policy, reservations, outcomes)
        _require(state["state"] != "halted_unknown", "spend_halted_unknown")
        _require(state["state"] != "awaiting_outcome", "spend_request_pending")
        _require(all((cell_id, previous) in outcomes and
                     outcomes[(cell_id, previous)]["state"] == "settled"
                     for previous in range(1, decision)), "spend_decision_order")
        _require(state["request_count"] < policy["max_requests"],
                 "spend_request_ceiling")
        total = _parse_usd(state["spent_usd"], code="spend_entries_invalid") +\
                _parse_usd(state["reserved_usd"], code="spend_entries_invalid")
        _require(total + _RESERVATION <= _parse_usd(policy["max_usd"],
                                                     code="spend_policy_invalid"),
                 "spend_budget_exhausted")
        row = {"schema": SCHEMA + ".reservation", "batch_sha256": batch_sha256,
               "cell_id": cell_id, "decision": decision,
               "state": "reserved_unknown_outcome",
               "max_output_tokens": max_output_tokens,
               "reserved_usd": _usd(_RESERVATION)}
        try:
            _write_new(root / "spend" / "entries" /
                       f"{cell_id}-{decision:02d}.reservation.json", row,
                       _MAX_RECORD)
        except PolicyBatchError:
            raise PolicySpendError("spend_reservation_write_invalid") from None
        return row


def _normalized_charge(usage: object, max_output_tokens: int) -> int | None:
    if type(usage) is not dict or set(usage) != {
            "prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens", "cache_basis", "reasoning_tokens"}:
        return None
    prompt, output = usage["prompt_tokens"], usage["completion_tokens"]
    hit, miss = usage["prompt_cache_hit_tokens"], usage["prompt_cache_miss_tokens"]
    reasoning = usage["reasoning_tokens"]
    if (any(type(value) is not int for value in (prompt, output, hit, miss)) or
            prompt <= 0 or output <= 0 or hit < 0 or miss < 0 or
            prompt > PRICE_SNAPSHOT["context_limit_tokens"] or
            output > max_output_tokens or hit + miss != prompt or
            type(usage["cache_basis"]) is not str or
            usage["cache_basis"] not in ("reported", "all_miss") or
            (usage["cache_basis"] == "all_miss" and (hit != 0 or miss != prompt)) or
            (reasoning is not None and (type(reasoning) is not int or
                                        reasoning < 0 or reasoning > output))):
        return None
    # Ceiling to one micro-USD; all rates are exact integer micro-USD / 1M.
    numerator = miss * 300_000 + hit * 6_000 + output * 1_200_000
    return (numerator + _MICRO - 1) // _MICRO


def _usage_evidence(raw_usage: object, max_output_tokens: int) -> tuple[int, dict] | None:
    """Keep only billable counters; missing split is charged all cache-miss."""
    if type(raw_usage) is not dict:
        return None
    prompt = raw_usage.get("prompt_tokens")
    output = raw_usage.get("completion_tokens")
    if (type(prompt) is not int or type(output) is not int or prompt <= 0 or
            output <= 0 or prompt > PRICE_SNAPSHOT["context_limit_tokens"] or
            output > max_output_tokens):
        return None
    total = raw_usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != prompt + output):
        return None
    details = raw_usage.get("completion_tokens_details")
    reasoning = None
    if details is not None:
        if type(details) is not dict:
            return None
        reasoning = details.get("reasoning_tokens")
        if reasoning is not None and (type(reasoning) is not int or
                                      reasoning < 0 or reasoning > output):
            return None
    hit = raw_usage.get("prompt_cache_hit_tokens")
    miss = raw_usage.get("prompt_cache_miss_tokens")
    if hit is None or miss is None:
        if any(value is not None and (type(value) is not int or value < 0 or
                                     value > prompt) for value in (hit, miss)):
            return None
        hit, miss, basis = 0, prompt, "all_miss"
    elif (type(hit) is not int or type(miss) is not int or hit < 0 or
          miss < 0 or hit + miss != prompt):
        return None
    else:
        basis = "reported"
    normalized = {"prompt_tokens": prompt, "completion_tokens": output,
                  "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": miss,
                  "cache_basis": basis, "reasoning_tokens": reasoning}
    cost = _normalized_charge(normalized, max_output_tokens)
    return None if cost is None else (cost, normalized)


def _finish(control_root, *, batch_sha256, cell_id, decision,
            raw_usage=None, unknown=False) -> dict:
    root, batch = _batch(control_root, batch_sha256)
    _require(type(cell_id) is str and _CELL.fullmatch(cell_id) is not None and
             type(decision) is int and 1 <= decision <= 3,
             "spend_request_invalid")
    with _locked(root):
        policy = _policy(root, batch)
        reservations, outcomes = _records(root, batch, policy)
        key = (cell_id, decision)
        _require(key in reservations, "spend_reservation_missing")
        _require(key not in outcomes, "spend_outcome_exists")
        _require(not any(row["state"] != "settled" for row in outcomes.values()),
                 "spend_halted_unknown")
        evidence = None if unknown else _usage_evidence(
            raw_usage, reservations[key]["max_output_tokens"])
        state = "unknown" if unknown else "usage_unknown" if evidence is None or\
                evidence[0] > _RESERVATION else "settled"
        row = {"schema": SCHEMA + ".outcome", "batch_sha256": batch_sha256,
               "cell_id": cell_id, "decision": decision, "state": state,
               "charged_usd": _usd(evidence[0] if state == "settled" else 0),
               "usage": evidence[1] if state == "settled" else None}
        try:
            _write_new(root / "spend" / "entries" /
                       f"{cell_id}-{decision:02d}.outcome.json", row,
                       _MAX_RECORD)
        except PolicyBatchError:
            raise PolicySpendError("spend_outcome_write_invalid") from None
        return row


def settle_spend(control_root, *, batch_sha256, cell_id, decision,
                 raw_usage) -> dict:
    """Settle trusted usage; missing/inconsistent usage holds reserve and halts."""
    return _finish(control_root, batch_sha256=batch_sha256, cell_id=cell_id,
                   decision=decision, raw_usage=raw_usage)


def mark_spend_unknown(control_root, *, batch_sha256, cell_id, decision) -> dict:
    """Freeze the whole batch after a request whose remote outcome is unknown."""
    return _finish(control_root, batch_sha256=batch_sha256, cell_id=cell_id,
                   decision=decision, unknown=True)
