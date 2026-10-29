"""One approved, local DeepSeek policy pilot; prepare/status never use a Key.

The executable path requires an interactive terminal. It never accepts a Key
through arguments, environment variables, or files. All source/output artifacts
remain under the private run directory. No VM, TestRunner or balance API is used.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
import warnings

PROJECT = Path(__file__).resolve().parents[1]
TASK_ROOT = PROJECT.parent
DATASET = PROJECT / "evals" / "deepseek-policy-dev-v1"
PRIVATE_ROOT = TASK_ROOT / "private-pilot-runs"
sys.path.insert(0, str(PROJECT / "src"))

from macr.evals.deepseek_policy_batch import (PolicyBatchError, _check_drift,
    _load_batch, _locked, _read_json, _registry, _write_new,
    prepare_policy_batch)
from macr.evals.deepseek_policy_live_entry import (LiveEntryError,
    PRICE_SNAPSHOT_ID, prepare_approved_policy_batch,
    run_next_approved_policy_cell)
from macr.evals.deepseek_policy_live_scope import (ENDPOINT, MODEL_ID,
    LiveApproval, LiveScopeError, validate_live_scope, write_log_policy)
from macr.evals.deepseek_policy_manifest import (PolicyManifestError,
    prepare_policy_manifest)
from macr.evals.deepseek_policy_recovery import RecoveryError
from macr.evals.deepseek_policy_spend import (PRICE_SNAPSHOT, PolicySpendError,
    spend_state)
from macr.investigation.evidence_budget import BudgetExceeded
from macr.investigation.scope import open_directory, read_safe, validate_workspace_path
from macr.investigation.validation import ContractError


LOCK_FILENAME = "approval-refs-v2-lock.json"
APPROVED_SOURCES = (
    "snapshots/p01/logic.py", "snapshots/p02/entry.py",
    "snapshots/p02/metrics.py", "snapshots/p03/entry.py",
    "snapshots/p03/ring.py", "snapshots/p04/logic.py",
    "snapshots/p05/logic.py", "snapshots/p06/entry.py",
    "snapshots/p06/fields.py", "snapshots/p07/logic.py",
    "snapshots/p08/entry.py", "snapshots/p08/layout.py",
    "snapshots/p09/logic.py", "snapshots/p10/encoding.py",
    "snapshots/p10/entry.py", "snapshots/p11/db_ops.py",
    "snapshots/p11/entry.py", "snapshots/p12/logic.py",
)
MODEL = {"model_id": MODEL_ID, "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}
APPROVAL_SCHEMA = "macr.deepseek.policy.approval.v1"
LOCK_SCHEMA = "macr.deepseek.policy.one-batch.v1"
_RUN_NAME = re.compile(r"pilot-[a-z0-9][a-z0-9-]{0,63}\Z")
_APPROVAL_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RESERVATION = Decimal("0.33")


class LiveCliError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise LiveCliError(code)


def _run_path(run_dir: Path, private_root: Path) -> tuple[Path, Path]:
    root, parent = Path(run_dir).absolute(), Path(private_root).absolute()
    _require(".." not in root.parts and ".." not in parent.parts and
             root.parent == parent and _RUN_NAME.fullmatch(root.name) is not None,
             "live_run_path_invalid")
    try:
        validate_workspace_path(root, output=True)
    except ContractError:
        raise LiveCliError("live_run_path_invalid") from None
    return root, parent


def _private_root(parent: Path, *, create: bool) -> None:
    try:
        if create and not parent.exists() and not parent.is_symlink():
            with open_directory(parent.parent) as parent_fd:
                os.mkdir(parent.name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
        with open_directory(parent) as fd:
            _require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700,
                     "live_private_root_not_private")
    except (OSError, ContractError):
        raise LiveCliError("live_private_root_invalid") from None


def _lock_record(run_dir: Path, approval_ref: str,
                 predecessor_batch_sha256: str | None) -> dict:
    return {"schema": LOCK_SCHEMA, "approval_ref": approval_ref,
            "run_dir": str(run_dir), "generation": "refs-v2",
            "predecessor_batch_sha256": predecessor_batch_sha256}


def _check_one_batch(run_dir: Path, private_root: Path) -> dict:
    if run_dir.name.startswith("pilot-retained-v3-"):
        return _check_recovery_batch(run_dir, private_root)
    try:
        _private_root(private_root, create=False)
        stored = _read_json(private_root, LOCK_FILENAME, 4096,
                            "live_approval_lock_invalid")
    except PolicyBatchError:
        raise LiveCliError("live_approval_lock_invalid") from None
    reference = stored.get("approval_ref")
    predecessor = stored.get("predecessor_batch_sha256")
    _require(type(reference) is str and _APPROVAL_REF.fullmatch(reference) is not None
             and (predecessor is None or type(predecessor) is str and
                  _SHA256.fullmatch(predecessor) is not None),
             "live_approval_lock_invalid")
    _require(stored == _lock_record(run_dir, reference, predecessor),
             "live_approval_lock_mismatch")
    return stored


def _cases(dataset_root: Path) -> list[dict]:
    try:
        catalog = json.loads(read_safe(dataset_root, "cases.json", 64 * 1024))
    except (OSError, ContractError, BudgetExceeded, ValueError, UnicodeError):
        raise LiveCliError("live_dataset_invalid") from None
    _require(type(catalog) is dict and catalog.get("version") ==
             "deepseek-policy-dev-v1" and catalog.get("split") == "development",
             "live_dataset_invalid")
    cases = catalog.get("cases")
    _require(type(cases) is list and len(cases) == 12 and
             {case.get("id") for case in cases if type(case) is dict} ==
             {f"p{number:02d}" for number in range(1, 13)},
             "live_case_grid_invalid")
    try:
        paths = tuple(sorted(case["snapshot_root"] + "/" + name
                             for case in cases for name in case["authorized_files"]))
    except (KeyError, TypeError):
        raise LiveCliError("live_source_scope_invalid") from None
    _require(paths == APPROVED_SOURCES, "live_source_scope_invalid")
    return cases


def _approval_record(batch: dict, run_dir: Path, approval_ref: str,
                     predecessor_batch_sha256: str | None) -> dict:
    record = {"schema": APPROVAL_SCHEMA, "granted": True,
            "approval_ref": approval_ref,
            "batch_sha256": batch["batch_sha256"], "model_id": MODEL_ID,
            "endpoint": ENDPOINT, "max_requests": 84, "max_usd": "3.00",
            "budget_scope": "cumulative_two_batches",
            "predecessor_batch_sha256": predecessor_batch_sha256,
            "price_snapshot_id": PRICE_SNAPSHOT_ID,
            "price_checked_on": PRICE_SNAPSHOT["checked_date"],
            "approved_sources": list(APPROVED_SOURCES),
            "log_root": str(run_dir / "outputs"), "retention_days": 14}
    if "recovery_plan" in batch:
        record["budget_scope"] = "cumulative_retained_unknown"
        record["recovery_plan_sha256"] = batch["recovery_plan"]["plan_sha256"]
    return record


def _approval(record: dict) -> LiveApproval:
    return LiveApproval(granted=True, approval_ref=record["approval_ref"],
        batch_sha256=record["batch_sha256"], model_id=MODEL_ID,
        endpoint=ENDPOINT, max_requests=84, max_usd="3.00",
        price_snapshot_id=PRICE_SNAPSHOT_ID,
        price_checked_on=PRICE_SNAPSHOT["checked_date"],
        approved_sources=APPROVED_SOURCES,
        log_root=Path(record["log_root"]), retention_days=14,
        predecessor_batch_sha256=record["predecessor_batch_sha256"],
        recovery_plan_sha256=record.get("recovery_plan_sha256"))


def _check_recovery_batch(run_dir: Path, private_root: Path) -> dict:
    """The ledger rechecks the original-root, exclusive recovery lock."""
    from macr.evals.deepseek_policy_recovery import validate_recovery_confirmation
    _private_root(private_root, create=False)
    try:
        _, batch = _load_batch(run_dir / "control")
        plan = batch.get("recovery_plan")
        _require(type(plan) is dict and plan.get("target_control_root") == str(run_dir / "control")
                 and plan.get("target_output_root") == str(run_dir / "outputs"),
                 "live_recovery_path_mismatch")
        confirmation = _read_json(run_dir, "recovery-confirmation.json", 4096,
                                  "live_recovery_confirmation_invalid")
        validate_recovery_confirmation(plan, confirmation,
                                        batch_sha256=batch["batch_sha256"])
        money = spend_state(run_dir / "control")
        policy = _read_json(run_dir / "control", "spend/policy.json", 64 * 1024,
                            "live_recovery_spend_mismatch")
        _require(policy.get("recovery_forward", {}).get("confirmation") == confirmation,
                 "live_recovery_confirmation_mismatch")
        _require(money.get("carried_batch_sha256") == plan["predecessor_batch_sha256"],
                 "live_recovery_spend_mismatch")
        return {"approval_ref": confirmation["approval_ref"],
                "predecessor_batch_sha256": plan["predecessor_batch_sha256"]}
    except (PolicyBatchError, PolicySpendError, RecoveryError) as error:
        raise LiveCliError(error.code) from None


def _load_approval(run_dir: Path, batch: dict) -> LiveApproval:
    lock = _check_one_batch(run_dir, run_dir.parent)
    try:
        record = _read_json(run_dir, "approval.json", 4096,
                            "live_approval_file_invalid")
    except PolicyBatchError:
        raise LiveCliError("live_approval_file_invalid") from None
    _require(record == _approval_record(batch, run_dir, lock["approval_ref"],
                                      lock["predecessor_batch_sha256"]),
             "live_approval_file_mismatch")
    return _approval(record)


def prepare_run(run_dir: Path, *, project_root: Path = PROJECT,
                dataset_root: Path = DATASET, private_root: Path = PRIVATE_ROOT,
                approval_ref: str, predecessor_control_root=None,
                predecessor_batch_sha256=None) -> dict:
    """Claim this approved batch exactly once, freeze inputs and local log policy."""
    root, parent = _run_path(run_dir, private_root)
    _require(type(approval_ref) is str and _APPROVAL_REF.fullmatch(approval_ref) is not None,
             "live_approval_ref_invalid")
    _require((predecessor_control_root is None) == (predecessor_batch_sha256 is None)
             and (predecessor_batch_sha256 is None or
                  type(predecessor_batch_sha256) is str and
                  _SHA256.fullmatch(predecessor_batch_sha256) is not None),
             "live_predecessor_scope_invalid")
    project, dataset = Path(project_root).absolute(), Path(dataset_root).absolute()
    _require(not parent.is_relative_to(project) and
             not parent.is_relative_to(dataset), "live_private_root_overlap")
    cases = _cases(dataset)
    _private_root(parent, create=True)
    _require(not root.exists() and not root.is_symlink(), "live_run_exists")
    # This durable claim precedes the new directory. Never remove it on error:
    # an uncertain prepare must not become another approved batch.
    try:
        _write_new(parent / LOCK_FILENAME, _lock_record(root, approval_ref,
                   predecessor_batch_sha256), 4096)
        with open_directory(parent) as parent_fd:
            os.mkdir(root.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        with open_directory(root) as root_fd:
            for name in ("manifests", "outputs"):
                os.mkdir(name, 0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        manifests = []
        for case in cases:
            directory = root / "manifests" / case["id"]
            prepare_policy_manifest(case, dataset_root=dataset,
                                    out=directory, model=MODEL)
            manifests.append(directory)
        batch = prepare_policy_batch(manifests, dataset_root=dataset,
            project_root=project, control_root=root / "control",
            output_root=root / "outputs", seed=41,
            implementation_version="live-policy-refs-v2")
        record = _approval_record(batch, root, approval_ref, predecessor_batch_sha256)
        approval = _approval(record)
        prepare_approved_policy_batch(root / "control", dataset_root=dataset,
            approval=approval, predecessor_control_root=predecessor_control_root,
            predecessor_batch_sha256=predecessor_batch_sha256)
        _write_new(root / "approval.json", record, 4096)
        return batch
    except (OSError, ContractError, PolicyBatchError, PolicyManifestError,
            LiveEntryError) as error:
        raise LiveCliError(getattr(error, "code", "live_prepare_incomplete")) from None


def prepare_recovery_run(run_dir: Path, *, project_root: Path = PROJECT,
                         dataset_root: Path = DATASET, private_root: Path = PRIVATE_ROOT,
                         approval_ref: str, retain_unknown: bool,
                         predecessor_control_root, predecessor_batch_sha256) -> dict:
    """Prepare one explicitly confirmed subset; never alter a halted predecessor."""
    _require(retain_unknown is True and type(approval_ref) is str and
             _APPROVAL_REF.fullmatch(approval_ref) is not None,
             "live_recovery_confirmation_missing")
    _require(type(predecessor_batch_sha256) is str and
             _SHA256.fullmatch(predecessor_batch_sha256) is not None,
             "live_predecessor_scope_invalid")
    root, parent = _run_path(run_dir, private_root)
    _require(root.name.startswith("pilot-retained-v3-"), "live_generation_name_invalid")
    project, dataset = Path(project_root).absolute(), Path(dataset_root).absolute()
    _require(not parent.is_relative_to(project) and not parent.is_relative_to(dataset),
             "live_private_root_overlap")
    from macr.evals.deepseek_policy_recovery import build_recovery_plan
    try:
        plan = build_recovery_plan(predecessor_control_root,
            predecessor_batch_sha256=predecessor_batch_sha256,
            target_control_root=root / "control", target_output_root=root / "outputs",
            recovery_version="live-policy-retained-v3")
        _private_root(parent, create=False)
        _require(not root.exists() and not root.is_symlink(), "live_run_exists")
        cases = _cases(dataset)
        with open_directory(parent) as parent_fd:
            os.mkdir(root.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        with open_directory(root) as root_fd:
            for name in ("manifests", "outputs"):
                os.mkdir(name, 0o700, dir_fd=root_fd)
            os.fsync(root_fd)
        manifests = []
        for case in cases:
            directory = root / "manifests" / case["id"]
            prepare_policy_manifest(case, dataset_root=dataset, out=directory, model=MODEL)
            manifests.append(directory)
        batch = prepare_policy_batch(manifests, dataset_root=dataset,
            project_root=project, control_root=root / "control",
            output_root=root / "outputs", seed=41,
            implementation_version=plan["recovery_version"], recovery_plan=plan)
        confirmation = {"schema": "macr.deepseek.policy.recovery.confirmation.v1",
            "granted": True, "approval_ref": approval_ref,
            "plan_sha256": plan["plan_sha256"], "batch_sha256": batch["batch_sha256"],
            "action": "retain_unknown_and_continue_unattempted",
            "max_requests": 84, "max_usd": "3.000000"}
        record = _approval_record(batch, root, approval_ref, predecessor_batch_sha256)
        _write_new(root / "recovery-confirmation.json", confirmation, 4096)
        prepare_approved_policy_batch(root / "control", dataset_root=dataset,
            approval=_approval(record), recovery_confirmation=confirmation)
        _write_new(root / "approval.json", record, 4096)
        return batch
    except (OSError, ContractError, PolicyBatchError, PolicyManifestError,
            PolicySpendError, LiveEntryError, RecoveryError) as error:
        raise LiveCliError(getattr(error, "code", "live_prepare_incomplete")) from None


def status_run(run_dir: Path, *, private_root: Path | None = None) -> dict:
    """Derive progress from durable claims, results and USD receipts only."""
    root, parent = _run_path(run_dir, private_root or Path(run_dir).absolute().parent)
    _check_one_batch(root, parent)
    try:
        control, batch = _load_batch(root / "control")
        with _locked(control):
            claims, outcomes = _registry(control, batch)
        money = spend_state(control)
        live_attempts = 0
        for cell in batch["cells"]:
            if cell["cell_id"] in outcomes:
                result = _read_json(Path(cell["out"]), "result.json",
                                    batch["budget"]["max_record_bytes"],
                                    "live_result_invalid")
                count = result.get("model_calls_live")
                _require(type(count) is int and 0 <= count <= 3,
                         "live_result_invalid")
                live_attempts += count
    except (PolicyBatchError, PolicySpendError) as error:
        raise LiveCliError(error.code) from None
    closed = len(outcomes)
    halted = sum(item["state"] != "completed" for item in outcomes.values())
    summary = {"schema": "macr.deepseek.policy.live-progress.v1",
        "batch_sha256": batch["batch_sha256"],
        "planned_cells": len(batch["cells"]), "closed_cells": closed,
        "claimed_unresolved_cells": len(claims) - closed,
        "not_attempted_cells": len(batch["cells"]) - len(claims),
        "halted_cells": halted,
        "semantic_pending_cells": sum(item["status"] == "semantic_pending"
                                      for item in outcomes.values()),
        "verification_rejected_cells": sum(item["status"] == "verification_rejected"
                                           for item in outcomes.values()),
        "ask_user_terminated_cells": sum(item["status"] == "ask_user_terminated"
                                          for item in outcomes.values()),
        "confirmed_live_attempts": live_attempts,
        "reserved_request_count": money["request_count"],
        "batch_request_count": money.get("batch_request_count", money["request_count"]),
        "carried_request_count": money.get("carried_request_count", 0),
        "carried_spent_usd": money.get("carried_spent_usd", "0.000000"),
        "batch_spent_usd": money.get("batch_spent_usd", money["spent_usd"]),
        "spent_usd": money["spent_usd"],
        "reserved_usd": money["reserved_usd"],
        "retained_unknown_usd": money.get("retained_unknown_usd", "0.000000"),
        "spend_state": money["state"],
        "batch_complete": closed == len(batch["cells"]) and halted == 0}
    return summary


def execute_prepared(run_dir: Path, *, dataset_root: Path = DATASET,
                     key_reader, cell_limit: int = 48,
                     scripted_by_cell: dict | None = None,
                     private_root: Path | None = None) -> dict:
    """Preflight before a single Key read; no retry or parallel dispatch."""
    root, parent = _run_path(run_dir, private_root or Path(run_dir).absolute().parent)
    _check_one_batch(root, parent)
    _require(type(cell_limit) is int and 1 <= cell_limit <= 48,
             "live_cell_limit_invalid")
    _require(callable(key_reader), "live_key_reader_invalid")
    try:
        control, batch = _load_batch(root / "control")
        approval = _load_approval(root, batch)
        scope = validate_live_scope(approval, batch, dataset_root=dataset_root)
        _check_drift(batch)
        money = spend_state(control)
        _require(money["batch_sha256"] == batch["batch_sha256"] and
                 money["price_snapshot"] == PRICE_SNAPSHOT and
                 money["max_requests"] == 84 and money["max_usd"] == "3.000000",
                 "live_spend_policy_mismatch")
        summary = status_run(root, private_root=parent)
        _require(summary["claimed_unresolved_cells"] == 0 and
                 summary["halted_cells"] == 0 and money["state"] == "open",
                 "live_batch_halted")
        if scripted_by_cell is None:
            _require(approval.predecessor_batch_sha256 is not None and
                     type(money.get("carried_request_count")) is int and
                     money["carried_request_count"] > 0 and
                     money.get("carried_batch_sha256") == approval.predecessor_batch_sha256,
                     "live_cumulative_budget_missing")
        if summary["batch_complete"]:
            return summary
        _require(Decimal(money["spent_usd"]) +
                 Decimal(money["reserved_usd"]) + _RESERVATION <= Decimal("3.00") and
                 money["request_count"] < 84, "live_budget_exhausted")
        if scripted_by_cell is not None:
            _require(type(scripted_by_cell) is dict, "live_script_invalid")
        write_log_policy(scope)
        key = key_reader()
        try:
            for cell in batch["cells"][summary["closed_cells"]:][:cell_limit]:
                if scripted_by_cell is not None:
                    _require(cell["cell_id"] in scripted_by_cell,
                             "live_scripted_cell_missing")
                    replies = scripted_by_cell[cell["cell_id"]]
                else:
                    replies = None
                completed = run_next_approved_policy_cell(control,
                    dataset_root=dataset_root, task_id=cell["task_id"],
                    repeat=cell["repeat"], arm=cell["arm"], out=cell["out"],
                    approval=approval, key_reader=lambda: key,
                    scripted_replies=replies)
                summary = status_run(root, private_root=parent)
                if completed["outcome"]["state"] != "completed":
                    break
                money = spend_state(control)
                if (money["state"] != "open" or money["request_count"] >= 84 or
                        Decimal(money["spent_usd"]) +
                        Decimal(money["reserved_usd"]) + _RESERVATION > Decimal("3.00")):
                    break
        finally:
            key = ""
        return summary
    except (PolicyBatchError, PolicySpendError, LiveEntryError,
            LiveScopeError) as error:
        raise LiveCliError(error.code) from None


def _read_key_without_echo() -> str:
    """Reject getpass fallback before it can read potentially echoed input."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("DeepSeek API Key (hidden): ", stream=sys.stderr)
    except Exception:
        # Includes terminal I/O and echo-control failures, without exposing the
        # exception text. KeyboardInterrupt still reaches main's cancellation.
        raise LiveCliError("live_no_echo_input_unavailable") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare", action="store_true")
    actions.add_argument("--prepare-recovery", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--execute", action="store_true")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--approval-ref")
    parser.add_argument("--retain-unknown", action="store_true")
    parser.add_argument("--predecessor-control", type=Path)
    parser.add_argument("--predecessor-sha256")
    parser.add_argument("--cell-limit", type=int, default=48)
    args = parser.parse_args(argv)
    try:
        root, _ = _run_path(args.run_dir, PRIVATE_ROOT)
        if args.prepare_recovery:
            _require(args.retain_unknown and args.cell_limit == 48,
                     "live_recovery_confirmation_missing")
            _require(args.predecessor_control is not None,
                     "live_predecessor_scope_invalid")
            batch = prepare_recovery_run(root, approval_ref=args.approval_ref,
                retain_unknown=True, predecessor_control_root=args.predecessor_control,
                predecessor_batch_sha256=args.predecessor_sha256)
            print(json.dumps({"batch_sha256": batch["batch_sha256"],
                "recovery_plan_sha256": batch["recovery_plan"]["plan_sha256"],
                "status": status_run(root, private_root=PRIVATE_ROOT)}, ensure_ascii=False))
            return 0
        if args.prepare:
            _require(not args.retain_unknown, "live_recovery_confirmation_unexpected")
            _require(args.cell_limit == 48, "live_cell_limit_invalid")
            _require(root.name.startswith("pilot-refs-v2-"), "live_generation_name_invalid")
            _require(type(args.approval_ref) is str and
                     _APPROVAL_REF.fullmatch(args.approval_ref) is not None,
                     "live_approval_ref_invalid")
            _require(args.predecessor_control is not None and
                     type(args.predecessor_sha256) is str and
                     _SHA256.fullmatch(args.predecessor_sha256) is not None,
                     "live_predecessor_scope_invalid")
            batch = prepare_run(root, approval_ref=args.approval_ref,
                predecessor_control_root=args.predecessor_control,
                predecessor_batch_sha256=args.predecessor_sha256)
            print(json.dumps({"batch_sha256": batch["batch_sha256"],
                "approval_file": str(root / "approval.json"),
                "status": status_run(root, private_root=PRIVATE_ROOT)},
                ensure_ascii=False))
            return 0
        _require(not args.retain_unknown and args.approval_ref is None and args.predecessor_control is None and
                 args.predecessor_sha256 is None, "live_approval_ref_unexpected")
        if args.status:
            _require(args.cell_limit == 48, "live_cell_limit_invalid")
            print(json.dumps(status_run(root, private_root=PRIVATE_ROOT),
                             ensure_ascii=False))
            return 0
        _require(sys.stdin.isatty() and sys.stderr.isatty(),
                 "live_interactive_tty_required")
        summary = execute_prepared(root, private_root=PRIVATE_ROOT,
            key_reader=_read_key_without_echo,
            cell_limit=args.cell_limit)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["batch_complete"] or (
            summary["halted_cells"] == 0 and
            summary["claimed_unresolved_cells"] == 0) else 1
    except KeyboardInterrupt:
        print("live_interrupted; inspect --status before any further action",
              file=sys.stderr)
        return 130
    except (LiveCliError, OSError) as error:
        print(getattr(error, "code", "live_io_invalid"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
