"""Control-plane freeze and single-concurrency registry for policy cells.

This module owns no transport and is not an OS sandbox. Uniqueness applies to
one fixed control root; generation receives only a neutral cell ticket.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
from random import Random
import re

from macr.evals.deepseek_policy_manifest import (ARMS, PolicyManifestError,
    load_policy_manifest)
from macr.evals.deepseek_policy_runner import (ARM_REQUESTS, BILLING_CONTRACT_VERSION,
    SCHEMA as RUN_SCHEMA)
from macr.investigation.evidence_budget import BudgetExceeded
from macr.investigation.scope import open_directory, read_safe, validate_workspace_path
from macr.investigation.validation import ContractError
from macr.providers.base import validate_billing_evidence

SCHEMA = "macr.deepseek.policy.batch.v1"
CELL_FAILURE_POLICY = "record_known_contract_failure_v1"
_KNOWN_CONTRACT_ERRORS = frozenset({
    "model_response_invalid", "model_output_incomplete", "model_output_invalid",
    "diagnosis_json_invalid", "eval_contract_invalid", "eval_citation_invalid",
    "verification_contract_invalid", "unexpected_model_action", "tool_call_invalid",
    "terminal_action_missing", "duplicate_tool_call", "third_decision_must_terminate",
    "mixed_terminal_action", "ask_user_contract_invalid", "finish_contract_invalid",
    "duplicate_read_tool",
})

CORE_IMPLEMENTATION_FILES = (
    "src/macr/evals/deepseek_policy_batch.py",
    "src/macr/evals/deepseek_policy_batch_entry.py",
    "src/macr/evals/deepseek_policy_live_entry.py",
    "src/macr/evals/deepseek_policy_live_scope.py",
    "src/macr/evals/deepseek_policy_manifest.py",
    "src/macr/evals/deepseek_policy_recovery.py",
    "src/macr/evals/deepseek_policy_runner.py",
    "src/macr/evals/deepseek_policy_spend.py",
    "src/macr/evals/deepseek_policy_tools.py",
    "src/macr/evals/diagnostic_compare.py",
    "src/macr/providers/base.py",
    "src/macr/providers/deepseek.py",
    "src/macr/providers/failure_evidence.py",
    "src/macr/providers/response_normalization.py",
    "src/macr/investigation/scope.py",
    "src/macr/investigation/source_text.py",
    "src/macr/investigation/validation.py",
    "src/macr/investigation/evidence_budget.py",
    "src/macr/investigation/records.py",
    "scripts/run_deepseek_policy_dryrun.py",
    "scripts/run_deepseek_policy_live.py",
)
MAX_CONTROL_FILE_BYTES = 128 * 1024
MAX_BATCH_BYTES = 256 * 1024
MAX_CODE_FILE_BYTES = 256 * 1024
MAX_CELLS = 144
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class PolicyBatchError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PolicyBatchError(code)


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PolicyBatchError("batch_json_invalid") from None


def _digest(value: object) -> str:
    return sha256(_encoded(value)).hexdigest()


def _directory(path: Path) -> Path:
    path = Path(path).absolute()
    try:
        validate_workspace_path(path, output=True)
        with open_directory(path):
            pass
    except (OSError, ContractError):
        raise PolicyBatchError("batch_directory_invalid") from None
    return path


def _read(root: Path, relative: str, limit: int, code: str) -> bytes:
    try:
        return read_safe(root, relative, limit)
    except (OSError, ContractError, BudgetExceeded):
        raise PolicyBatchError(code) from None


def _write_new(path: Path, value: object, limit: int) -> None:
    payload = _encoded(value) + b"\n"
    _require(len(payload) <= limit, "batch_record_limit")
    try:
        validate_workspace_path(path, output=True)
        with open_directory(path.parent) as parent_fd:
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(parent_fd)
    except FileExistsError:
        raise PolicyBatchError("batch_record_exists") from None
    except (OSError, ContractError):
        raise PolicyBatchError("batch_record_write_invalid") from None


def _read_json(root: Path, relative: str, limit: int, code: str) -> dict:
    return _decode_json(_read(root, relative, limit, code), code)


def _decode_json(raw: bytes, code: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, code)
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("nonfinite")

    try:
        value = json.loads(raw.decode("utf-8"),
                           object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise PolicyBatchError(code) from None
    _require(type(value) is dict, code)
    return value


def _scan_names(path: Path, limit: int) -> set[str]:
    names = set()
    try:
        with open_directory(path) as directory_fd, os.scandir(directory_fd) as entries:
            for entry in entries:
                if len(names) >= limit or len(entry.name) > 100 or not entry.is_file(
                        follow_symlinks=False):
                    raise PolicyBatchError("batch_registry_invalid")
                names.add(entry.name)
    except (OSError, ContractError):
        raise PolicyBatchError("batch_registry_invalid") from None
    return names


def _implementation(project_root: Path) -> dict:
    rows = {}
    total = 0
    for relative in CORE_IMPLEMENTATION_FILES:
        data = _read(project_root, relative, MAX_CODE_FILE_BYTES, "batch_code_drift")
        total += len(data)
        _require(total <= 1024 * 1024, "batch_code_limit")
        rows[relative] = sha256(data).hexdigest()
    return rows


def _cell_id(task_id: str, repeat: int, arm: str, prompt_version: str) -> str:
    return _digest({"task_id": task_id, "repeat": repeat, "arm": arm,
                    "prompt_version": prompt_version})[:32]


def _check_recovery_batch(batch: dict, *, control_root=None) -> dict:
    """Recheck the permitted subset without exposing its history to generation."""
    from macr.evals.deepseek_policy_recovery import RecoveryError, validate_recovery_plan
    try:
        plan = validate_recovery_plan(batch.get("recovery_plan"))
    except RecoveryError as error:
        raise PolicyBatchError(error.code) from None
    _require(batch.get("cell_failure_policy") == plan.get("cell_failure_policy"),
             "batch_recovery_policy_mismatch")
    _require(batch["output_root"] == plan["target_output_root"] and
             batch["implementation_version"] == plan["recovery_version"] and
             (control_root is None or str(Path(control_root).absolute()) ==
              plan["target_control_root"]), "batch_recovery_target_mismatch")
    _require(batch["model"] == plan["source_model"] and
             batch["gold_sha256"] == plan["source_gold_sha256"] and
             batch["rubric_sha256"] == plan["source_rubric_sha256"] and
             [{key: row[key] for key in ("task_id", "repeat", "snapshot_sha256")}
              for row in batch["manifests"]] == plan["source_manifests"],
             "batch_recovery_source_mismatch")
    _, previous = _load_batch(plan["predecessor_control_root"])
    old_manifests = {(row["task_id"], row["repeat"]): row for row in previous["manifests"]}
    manifests = {(row["task_id"], row["repeat"]): row for row in batch["manifests"]}
    try:
        for key, row in manifests.items():
            current = load_policy_manifest(row["manifest_dir"], dataset_root=batch["dataset_root"])
            original = load_policy_manifest(old_manifests[key]["manifest_dir"],
                                            dataset_root=previous["dataset_root"])
            _require(original["manifest_sha256"] == old_manifests[key]["manifest_sha256"] and
                     current["manifest_sha256"] == row["manifest_sha256"] and
                     all(current[field] == original[field] for field in
                         ("task", "snapshot", "model", "budget", "repeat", "arms")),
                     "batch_recovery_task_mismatch")
    except (PolicyManifestError, KeyError):
        raise PolicyBatchError("batch_recovery_task_mismatch") from None
    eligible = {(cell["task_id"], cell["repeat"], cell["arm"])
                for cell in plan["eligible_cells"]}
    cells = batch["cells"]
    _require(len(cells) == len(eligible) and
             {(cell["task_id"], cell["repeat"], cell["arm"]) for cell in cells} == eligible,
             "batch_recovery_subset_mismatch")
    for cell in cells:
        expected_id = _digest({"cell_id": _cell_id(cell["task_id"], cell["repeat"],
            cell["arm"], batch["prompt_version"]),
            "recovery_plan_sha256": plan["plan_sha256"]})[:32]
        source = manifests[(cell["task_id"], cell["repeat"])]
        _require(cell["cell_id"] == expected_id and
                 cell["out"] == str(Path(plan["target_output_root"]) / expected_id) and
                 cell["manifest_dir"] == source["manifest_dir"] and
                 cell["manifest_sha256"] == source["manifest_sha256"],
                 "batch_recovery_identity_mismatch")
    return plan


def _ticket(batch: dict, cell: dict) -> dict:
    return {"schema": SCHEMA + ".ticket", "batch_sha256": batch["batch_sha256"],
            "cell_id": cell["cell_id"], "task_id": cell["task_id"],
            "repeat": cell["repeat"], "arm": cell["arm"],
            "manifest_dir": cell["manifest_dir"],
            "manifest_sha256": cell["manifest_sha256"], "out": cell["out"],
            "model_id": batch["model"]["model_id"]}


def prepare_policy_batch(manifest_dirs, *, dataset_root, project_root, control_root,
                         output_root, seed, implementation_version,
                         fingerprint_policy="pin_first", recovery_plan=None,
                         cell_failure_policy=None):
    """Freeze a control-only batch; keep gold/rubric bytes out of cell tickets."""
    _require(type(seed) is int and 0 <= seed < 2**63, "batch_seed_invalid")
    _require(type(implementation_version) is str and
             _VERSION.fullmatch(implementation_version) is not None,
             "batch_implementation_version_invalid")
    _require(fingerprint_policy == "pin_first", "batch_fingerprint_policy_invalid")
    _require(cell_failure_policy is None or type(cell_failure_policy) is str and
             cell_failure_policy == CELL_FAILURE_POLICY, "batch_cell_failure_policy_invalid")
    dataset = _directory(dataset_root)
    project = _directory(project_root)
    outputs = _directory(output_root)
    control = Path(control_root).absolute()
    _require(not control.is_relative_to(dataset) and not control.is_relative_to(project),
             "batch_control_in_snapshot")
    _require(not outputs.is_relative_to(dataset) and not outputs.is_relative_to(project),
             "batch_output_in_snapshot")
    _require(not control.is_relative_to(outputs) and not outputs.is_relative_to(control),
             "batch_control_overlaps_output")
    _directory(control.parent)
    _require(type(manifest_dirs) in (list, tuple) and 1 <= len(manifest_dirs) <= 36,
             "batch_manifest_count_invalid")
    manifests = []
    seen = set()
    model = prompt_version = budget = None
    for manifest_dir in manifest_dirs:
        path = _directory(manifest_dir)
        _require(not path.is_relative_to(dataset) and not path.is_relative_to(project),
                 "batch_manifest_overlaps_dataset")
        _require(not path.is_relative_to(control) and not control.is_relative_to(path),
                 "batch_control_overlaps_manifest")
        try:
            manifest = load_policy_manifest(path, dataset_root=dataset)
        except PolicyManifestError:
            raise PolicyBatchError("batch_manifest_drift") from None
        key = (manifest["task"]["id"], manifest["repeat"])
        _require(key not in seen, "batch_duplicate_manifest")
        seen.add(key)
        if model is None:
            model, prompt_version, budget = (manifest["model"], manifest["prompt_version"],
                                             manifest["budget"])
        _require((manifest["model"], manifest["prompt_version"], manifest["budget"])
                 == (model, prompt_version, budget), "batch_policy_drift")
        manifests.append({"task_id": key[0], "repeat": key[1],
                          "manifest_dir": str(path),
                          "manifest_sha256": manifest["manifest_sha256"],
                          "snapshot_sha256": manifest["snapshot"]["sha256"]})
    _require(not control.exists() and not control.is_symlink(), "batch_control_exists")
    repeats = sorted({item["repeat"] for item in manifests})
    _require(repeats == list(range(1, len(repeats) + 1)) and len(repeats) <= 3,
             "batch_repeat_invalid")
    tasks = sorted(item["task_id"] for item in manifests if item["repeat"] == 1)
    _require(1 <= len(tasks) <= 12 and len(manifests) == len(tasks) * len(repeats)
             and all({item["task_id"] for item in manifests if item["repeat"] == repeat}
                     == set(tasks) for repeat in repeats), "batch_task_grid_invalid")
    by_key = {(item["task_id"], item["repeat"]): item for item in manifests}
    rng = Random(seed)
    cells = []
    for repeat in repeats:
        offset = rng.randrange(len(ARMS))
        positions = {task_id: index for index, task_id in enumerate(tasks)}
        for round_number in range(len(ARMS)):
            shuffled = tasks[:]
            rng.shuffle(shuffled)
            for task_id in shuffled:
                arm = ARMS[(positions[task_id] + round_number + offset) % len(ARMS)]
                source = by_key[(task_id, repeat)]
                cell_id = _cell_id(task_id, repeat, arm, prompt_version)
                cells.append({"cell_id": cell_id, "task_id": task_id,
                              "repeat": repeat, "arm": arm,
                              "manifest_dir": source["manifest_dir"],
                              "manifest_sha256": source["manifest_sha256"],
                              "out": str(outputs / cell_id)})
    if recovery_plan is not None:
        from macr.evals.deepseek_policy_recovery import RecoveryError, validate_recovery_plan
        try:
            recovery_plan = validate_recovery_plan(recovery_plan)
        except RecoveryError as error:
            raise PolicyBatchError(error.code) from None
        eligible = {(row["task_id"], row["repeat"], row["arm"])
                    for row in recovery_plan["eligible_cells"]}
        cells = [row for row in cells if (row["task_id"], row["repeat"], row["arm"]) in eligible]
        for cell in cells:
            cell["cell_id"] = _digest({"cell_id": cell["cell_id"],
                "recovery_plan_sha256": recovery_plan["plan_sha256"]})[:32]
            cell["out"] = str(outputs / cell["cell_id"])
    _require(len(cells) <= MAX_CELLS and len({cell["cell_id"] for cell in cells})
             == len(cells), "batch_cell_grid_invalid")
    code_files = _implementation(project)
    batch = {"schema": SCHEMA, "dataset_root": str(dataset), "project_root": str(project),
             "output_root": str(outputs), "gold_sha256": sha256(_read(
                 dataset, "gold.json", MAX_CONTROL_FILE_BYTES, "batch_gold_invalid")).hexdigest(),
             "rubric_sha256": sha256(_read(dataset, "blind-review-format.json",
                 MAX_CONTROL_FILE_BYTES, "batch_rubric_invalid")).hexdigest(),
             "implementation_version": implementation_version,
             "implementation_files": code_files, "implementation_sha256": _digest(code_files),
             "model": model, "response_model_allowlist": [model["model_id"]],
             "fingerprint_policy": fingerprint_policy, "prompt_version": prompt_version,
             "budget": budget, "seed": seed, "manifests": manifests, "cells": cells}
    if cell_failure_policy is not None:
        batch["cell_failure_policy"] = cell_failure_policy
    if recovery_plan is not None:
        batch["recovery_plan"] = recovery_plan
        _check_recovery_batch(batch, control_root=control)
    batch["batch_sha256"] = _digest(batch)
    _require(len(_encoded(batch)) + 1 <= MAX_BATCH_BYTES, "batch_record_limit")
    try:
        with open_directory(control.parent) as parent_fd:
            os.mkdir(control.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        with open_directory(control) as control_fd:
            os.mkdir("registry", 0o700, dir_fd=control_fd)
            fd = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=control_fd)
            os.close(fd)
            os.fsync(control_fd)
    except (OSError, ContractError):
        raise PolicyBatchError("batch_control_create_invalid") from None
    _write_new(control / "batch.json", batch, MAX_BATCH_BYTES)
    return batch


def claim_policy_cell(control_root, *, task_id, repeat, arm, out):
    """Reserve the next frozen cell before dispatch; never replay a prior claim."""
    root, batch = _load_batch(control_root)
    requested = [cell for cell in batch["cells"] if
                 (cell["task_id"], cell["repeat"], cell["arm"]) ==
                 (task_id, repeat, arm)]
    _require(len(requested) == 1, "batch_cell_unknown")
    cell = requested[0]
    _require(str(Path(out).absolute()) == cell["out"], "batch_cell_output_mismatch")
    with _locked(root):
        claims, outcomes = _registry(root, batch)
        _require(cell["cell_id"] not in claims, "batch_cell_already_claimed")
        _require(not any(row.get("state", "").startswith("batch_halted")
                         for row in outcomes.values()), "batch_halted")
        _require(set(claims) == set(outcomes), "batch_unresolved_claim")
        next_cell = next((row for row in batch["cells"] if row["cell_id"] not in claims),
                         None)
        _require(next_cell is not None and next_cell["cell_id"] == cell["cell_id"],
                 "batch_order_invalid")
        _check_drift(batch)
        _require(not Path(cell["out"]).exists() and not Path(cell["out"]).is_symlink(),
                 "batch_output_exists")
        ticket = _ticket(batch, cell)
        _write_new(root / "registry" / f'{cell["cell_id"]}.claim.json', ticket, 4096)
        return ticket


def record_policy_cell(control_root, ticket):
    """Close a cell from its durable runner artifacts, never caller assertions."""
    root, batch = _load_batch(control_root)
    _require(type(ticket) is dict and ticket.get("batch_sha256") == batch["batch_sha256"]
             and type(ticket.get("cell_id")) is str, "batch_ticket_invalid")
    cell_id = ticket["cell_id"]
    _require(any(_ticket(batch, cell) == ticket for cell in batch["cells"]),
             "batch_ticket_invalid")
    with _locked(root):
        claims, outcomes = _registry(root, batch)
        _require(cell_id in claims and claims[cell_id] == ticket, "batch_ticket_invalid")
        _require(cell_id not in outcomes, "batch_outcome_already_recorded")
        _require(not any(row.get("state", "").startswith("batch_halted")
                         for row in outcomes.values()), "batch_halted")
        _check_drift(batch)
        evidence = _inspect_result(batch, ticket)
        pinned = next((row["fingerprint"] for row in outcomes.values()
                       if row.get("state") in {"completed", "failed_contract"}
                       and row.get("fingerprint") is not None), None)
        state = _state_from_evidence(evidence, pinned,
            cell_failure_policy=batch.get("cell_failure_policy"))
        outcome = {"schema": SCHEMA + ".outcome", "cell_id": cell_id,
                   "status": evidence["status"], "state": state,
                   "model": evidence["model"], "fingerprint": evidence["fingerprint"],
                   "request_count": evidence["request_count"],
                   "result_sha256": evidence["result_sha256"],
                   "response_receipts": evidence["response_receipts"]}
        _write_new(root / "registry" / f"{cell_id}.outcome.json", outcome, 4096)
        return outcome


def _load_batch(control_root: Path) -> tuple[Path, dict]:
    root = _directory(control_root)
    batch = _read_json(root, "batch.json", MAX_BATCH_BYTES, "batch_control_invalid")
    _require(batch.get("schema") == SCHEMA and type(batch.get("batch_sha256")) is str,
             "batch_control_invalid")
    bound = {key: value for key, value in batch.items() if key != "batch_sha256"}
    _require(_digest(bound) == batch["batch_sha256"], "batch_control_drift")
    _require(type(batch.get("cells")) is list and 1 <= len(batch["cells"]) <= MAX_CELLS,
             "batch_control_invalid")
    _require("cell_failure_policy" not in batch or
             type(batch["cell_failure_policy"]) is str and
             batch["cell_failure_policy"] == CELL_FAILURE_POLICY, "batch_control_invalid")
    return root, batch


def _check_drift(batch: dict) -> None:
    if "recovery_plan" in batch:
        _check_recovery_batch(batch)
    dataset = _directory(batch["dataset_root"])
    project = _directory(batch["project_root"])
    _require(sha256(_read(dataset, "gold.json", MAX_CONTROL_FILE_BYTES,
                          "batch_gold_drift")).hexdigest() == batch["gold_sha256"],
             "batch_gold_drift")
    _require(sha256(_read(dataset, "blind-review-format.json", MAX_CONTROL_FILE_BYTES,
                          "batch_rubric_drift")).hexdigest() == batch["rubric_sha256"],
             "batch_rubric_drift")
    code_files = _implementation(project)
    _require(code_files == batch["implementation_files"] and
             _digest(code_files) == batch["implementation_sha256"], "batch_code_drift")
    for row in batch["manifests"]:
        try:
            manifest = load_policy_manifest(row["manifest_dir"], dataset_root=dataset)
        except PolicyManifestError as error:
            raise PolicyBatchError("batch_snapshot_drift" if error.code ==
                                   "policy_snapshot_drift" else "batch_manifest_drift") from None
        _require(manifest["manifest_sha256"] == row["manifest_sha256"] and
                 manifest["snapshot"]["sha256"] == row["snapshot_sha256"],
                 "batch_manifest_drift")


def _request_billing_usage(response: dict, reservation: dict, *, model: str) -> dict | None:
    """Accept only canonical billing evidence bound to this reserved request."""
    _require(type(response) is dict and type(reservation) is dict,
             "batch_billing_evidence_invalid")
    if "billing_evidence" not in response:
        return None
    try:
        billing = validate_billing_evidence(response["billing_evidence"])
        _require(response.get("billing_evidence_rejected") is not True and
                 type(reservation.get("reserved_output_tokens")) is int and
                 type(model) is str and
                 billing["request_sha256"] == reservation.get("request_sha256") and
                 billing["max_output_tokens"] == reservation.get("reserved_output_tokens") and
                 billing["model"] == model, "batch_billing_evidence_invalid")
    except (ValueError, TypeError, KeyError):
        raise PolicyBatchError("batch_billing_evidence_invalid") from None
    return billing["raw_usage"]


def _inspect_result(batch: dict, ticket: dict) -> dict:
    """Derive one cell outcome from the runner's immutable local evidence files."""
    try:
        out = _directory(ticket["out"])
    except PolicyBatchError:
        raise PolicyBatchError("batch_result_unresolved") from None
    limit = batch["budget"]["max_record_bytes"]
    raw_result = _read(out, "result.json", limit, "batch_result_unresolved")
    result = _decode_json(raw_result, "batch_result_unresolved")
    _require(result.get("schema") == RUN_SCHEMA and
             (result.get("task_id"), result.get("repeat"), result.get("arm"),
              result.get("manifest_sha256")) ==
             (ticket["task_id"], ticket["repeat"], ticket["arm"],
              ticket["manifest_sha256"]), "batch_result_unresolved")
    claim = _read_json(out, "claim.json", 4096, "batch_result_unresolved")
    _require(claim == {"schema": RUN_SCHEMA, "manifest_sha256": ticket["manifest_sha256"],
                       "arm": ticket["arm"], "state": "claimed_before_first_dispatch"},
             "batch_result_unresolved")
    status = result.get("status")
    billing_version = result.get("billing_contract_version")
    _require(billing_version in (None, BILLING_CONTRACT_VERSION), "batch_result_unresolved")
    success = status in {"semantic_pending", "ask_user_terminated",
                         "verification_rejected"}
    _require(success or status in {"failed", "remote_outcome_unknown",
                                   "record_limit_exceeded"}, "batch_result_unresolved")
    if success:
        required_review = "not_ready" if status == "verification_rejected" else "pending"
        _require(result.get("review_status") == required_review and
                 result.get("semantic_proof") is False, "batch_result_unresolved")
    requests = result.get("requests")
    count = result.get("requests_attempted")
    _require(type(requests) is list and type(count) is int and count == len(requests)
             and 0 <= count <= ARM_REQUESTS[ticket["arm"]], "batch_result_unresolved")
    if success:
        _require(count >= 1 and (ticket["arm"] != "C" or count == 2)
                 and (ticket["arm"] not in "AB" or count == 1),
                 "batch_result_unresolved")
    expected_names = {"claim.json", "result.json"}
    for decision in range(1, count + 1):
        expected_names.update({f"decision-{decision:02d}.{kind}.json" for kind in
                               ("reservation", "response", "outcome")})
    try:
        names = _scan_names(out, 2 + 3 * ARM_REQUESTS[ticket["arm"]] + 1)
    except PolicyBatchError:
        raise PolicyBatchError("batch_result_unresolved") from None
    _require(names == expected_names, "batch_result_unresolved")
    models, fingerprints, receipt_hashes = [], [], []
    all_completed = True
    all_billing_known = billing_version == BILLING_CONTRACT_VERSION and count > 0
    for decision, entry in enumerate(requests, 1):
        _require(type(entry) is dict and entry.get("decision") == decision and
                 type(entry.get("request")) is dict, "batch_result_unresolved")
        prefix = f"decision-{decision:02d}"
        reservation = _read_json(out, prefix + ".reservation.json", 4096,
                                 "batch_result_unresolved")
        raw_response = _read(out, prefix + ".response.json", limit,
                             "batch_result_unresolved")
        response = _decode_json(raw_response, "batch_result_unresolved")
        outcome = _read_json(out, prefix + ".outcome.json", 4096,
                             "batch_result_unresolved")
        _require(reservation.get("schema") == RUN_SCHEMA and
                 reservation.get("decision") == decision and
                 reservation.get("state") == "reserved_unknown_outcome" and
                 type(reservation.get("request_sha256")) is str and
                 re.fullmatch(r"[0-9a-f]{64}", reservation["request_sha256"]) and
                 reservation["request_sha256"] == entry.get("request_sha256") and
                 type(reservation.get("request_canonical_sha256")) is str and
                 reservation["request_canonical_sha256"] ==
                 entry.get("request_canonical_sha256") == _digest(entry["request"]) and
                 type(reservation.get("request_bytes")) is int and
                 0 < reservation["request_bytes"] <= batch["budget"]["max_request_bytes"],
                 "batch_result_unresolved")
        _require(entry.get("response") == response and
                 outcome.get("schema") == RUN_SCHEMA and
                 outcome.get("decision") == decision and
                 outcome.get("response_sha256") == _digest(response) and
                 type(outcome.get("response_evidence_complete")) is bool,
                 "batch_result_unresolved")
        receipt_hashes.append(sha256(raw_response).hexdigest())
        if billing_version == BILLING_CONTRACT_VERSION:
            _require(type(reservation.get("reserved_output_tokens")) is int and
                     reservation["reserved_output_tokens"] == entry["request"].get("max_tokens") and
                     entry["request"].get("model") == ticket["model_id"],
                     "batch_result_unresolved")
            usage = _request_billing_usage(response, reservation, model=ticket["model_id"])
            if (usage is None and response.get("state") == "received"
                    and response.get("model") == ticket["model_id"]):
                try:
                    usage = validate_billing_evidence({"schema": BILLING_CONTRACT_VERSION,
                        "provider": "deepseek", "model": response.get("model"),
                        "request_sha256": reservation["request_sha256"],
                        "max_output_tokens": reservation["reserved_output_tokens"],
                        "raw_usage": response.get("raw_usage"),
                        "latency_ms": response.get("latency_ms")})["raw_usage"]
                except ValueError:
                    usage = None
            known = (usage is not None and response.get("billing_evidence_rejected") is not True
                     and outcome.get("usage") == {"input_tokens": usage["prompt_tokens"],
                                                 "output_tokens": usage["completion_tokens"]}
                     and outcome.get("state") in {"completed", "failed_known_usage"})
            all_billing_known = all_billing_known and known
        if response.get("state") == "received":
            models.append(response.get("model"))
            fingerprints.append(response.get("system_fingerprint"))
        else:
            all_completed = False
        if (outcome.get("state") != "completed" or entry.get("error_code") is not None
                or outcome["response_evidence_complete"] is not True):
            all_completed = False
    if success:
        _require(all_completed, "batch_result_unresolved")
    identity_bad = any(model != ticket["model_id"] for model in models) or any(
        type(fingerprint) is not str or not fingerprint.strip()
        for fingerprint in fingerprints) or len({fingerprint for fingerprint in fingerprints
                                                 if type(fingerprint) is str}) > 1
    return {"status": status, "error_code": result.get("error_code"),
            "model": models[0] if models else None,
            "fingerprint": fingerprints[0] if fingerprints else None,
            "identity_bad": identity_bad, "all_completed": all_completed,
            "all_billing_known": all_billing_known,
            "all_identity_known": count > 0 and len(models) == count and not identity_bad,
            "request_count": count, "result_sha256": sha256(raw_result).hexdigest(),
            "response_receipts": receipt_hashes}


