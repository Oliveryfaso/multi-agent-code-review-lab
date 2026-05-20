from __future__ import annotations

import subprocess
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.timing import Timer


class TextSearchTool:
    name = "search_text"

    def run(self, repo_path: Path, query: str, max_results: int = 20) -> ToolResult:
        with Timer() as timer:
            try:
                proc = subprocess.run(
                    ["rg", "-n", "--no-heading", "--glob", "!*.pyc", query, str(repo_path)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
            except FileNotFoundError:
                return self._fallback(repo_path, query, max_results, timer)
        if proc.returncode not in {0, 1}:
            return ToolResult(False, self.name, {}, "rg failed", timer.latency_ms, "tool_error", proc.stderr, True)
        matches = []
        for line in proc.stdout.splitlines()[:max_results]:
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            path, line_no, text = parts
            try:
                rel = str(Path(path).resolve().relative_to(repo_path.resolve()))
            except ValueError:
                rel = path
            matches.append({"file": rel, "line": int(line_no), "text": text.strip()})
        if not matches:
            return ToolResult(
                False,
                self.name,
                {"matches": []},
                f"No matches for `{query}`",
                timer.latency_ms,
                "empty_recall",
                f"No text matches for {query}",
                True,
                "semantic_search",
            )
        return ToolResult(True, self.name, {"query": query, "matches": matches}, f"Found {len(matches)} matches", timer.latency_ms)

    def _fallback(self, repo_path: Path, query: str, max_results: int, timer: Timer) -> ToolResult:
        matches = []
        for path in repo_path.rglob("*"):
            if len(matches) >= max_results or not path.is_file() or path.suffix in {".pyc", ".png", ".jpg"}:
                continue
            try:
                for index, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
                    if query.lower() in line.lower():
                        matches.append(
                            {
                                "file": str(path.relative_to(repo_path)),
                                "line": index,
                                "text": line.strip(),
                            }
                        )
                        break
            except OSError:
                continue
        if not matches:
            return ToolResult(False, self.name, {"matches": []}, f"No matches for `{query}`", timer.latency_ms, "empty_recall", retryable=True)
        return ToolResult(True, self.name, {"query": query, "matches": matches}, f"Found {len(matches)} matches", timer.latency_ms)

