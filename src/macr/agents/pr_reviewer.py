from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiffHunk:
    file: str
    old_start: int
    new_start: int
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)


class DiffReviewAgent:
    """Reviews unified diffs with deterministic engineering risk rules."""

    RISK_PATTERNS = [
        ("high", re.compile(r"\b(eval|exec|subprocess\.Popen|shell=True)\b"), "dangerous execution path"),
        ("high", re.compile(r"\b(password|secret|token|api_key)\s*="), "possible hardcoded secret"),
        ("medium", re.compile(r"\bexcept\s*:"), "bare except hides failure modes"),
        ("medium", re.compile(r"\bTODO\b|\bFIXME\b"), "unfinished implementation marker"),
        ("medium", re.compile(r"\bprint\s*\("), "debug print in changed code"),
        ("medium", re.compile(r"\btime\.sleep\s*\("), "sleep-based synchronization is brittle"),
        ("low", re.compile(r"\breturn\s+None\b"), "explicit None return may need caller handling"),
    ]

    TEST_FILE_PATTERNS = ("/test_", "tests/", "_test.py", ".spec.", ".test.")

    def review(self, diff_text: str) -> dict[str, Any]:
        hunks = self._parse(diff_text)
        comments: list[dict[str, Any]] = []
        changed_files = sorted({hunk.file for hunk in hunks})
        added_lines = sum(len(hunk.added) for hunk in hunks)
        removed_lines = sum(len(hunk.removed) for hunk in hunks)
        touched_tests = [path for path in changed_files if self._is_test_file(path)]

        for hunk in hunks:
            comments.extend(self._review_hunk(hunk))

        risk = self._risk_level(comments, changed_files, added_lines, touched_tests)
        return {
            "status": "ok" if risk in {"low", "medium"} else "review",
            "risk_level": risk,
            "changed_files": changed_files,
            "changed_file_count": len(changed_files),
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "test_files": touched_tests,
            "comments": comments[:30],
            "summary": self._summary(risk, changed_files, added_lines, removed_lines, comments, touched_tests),
            "test_suggestions": self._test_suggestions(changed_files, touched_tests, comments),
        }

    def _parse(self, diff_text: str) -> list[DiffHunk]:
        hunks: list[DiffHunk] = []
        current_file = ""
        current: DiffHunk | None = None
        new_line = 0
        old_line = 0
        for raw_line in diff_text.splitlines():
            if raw_line.startswith("+++ "):
                current_file = raw_line[4:].strip()
                if current_file.startswith("b/"):
                    current_file = current_file[2:]
                continue
            if raw_line.startswith("@@"):
                match = re.search(r"-(\d+)(?:,\d+)? \+(\d+)(?:,\d+)?", raw_line)
                old_start = int(match.group(1)) if match else 0
                new_start = int(match.group(2)) if match else 0
                current = DiffHunk(file=current_file or "unknown", old_start=old_start, new_start=new_start)
                hunks.append(current)
                old_line = old_start
                new_line = new_start
                continue
            if current is None or raw_line.startswith(("diff --git", "index ", "--- ")):
                continue
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                current.added.append((new_line, raw_line[1:]))
                new_line += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                current.removed.append((old_line, raw_line[1:]))
                old_line += 1
            else:
                old_line += 1
                new_line += 1
        return hunks

    def _review_hunk(self, hunk: DiffHunk) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        for line_number, line in hunk.added:
            stripped = line.strip()
            for severity, pattern, reason in self.RISK_PATTERNS:
                if pattern.search(stripped):
                    comments.append(
                        {
                            "file": hunk.file,
                            "line": line_number,
                            "severity": severity,
                            "category": "code_risk",
                            "message": reason,
                            "evidence": stripped[:220],
                        }
                    )
            if len(stripped) > 140:
                comments.append(
                    {
                        "file": hunk.file,
                        "line": line_number,
                        "severity": "low",
                        "category": "maintainability",
                        "message": "very long changed line; consider extracting a named helper or wrapping expression",
                        "evidence": stripped[:220],
                    }
                )
        if len(hunk.added) >= 80 and not self._is_test_file(hunk.file):
            comments.append(
                {
                    "file": hunk.file,
                    "line": hunk.new_start,
                    "severity": "medium",
                    "category": "review_scope",
                    "message": "large non-test hunk; review should include focused tests and smaller patch split if possible",
                    "evidence": f"{len(hunk.added)} added lines in one hunk",
                }
            )
        return comments

    def _risk_level(self, comments: list[dict[str, Any]], changed_files: list[str], added_lines: int, touched_tests: list[str]) -> str:
        severities = {comment["severity"] for comment in comments}
        if "high" in severities:
            return "high"
        if len(changed_files) >= 8 or added_lines >= 250:
            return "high"
        if "medium" in severities:
            return "medium"
        if not touched_tests and any(not self._is_test_file(path) for path in changed_files):
            return "medium"
        return "low"

    def _summary(
        self,
        risk: str,
        changed_files: list[str],
        added_lines: int,
        removed_lines: int,
        comments: list[dict[str, Any]],
        touched_tests: list[str],
    ) -> str:
        test_note = "包含测试变更" if touched_tests else "未检测到测试文件变更"
        return (
            f"Diff risk is `{risk}` across {len(changed_files)} files "
            f"({added_lines} added / {removed_lines} removed). "
            f"{test_note}。发现 {len(comments)} 条规则审查意见。"
        )

    def _test_suggestions(self, changed_files: list[str], touched_tests: list[str], comments: list[dict[str, Any]]) -> list[str]:
        suggestions: list[str] = []
        if not touched_tests and any(not self._is_test_file(path) for path in changed_files):
            suggestions.append("add or run targeted tests for changed production files")
        if any(comment["severity"] == "high" for comment in comments):
            suggestions.append("run security-focused review before merging high-risk changes")
        if any(path.endswith(".py") for path in changed_files):
            suggestions.append("run Python unit tests for affected modules")
        if not suggestions:
            suggestions.append("run the repository's standard CI or pre-merge test command")
        return list(dict.fromkeys(suggestions))

    def _is_test_file(self, path: str) -> bool:
        lower = path.lower()
        return any(pattern in lower for pattern in self.TEST_FILE_PATTERNS)