def _state_from_evidence(evidence: dict, pinned_fingerprint: str | None, *,
                         cell_failure_policy=None) -> str:
    if evidence["identity_bad"] or (pinned_fingerprint is not None and
                                    evidence["fingerprint"] not in (None, pinned_fingerprint)):
        return "batch_halted_model_drift"
    if not evidence["all_completed"] or evidence["status"] not in {
            "semantic_pending", "ask_user_terminated", "verification_rejected"}:
        if evidence.get("all_billing_known"):
            if (cell_failure_policy == CELL_FAILURE_POLICY and evidence["status"] == "failed"
                    and evidence.get("all_identity_known") is True
                    and type(evidence.get("error_code")) is str
                    and evidence["error_code"] in _KNOWN_CONTRACT_ERRORS):
                return "failed_contract"
            return "batch_halted_contract"
        return "batch_halted_unknown"
    return "completed"


@contextmanager
def _locked(root: Path):
    try:
        with open_directory(root) as control_fd:
            fd = os.open("lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=control_fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
    except (OSError, ContractError):
        raise PolicyBatchError("batch_registry_invalid") from None


def _registry(root: Path, batch: dict) -> tuple[dict, dict]:
    """Bound inventory and verify the claim/outcome prefix before advancing."""
    names = _scan_names(root / "registry", 2 * MAX_CELLS)
    expected = {f'{cell["cell_id"]}.{kind}.json' for cell in batch["cells"]
                for kind in ("claim", "outcome")}
    _require(names <= expected, "batch_registry_invalid")
    claims, outcomes = {}, {}
    pinned = None
    for cell in batch["cells"]:
        cell_id = cell["cell_id"]
        if f"{cell_id}.claim.json" in names:
            claims[cell_id] = _read_json(root, f"registry/{cell_id}.claim.json",
                                         4096, "batch_registry_invalid")
            _require(claims[cell_id] == _ticket(batch, cell), "batch_registry_invalid")
        if f"{cell_id}.outcome.json" in names:
            _require(cell_id in claims, "batch_registry_invalid")
            outcome = _read_json(root, f"registry/{cell_id}.outcome.json",
                                 4096, "batch_registry_invalid")
            _require(set(outcome) == {"schema", "cell_id", "status", "state",
                                      "model", "fingerprint", "request_count",
                                      "result_sha256", "response_receipts"} and
                     outcome["schema"] == SCHEMA + ".outcome" and
                     outcome["cell_id"] == cell_id and
                     outcome["state"] in {"completed", "failed_contract", "batch_halted_unknown",
                                          "batch_halted_contract",
                                          "batch_halted_model_drift"} and
                     type(outcome["request_count"]) is int and
                     0 <= outcome["request_count"] <= ARM_REQUESTS[cell["arm"]] and
                     type(outcome["response_receipts"]) is list and
                     len(outcome["response_receipts"]) == outcome["request_count"] and
                     type(outcome["result_sha256"]) is str and
                     re.fullmatch(r"[0-9a-f]{64}", outcome["result_sha256"]),
                     "batch_registry_invalid")
            try:
                evidence = _inspect_result(batch, claims[cell_id])
            except PolicyBatchError:
                raise PolicyBatchError("batch_registry_invalid") from None
            _require((outcome["status"], outcome["state"], outcome["model"],
                      outcome["fingerprint"], outcome["request_count"],
                      outcome["result_sha256"], outcome["response_receipts"]) ==
                     (evidence["status"], _state_from_evidence(evidence, pinned,
                         cell_failure_policy=batch.get("cell_failure_policy")),
                      evidence["model"], evidence["fingerprint"],
                      evidence["request_count"], evidence["result_sha256"],
                      evidence["response_receipts"]), "batch_registry_invalid")
            if outcome["state"] in {"completed", "failed_contract"} and outcome["fingerprint"] is not None:
                pinned = outcome["fingerprint"]
            outcomes[cell_id] = outcome
    ordered = [cell["cell_id"] for cell in batch["cells"]]
    _require([cell_id for cell_id in ordered if cell_id in claims] ==
             ordered[:len(claims)] and
             [cell_id for cell_id in ordered if cell_id in outcomes] ==
             ordered[:len(outcomes)], "batch_registry_invalid")
    return claims, outcomes
