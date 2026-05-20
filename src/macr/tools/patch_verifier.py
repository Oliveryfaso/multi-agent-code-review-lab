from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.test_runner import TestRunnerTool
from macr.tools.timing import Timer


class PatchVerifierTool:
    name = "verify_patch"

    def run(self, repo_path: Path, diff: str, test_selector: str | None = None) -> ToolResult:
        with Timer() as timer:
            if not diff.strip():
                return ToolResult(
                    True,
                    self.name,
                    {"patch_apply_check": "skipped", "test_check": "skipped"},
                    "No patch to verify",
                    timer.elapsed_ms(),
                )
            with tempfile.TemporaryDirectory() as tmp:
                tmp_repo = Path(tmp) / "repo"
                ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".git")
                shutil.copytree(repo_path, tmp_repo, ignore=ignore)
                proc = subprocess.run(
                    ["git", "apply", "--check", "-"],
                    input=diff,
                    cwd=tmp_repo,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if proc.returncode != 0:
                    return ToolResult(
                        False,
                        self.name,
                        {"patch_apply_check": "failed", "stderr": proc.stderr[-2000:]},
                        "Patch does not apply cleanly",
                        timer.elapsed_ms(),
                        "patch_apply_failed",
                        proc.stderr[-1000:],
                        True,
                    )
                apply_proc = subprocess.run(
                    ["git", "apply", "-"],
                    input=diff,
                    cwd=tmp_repo,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if apply_proc.returncode != 0:
                    return ToolResult(
                        False,
                        self.name,
                        {"patch_apply_check": "failed", "stderr": apply_proc.stderr[-2000:]},
                        "Patch failed during apply",
                        timer.elapsed_ms(),
                        "patch_apply_failed",
                        apply_proc.stderr[-1000:],
                        True,
                    )
                test_result = None
                if test_selector:
                    test_result = TestRunnerTool().run(tmp_repo, test_selector)
        data = {
            "patch_apply_check": "passed",
            "test_check": "passed" if not test_result or test_result.ok else "failed",
            "test_result": test_result.data if test_result else None,
        }
        ok = not test_result or test_result.ok
        return ToolResult(
            ok,
            self.name,
            data,
            "Patch verified" if ok else "Patch applies but tests failed",
            timer.latency_ms,
            None if ok else "test_failure",
            None if ok else "tests failed after patch",
            not ok,
        )
