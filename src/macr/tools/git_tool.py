from __future__ import annotations

import subprocess
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.timing import Timer


class GitTool:
    name = "git_log"

    def run(self, repo_path: Path, rel_files: list[str]) -> ToolResult:
        with Timer() as timer:
            if not (repo_path / ".git").exists():
                return ToolResult(True, self.name, {"commits": []}, "Repository has no .git directory", timer.elapsed_ms())
            args = ["git", "-C", str(repo_path), "log", "--oneline", "-5"]
            if rel_files:
                args.extend(["--", *rel_files])
            proc = subprocess.run(args, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            return ToolResult(False, self.name, {}, "git log failed", timer.latency_ms, "tool_error", proc.stderr, True)
        commits = [{"summary": line} for line in proc.stdout.splitlines()]
        return ToolResult(True, self.name, {"commits": commits}, f"Loaded {len(commits)} recent commits", timer.latency_ms)
