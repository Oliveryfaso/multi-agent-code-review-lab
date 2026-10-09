from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from macr.execution.base import ExecutionBackend
from macr.investigation.records import CheckSpec, ExecutionProfile, SnapshotRef
from macr.investigation.validation import ContractError
from macr.schemas import ToolResult
from macr.tools.test_runner import TestRunnerTool


class PatchVerifierTool:
    """Legacy facade. Diff and tests execute only through an approved backend."""
    name = "verify_patch"

    def __init__(self, profile: ExecutionProfile | None = None, artifact_root: Path | None = None):
        self.profile = profile
        allowed = Path(__file__).resolve().parents[3] / "artifacts"
        self.root = (artifact_root or allowed / "patch_checks").resolve()
        if not self.root.is_relative_to(allowed.resolve()):
            raise ContractError("forbidden_path", "artifact_root")

    def run(self, repo_path: Path, diff: str, test_selector: str | None = None, *,
            backend: ExecutionBackend | None = None, snapshot: SnapshotRef | None = None) -> ToolResult:
        if not diff.strip():
            return ToolResult(True, self.name, {"patch_apply_check": "skipped", "test_check": "not_applicable"}, "No patch requested", 0)
        if backend is None or snapshot is None or self.profile is None:
            return ToolResult(False, self.name, {"patch_apply_check": "blocked", "test_check": "not_applicable"},
                              "Approved backend, profile and sanitized snapshot required", 0, "environment_missing")
        run_id = str(uuid4())
        folder = self.root / run_id
        folder.mkdir(parents=True, exist_ok=False)
        patch_path = folder / "patch.diff"
        patch_path.write_text(diff, encoding="utf-8")
        check = CheckSpec(run_id, purpose="patch_apply", argv_profile="patch_apply",
                          input_artifacts=[str(patch_path.relative_to(Path(__file__).resolve().parents[3] / "artifacts"))],
                          execution_profile=self.profile.profile_id, limits=self.profile.limits)
        runner = TestRunnerTool()
        apply_result = runner.run_check(snapshot, check, backend)
        data = {"patch_apply_check": apply_result.observed_status, "test_check": "not_applicable",
                "apply_result": asdict(apply_result), "test_result": None, "repaired": False}
        if apply_result.observed_status != "passed":
            return ToolResult(False, self.name, data, "Patch application not confirmed", 0, "patch_apply_failed")
        if test_selector:
            if test_selector.startswith("-") or any(c in test_selector for c in "\x00\n\r;"):
                return ToolResult(False, self.name, data, "Test selector rejected", 0, "forbidden_action")
            test_check = CheckSpec(str(uuid4()), selectors=[test_selector], input_artifacts=check.input_artifacts,
                                   execution_profile=self.profile.profile_id, limits=self.profile.limits)
            test_result = runner.run_check(snapshot, test_check, backend)
            data.update(test_check=test_result.observed_status, test_result=asdict(test_result))
            ok = test_result.observed_status == "passed"
        else:
            ok = True
        return ToolResult(ok, self.name, data, "Patch applies; validation scope recorded", 0, None if ok else "test_failure")
