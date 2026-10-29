"""One explicitly confirmed retained-unknown continuation of a stopped pilot.

The source remains immutable and halted. Its unknown reservations occupy the
same authorization budget permanently; they are never treated as a bill.
This control-only module reads durable receipts, never credentials or network.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re

from macr.evals.deepseek_policy_batch import (PolicyBatchError, _digest, _load_batch,
    _locked, _read, _read_json, _registry, _scan_names, _write_new)
from macr.evals.deepseek_policy_runner import ARM_REQUESTS
from macr.evals.deepseek_policy_manifest import load_policy_manifest
from macr.investigation.scope import validate_workspace_path
from macr.investigation.validation import ContractError


SCHEMA = "macr.deepseek.policy.recovery.v1"
CONFIRMATION_SCHEMA = "macr.deepseek.policy.recovery.confirmation.v1"
ACTION = "retain_unknown_and_continue_unattempted"
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_RECOVERY_BYTES = 64 * 1024


class RecoveryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RecoveryError(code)


def _identity(cell: dict) -> tuple:
    return cell["task_id"], cell["repeat"], cell["arm"]


def _logical(cell: dict) -> dict:
    return {key: cell[key] for key in ("task_id", "repeat", "arm")}


def _path(value) -> Path:
    try:
        path = Path(value).absolute()
        validate_workspace_path(path, output=True)
    except (TypeError, ValueError, OSError, ContractError):
        raise RecoveryError("recovery_layout_invalid") from None
    return path


def _source(predecessor_control_root, predecessor_batch_sha256):
    # Local imports keep the ordinary spend path independent of recovery.
    from macr.evals.deepseek_policy_spend import (PolicySpendError, _parse_usd,
        _policy, _records, _summary, _usage_evidence, _usd)

    try:
        root, batch = _load_batch(predecessor_control_root)
        _require(batch["batch_sha256"] == predecessor_batch_sha256,
                 "recovery_source_mismatch")
        outputs = _path(batch["output_root"])
        _require(root == outputs.parent / "control", "recovery_layout_invalid")
        _require("recovery_plan" not in batch and len(batch["manifests"]) == 12 and
                 len(batch["cells"]) == 48 and all(cell["repeat"] == 1 for cell in batch["cells"]),
                 "recovery_source_invalid")
        with _locked(root):
            policy = _policy(root, batch)
            _require("recovery_forward" not in policy and policy["max_requests"] == 84 and
                     policy["max_usd"] == "3.000000", "recovery_source_invalid")
            reservations, outcomes = _records(root, batch, policy)
            claims, closed = _registry(root, batch)
            state = _summary(batch, policy, reservations, outcomes)
            _require(state["state"] == "halted_unknown" and claims and
                     set(claims) == set(closed) and
                     any(row["state"] == "batch_halted_unknown" for row in closed.values()),
                     "recovery_source_not_stopped")
            expected = {(cell_id, n) for cell_id, row in closed.items()
                        for n in range(1, row["request_count"] + 1)}
            _require(expected and set(reservations) == set(outcomes) == expected,
                     "recovery_source_pending")
            proof = {}

            def bind(directory, relative, limit):
                data = _read(directory, relative, limit, "recovery_source_invalid")
                proof[str(directory / relative)] = sha256(data).hexdigest()

            bind(root, "batch.json", 256 * 1024)
            bind(root, "spend/policy.json", MAX_RECOVERY_BYTES)
            for row in batch["manifests"]:
                manifest = load_policy_manifest(row["manifest_dir"],
                                                dataset_root=batch["dataset_root"])
                _require(manifest["manifest_sha256"] == row["manifest_sha256"] and
                         manifest["snapshot"]["sha256"] == row["snapshot_sha256"],
                         "recovery_source_manifest_drift")
                bind(Path(row["manifest_dir"]), "manifest.json", 128 * 1024)
            for name in _scan_names(root / "spend" / "entries", 168):
                bind(root, "spend/entries/" + name, 4096)
            for name in _scan_names(root / "registry", 288):
                bind(root, "registry/" + name, 4096)
            for cell_id, ticket in claims.items():
                output = Path(ticket["out"])
                names = {"claim.json", "result.json"}
                for decision in range(1, closed[cell_id]["request_count"] + 1):
                    names.update(f"decision-{decision:02d}.{kind}.json" for kind in
                                 ("reservation", "response", "outcome"))
                    settled = outcomes[(cell_id, decision)]
                    if settled["state"] == "settled":
                        response = _read_json(output, f"decision-{decision:02d}.response.json",
                            batch["budget"]["max_record_bytes"], "recovery_source_invalid")
                        usage = _usage_evidence(response.get("raw_usage"),
                            reservations[(cell_id, decision)]["max_output_tokens"])
                        _require(response.get("state") == "received" and usage is not None and
                                 usage[1] == settled["usage"] and
                                 _usd(usage[0]) == settled["charged_usd"],
                                 "recovery_source_usage_mismatch")
                for name in names:
                    bind(output, name, batch["budget"]["max_record_bytes"])
            attempted = {_identity(cell) for cell in batch["cells"]
                         if cell["cell_id"] in claims}
            carry = policy.get("carry_forward")
            if carry is not None:
                # _policy already rederives the entire settled ancestor proof
                # and checks its original exclusive successor lock.
                old_root, old_batch = _load_batch(carry["control_root"])
                with _locked(old_root):
                    old_claims, _ = _registry(old_root, old_batch)
                    attempted.update(_identity(cell) for cell in old_batch["cells"]
                                     if cell["cell_id"] in old_claims)
                proof["settled_ancestor_evidence"] = carry["evidence_sha256"]
            eligible = [_logical(cell) for cell in batch["cells"]
                        if _identity(cell) not in attempted]
            excluded = [{"task_id": key[0], "repeat": key[1], "arm": key[2]}
                        for key in sorted(attempted)]
            _require(eligible and len({_identity(cell) for cell in batch["cells"]}) == 48,
                     "recovery_source_invalid")
            opening = {"request_count": state["request_count"],
                       "spent_usd": state["spent_usd"],
                       "retained_unknown_usd": state["reserved_usd"]}
            _require(0 < _parse_usd(opening["retained_unknown_usd"],
                                   code="recovery_source_invalid") <= 3_000_000,
                     "recovery_source_invalid")
            return outputs.parent.parent, batch, {
                "opening": opening, "eligible_cells": eligible, "excluded_cells": excluded,
                "max_new_requests": sum(ARM_REQUESTS[cell["arm"]] for cell in eligible),
                "source_model": batch["model"], "source_prompt_version": batch["prompt_version"],
                "source_manifests": [{key: row[key] for key in
                    ("task_id", "repeat", "snapshot_sha256")} for row in batch["manifests"]],
                "source_gold_sha256": batch["gold_sha256"],
                "source_rubric_sha256": batch["rubric_sha256"],
                "source_predecessor_control_root": carry["control_root"] if carry else None,
                "evidence_sha256": _digest(proof)}
    except (PolicyBatchError, PolicySpendError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, RecoveryError):
            raise
        raise RecoveryError("recovery_source_invalid") from None


def build_recovery_plan(predecessor_control_root, *, predecessor_batch_sha256,
                        target_control_root, target_output_root, recovery_version):
    """Read-only preview; does not create a confirmation, lock or run directory."""
    _require(type(predecessor_batch_sha256) is str and
             _SHA.fullmatch(predecessor_batch_sha256) is not None,
             "recovery_source_mismatch")
    _require(type(recovery_version) is str and _VERSION.fullmatch(recovery_version) is not None,
             "recovery_version_invalid")
    source = _path(predecessor_control_root)
    authorization_root, batch, proof = _source(source, predecessor_batch_sha256)
    control, outputs = _path(target_control_root), _path(target_output_root)
    _require(control.name == "control" and outputs.name != "control" and
             control.parent == outputs.parent and control.parent.parent == authorization_root and
             control.parent != source.parent and control != outputs and
             not outputs.is_relative_to(control) and not control.is_relative_to(outputs),
             "recovery_layout_invalid")
    ancestor = proof["source_predecessor_control_root"]
    _require(ancestor is None or control.parent != Path(ancestor).parent,
             "recovery_layout_invalid")
    plan = {"schema": SCHEMA, "predecessor_control_root": str(source),
            "predecessor_batch_sha256": predecessor_batch_sha256,
            "target_control_root": str(control), "target_output_root": str(outputs),
            "recovery_version": recovery_version, **proof}
    plan["plan_sha256"] = _digest(plan)
    return plan


def validate_recovery_plan(plan):
    """Recompute source evidence and the exact permitted logical subset."""
    _require(type(plan) is dict, "recovery_plan_invalid")
    try:
        actual = build_recovery_plan(plan["predecessor_control_root"],
            predecessor_batch_sha256=plan["predecessor_batch_sha256"],
            target_control_root=plan["target_control_root"],
            target_output_root=plan["target_output_root"], recovery_version=plan["recovery_version"])
    except (KeyError, TypeError):
        raise RecoveryError("recovery_plan_invalid") from None
    _require(plan == actual, "recovery_plan_drift")
    return actual


def validate_recovery_confirmation(plan, confirmation, *, batch_sha256=None):
    """Validate caller-supplied consent evidence; it is not cryptographic consent."""
    _require(type(confirmation) is dict and set(confirmation) == {
        "schema", "granted", "approval_ref", "plan_sha256", "batch_sha256",
        "action", "max_requests", "max_usd"}, "recovery_confirmation_missing")
    _require(confirmation["schema"] == CONFIRMATION_SCHEMA and
             confirmation["granted"] is True and
             type(confirmation["approval_ref"]) is str and
             _VERSION.fullmatch(confirmation["approval_ref"]) is not None and
             confirmation["plan_sha256"] == plan["plan_sha256"] and
             type(confirmation["batch_sha256"]) is str and
             _SHA.fullmatch(confirmation["batch_sha256"]) is not None and
             (batch_sha256 is None or confirmation["batch_sha256"] == batch_sha256) and
             confirmation["action"] == ACTION and type(confirmation["max_requests"]) is int and
             confirmation["max_requests"] == 84 and confirmation["max_usd"] == "3.000000",
             "recovery_confirmation_invalid")
    return dict(confirmation)


def _bound_recovery(root, batch, plan, confirmation):
    actual = validate_recovery_plan(plan)
    validate_recovery_confirmation(actual, confirmation, batch_sha256=batch["batch_sha256"])
    _require(str(root) == actual["target_control_root"] and
             batch.get("output_root") == actual["target_output_root"] and
             batch.get("recovery_plan") == actual and
             len(batch["cells"]) == len(actual["eligible_cells"]) and
             {_identity(cell) for cell in batch["cells"]} ==
             {_identity(cell) for cell in actual["eligible_cells"]}, "recovery_batch_mismatch")
    record = {"schema": SCHEMA + ".lock", "plan_sha256": actual["plan_sha256"],
              "control_root": str(root), "batch_sha256": batch["batch_sha256"],
              "confirmation": confirmation}
    path = Path(actual["predecessor_control_root"]).parent.parent / "cumulative-recovery-lock.json"
    return path, record


def claim_recovery(root, batch, plan, confirmation):
    """Consume the original authorization root's sole recovery slot atomically.

    A crash after this write stays blocked; no automatic reset or replay exists.
    """
    path, record = _bound_recovery(root, batch, plan, confirmation)
    try:
        _write_new(path, record, MAX_RECOVERY_BYTES)
    except PolicyBatchError:
        raise RecoveryError("recovery_already_claimed") from None


def check_recovery(root, batch, plan, confirmation):
    path, record = _bound_recovery(root, batch, plan, confirmation)
    try:
        stored = _read_json(path.parent, path.name, MAX_RECOVERY_BYTES, "recovery_lock_invalid")
    except PolicyBatchError:
        raise RecoveryError("recovery_lock_invalid") from None
    _require(stored == record, "recovery_lock_invalid")
