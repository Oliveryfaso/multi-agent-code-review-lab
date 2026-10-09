from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from macr.execution.base import ExecutionBackend
from macr.execution.results import classify_check
from macr.investigation.records import CheckResult, CheckSpec, SnapshotRef
from macr.investigation.validation import ContractError, decode_record
from macr.schemas import ToolResult


class TestRunnerTool:
    name = "run_tests"

    def run(self, repo_path: Path, selector: str) -> ToolResult:
        return ToolResult(False, self.name, {"execution_status": "blocked", "observed_status": "not_applicable"},
                          "An approved isolation backend is required", 0, "environment_missing")

    def run_check(self, snapshot: SnapshotRef, check: CheckSpec, backend: ExecutionBackend | None) -> CheckResult:
        try:
            check = decode_record(CheckSpec, asdict(check))
            if any(not selector or selector.startswith('-') or any(c in selector for c in '\x00\n\r;') for selector in check.selectors):
                raise ContractError('forbidden_action')
        except (ContractError, TypeError):
            return classify_check({'execution_status': 'blocked'}, CheckSpec(getattr(check, 'check_id', 'invalid')))
        if backend is None or snapshot is None or check.limits is None:
            return classify_check({"execution_status": "blocked"}, check)
        caps = backend.capabilities()
        required = ("network_disabled", "readonly_input", "isolated_writes", "secrets_absent", "cpu_limit",
                    "memory_limit", "pid_limit", "wall_limit", "output_limit", "process_tree_stop")
        if not all(getattr(caps, name, False) is True for name in required):
            return classify_check({"execution_status": "blocked"}, check)
        if any(getattr(check.limits, name) <= 0 for name in ("cpu", "memory_bytes", "pids", "wall_seconds", "output_bytes")):
            return classify_check({"execution_status": "blocked"}, check)
        try:
            result = backend.run(check, snapshot)
        except Exception:
            return classify_check({'execution_status': 'error'}, check)
        if not isinstance(result, CheckResult) or result.check_id != check.check_id:
            return classify_check({"execution_status": "error"}, check)
        try:
            result = decode_record(CheckResult, asdict(result))
        except ContractError:
            return classify_check({'execution_status': 'error'}, check)
        return classify_check(asdict(result), check)
