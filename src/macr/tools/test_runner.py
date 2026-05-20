from __future__ import annotations

import subprocess
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.timing import Timer


class TestRunnerTool:
    name = "run_tests"

    def run(self, repo_path: Path, selector: str) -> ToolResult:
        with Timer() as timer:
            proc = subprocess.run(
                ["python3", "-m", "pytest", selector, "-q"],
                cwd=repo_path,
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0 and "No module named pytest" in proc.stderr:
                proc = subprocess.run(
                    ["python3", "-m", "unittest", "discover", "-s", selector],
                    cwd=repo_path,
                    text=True,
                    capture_output=True,
                    check=False,
                )
        ok = proc.returncode == 0
        return ToolResult(
            ok,
            self.name,
            {"returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]},
            "tests passed" if ok else "tests failed",
            timer.latency_ms,
            None if ok else "test_failure",
            None if ok else proc.stdout[-1000:],
            not ok,
        )
