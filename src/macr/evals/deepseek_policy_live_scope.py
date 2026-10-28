"""Explicit local approval boundary; no transport, credential store or deletion.

Approval references are caller-supplied evidence of human consent, not a
cryptographic proof. This module never reads gold, a real Key, or network data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import stat

from macr.evals.deepseek_policy_batch import _digest
from macr.evals.deepseek_policy_manifest import PolicyManifestError, load_policy_manifest
from macr.investigation.scope import open_directory, validate_workspace_path
from macr.investigation.validation import ContractError


class LiveScopeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise LiveScopeError(code)


@dataclass(frozen=True)
class LiveApproval:
    granted: bool
    approval_ref: str
    batch_sha256: str
    model_id: str
    endpoint: str
    max_requests: int
    max_usd: str
    price_snapshot_id: str
    price_checked_on: str
    approved_sources: tuple[str, ...]
    log_root: Path
    retention_days: int = 14
    predecessor_batch_sha256: str | None = None


@dataclass(frozen=True)
class ValidatedLiveScope:
    approval_ref: str
    batch_sha256: str
    model_id: str
    endpoint: str
    max_requests: int
    max_usd: str
    price_snapshot_id: str
    price_checked_on: str
    approved_sources: tuple[str, ...]
    log_root: Path
    retention_days: int
    predecessor_batch_sha256: str | None = None


LOG_CONTENTS = ("source", "model_output", "reasoning_content", "usage", "receipts")
MODEL_ID = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_USD = re.compile(r"(?:0|[1-9][0-9]{0,2})\.[0-9]{2}\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _private_log_root(path: Path) -> Path:
    path = Path(path).absolute()
    try:
        validate_workspace_path(path, output=True)
        with open_directory(path) as descriptor:
            mode = os.fstat(descriptor).st_mode
    except (OSError, ContractError):
        raise LiveScopeError("live_log_root_invalid") from None
    _require(stat.S_ISDIR(mode) and stat.S_IMODE(mode) == 0o700,
             "live_log_root_not_private")
    return path


def validate_live_scope(approval: LiveApproval, batch: dict, *, dataset_root: Path) -> ValidatedLiveScope:
    """Bind one explicit 12×4×1 approval to neutral frozen inputs only."""
    _require(type(approval) is LiveApproval and type(batch) is dict,
             "live_approval_invalid")
    _require(approval.granted is True and type(approval.approval_ref) is str and
             _IDENTIFIER.fullmatch(approval.approval_ref) is not None,
             "live_approval_missing")
    _require(type(approval.batch_sha256) is str and
             _DIGEST.fullmatch(approval.batch_sha256) is not None and
             approval.batch_sha256 == batch.get("batch_sha256"), "live_batch_mismatch")
    try:
        bound = {key: value for key, value in batch.items() if key != "batch_sha256"}
        _require(_digest(bound) == approval.batch_sha256, "live_batch_mismatch")
    except (TypeError, ValueError):
        raise LiveScopeError("live_batch_mismatch") from None
    _require(approval.model_id == MODEL_ID and approval.endpoint == ENDPOINT and
             batch.get("model") == {"model_id": MODEL_ID, "provider": "deepseek",
                                    "thinking": True, "reasoning_effort": "high"} and
             batch.get("response_model_allowlist") == [MODEL_ID] and
             batch.get("fingerprint_policy") == "pin_first", "live_model_or_endpoint_invalid")
    _require(type(approval.max_requests) is int and approval.max_requests == 84,
             "live_request_count_invalid")
    _require(type(approval.max_usd) is str and _USD.fullmatch(approval.max_usd) is not None,
             "live_usd_invalid")
    try:
        _require(Decimal(approval.max_usd) > 0, "live_usd_invalid")
    except InvalidOperation:
        raise LiveScopeError("live_usd_invalid") from None
    _require(type(approval.price_snapshot_id) is str and
             _IDENTIFIER.fullmatch(approval.price_snapshot_id) is not None and
             type(approval.price_checked_on) is str and
             _DATE.fullmatch(approval.price_checked_on) is not None,
             "live_price_snapshot_invalid")
    try:
        date.fromisoformat(approval.price_checked_on)
    except ValueError:
        raise LiveScopeError("live_price_snapshot_invalid") from None
    _require(type(approval.retention_days) is int and approval.retention_days == 14,
             "live_retention_invalid")
    predecessor = approval.predecessor_batch_sha256
    _require(predecessor is None or (type(predecessor) is str and
             _DIGEST.fullmatch(predecessor) is not None and
             predecessor != approval.batch_sha256), "live_predecessor_invalid")

    dataset = Path(dataset_root).absolute()
    _require(str(dataset) == batch.get("dataset_root"), "live_dataset_mismatch")
    manifests = batch.get("manifests")
    cells = batch.get("cells")
    _require(type(manifests) is list and len(manifests) == 12 and
             type(cells) is list and len(cells) == 48, "live_grid_invalid")
    expected_sources: list[str] = []
    task_ids: set[str] = set()
    for row in manifests:
        _require(type(row) is dict and type(row.get("manifest_dir")) is str,
                 "live_manifest_invalid")
        try:
            manifest = load_policy_manifest(row["manifest_dir"], dataset_root=dataset)
        except PolicyManifestError:
            raise LiveScopeError("live_manifest_invalid") from None
        task = manifest["task"]
        _require(manifest["repeat"] == 1 and task["id"] == row.get("task_id") and
                 manifest["manifest_sha256"] == row.get("manifest_sha256") and
                 task["id"] not in task_ids, "live_manifest_invalid")
        task_ids.add(task["id"])
        expected_sources.extend(task["snapshot_root"] + "/" + name
                                for name in task["authorized_files"])
    _require(len(expected_sources) == 18 and len(set(expected_sources)) == 18,
             "live_source_grid_invalid")
    cell_ids = set()
    grid = set()
    for cell in cells:
        _require(type(cell) is dict and cell.get("task_id") in task_ids and
                 cell.get("repeat") == 1 and cell.get("arm") in ("A", "B", "C", "D") and
                 type(cell.get("cell_id")) is str, "live_grid_invalid")
        identity = (cell["task_id"], cell["arm"])
        _require(identity not in grid and cell["cell_id"] not in cell_ids,
                 "live_grid_invalid")
        grid.add(identity)
        cell_ids.add(cell["cell_id"])
    _require(len(grid) == 48, "live_grid_invalid")
    _require(type(approval.approved_sources) is tuple and
             len(approval.approved_sources) == len(expected_sources) and
             len(set(approval.approved_sources)) == len(expected_sources) and
             set(approval.approved_sources) == set(expected_sources),
             "live_sources_not_approved")

    log_root = _private_log_root(approval.log_root)
    _require(str(log_root) == batch.get("output_root") and
             not log_root.is_relative_to(dataset) and
             not log_root.is_relative_to(Path(batch["project_root"])),
             "live_log_root_mismatch")
    return ValidatedLiveScope(approval_ref=approval.approval_ref,
        batch_sha256=approval.batch_sha256, model_id=MODEL_ID, endpoint=ENDPOINT,
        max_requests=84, max_usd=approval.max_usd,
        price_snapshot_id=approval.price_snapshot_id,
        price_checked_on=approval.price_checked_on,
        approved_sources=tuple(sorted(expected_sources)), log_root=log_root,
        retention_days=approval.retention_days,
        predecessor_batch_sha256=predecessor)


def acquire_runtime_inputs(approval: LiveApproval, batch: dict, *, dataset_root: Path,
                           key_reader) -> tuple[ValidatedLiveScope, str]:
    """Call an injected reader only after scope and log-policy validation.

    The returned Key exists only in caller memory; this module never persists it.
    """
    scope = validate_live_scope(approval, batch, dataset_root=dataset_root)
    write_log_policy(scope)
    _require(callable(key_reader), "live_key_reader_invalid")
    try:
        key = key_reader()
    except Exception:
        raise LiveScopeError("live_key_input_failed") from None
    _require(type(key) is str and 1 <= len(key) <= 4096 and key == key.strip() and
             all(32 < ord(char) < 127 for char in key), "live_key_invalid")
    return scope, key


def write_log_policy(scope: ValidatedLiveScope) -> Path:
    """Freeze local retention instructions; never upload or delete run artifacts."""
    _require(type(scope) is ValidatedLiveScope, "live_scope_invalid")
    root = _private_log_root(scope.log_root)
    record = {"schema": "macr.deepseek.policy.log.v1",
              "approval_ref": scope.approval_ref,
              "batch_sha256": scope.batch_sha256,
              "predecessor_batch_sha256": scope.predecessor_batch_sha256,
              "price_snapshot_id": scope.price_snapshot_id,
              "contents": list(LOG_CONTENTS),
              "retention_days": scope.retention_days,
              "auto_delete": False, "upload": False,
              "purge_requires_user_approval": True}
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    _require(len(payload) <= 4096, "live_log_policy_limit")
    path = root / "retention.json"
    try:
        validate_workspace_path(path, output=True)
        with open_directory(root) as parent_fd:
            try:
                fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent_fd)
            except FileExistsError:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent_fd)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    _require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                             and info.st_size == len(payload), "live_log_policy_mismatch")
                    _require(stream.read(len(payload) + 1) == payload,
                             "live_log_policy_mismatch")
            else:
                with os.fdopen(fd, "wb") as stream:
                    _require(stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600,
                             "live_log_policy_mismatch")
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(parent_fd)
    except (OSError, ContractError):
        raise LiveScopeError("live_log_policy_write_invalid") from None
    return path
