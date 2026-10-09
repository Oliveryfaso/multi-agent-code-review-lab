from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path
from macr.schemas import ToolResult
from macr.tools.timing import Timer
from macr.investigation.validation import ContractError, validate_relative

class GitTool:
    name = "git_log"

    def run(self, repo_path: Path, rel_files: list[str], *, base_commit: str | None = None) -> ToolResult:
        if base_commit is None:
            return ToolResult(False, self.name, {"commits": [], "legacy": True}, "Frozen base commit required", 0, "history_unavailable")
        return self.read_history(repo_path, base_commit, rel_files, 5)

    def read_history(self, repo: Path, base_commit: str, paths: list[str], limit: int) -> ToolResult:
        with Timer() as timer:
            try:
                if not isinstance(base_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", base_commit) or type(limit) is not int or not 1 <= limit <= 100:
                    raise ContractError('contract_invalid')
                for path in paths:
                    validate_relative(path)
            except ContractError:
                return ToolResult(False, self.name, {}, "History request rejected", timer.elapsed_ms(), "contract_invalid")
            if not (repo / '.git').exists():
                return ToolResult(False, self.name, {"commits": []}, "History unavailable", timer.elapsed_ms(), "history_unavailable")
            args = ['git', '--no-pager', '-c', 'core.hooksPath=/dev/null', '-c', 'log.showSignature=false', '-C', str(repo), 'log', '--no-show-signature', '--no-ext-diff', '--no-textconv', '--format=%h %s', f'-{limit}', base_commit, '--', *paths]
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1')
            try:
                proc = subprocess.run(args, text=True, capture_output=True, check=False, timeout=5, env=env)
            except subprocess.TimeoutExpired:
                return ToolResult(False, self.name, {}, "History timed out", timer.elapsed_ms(), "tool_timeout")
            except OSError:
                return ToolResult(False, self.name, {}, "History unavailable", timer.elapsed_ms(), "history_unavailable")
            if proc.returncode:
                return ToolResult(False, self.name, {}, "History failed", timer.elapsed_ms(), "tool_error")
            lines = proc.stdout.splitlines()
            commits = [{"summary": line[:500]} for line in lines[:limit]]
            return ToolResult(True, self.name, {"commits": commits, "base_commit": base_commit, "truncated": len(lines) > limit}, "Frozen history loaded", timer.elapsed_ms())
