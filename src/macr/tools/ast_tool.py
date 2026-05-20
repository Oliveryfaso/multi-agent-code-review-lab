from __future__ import annotations

import ast
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.timing import Timer


class PythonAstTool:
    name = "parse_ast"

    def run(self, repo_path: Path, rel_file: str, terms: list[str], focus_lines: list[int] | None = None) -> ToolResult:
        path = repo_path / rel_file
        with Timer() as timer:
            try:
                source = path.read_text()
                tree = ast.parse(source)
            except (OSError, SyntaxError) as exc:
                return ToolResult(False, self.name, {}, "AST parse failed", timer.elapsed_ms(), "parse_error", str(exc), False)
            symbols = []
            lower_terms = [term.lower() for term in terms]
            focus = set(focus_lines or [])
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = node.name
                    matches = any(term in name.lower() for term in lower_terms)
                    line_start = node.lineno
                    line_end = getattr(node, "end_lineno", node.lineno)
                    contains_focus_line = any(line_start <= line <= line_end for line in focus)
                    if matches or contains_focus_line or not lower_terms:
                        symbols.append(
                            {
                                "name": name,
                                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                                "line_start": line_start,
                                "line_end": line_end,
                                "matched_by": "focus_line" if contains_focus_line and not matches else "term",
                            }
                        )
            imports = [
                {"module": alias.name, "line": node.lineno}
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            ]
        return ToolResult(
            True,
            self.name,
            {"file": rel_file, "symbols": symbols, "imports": imports[:20]},
            f"Parsed {rel_file}: {len(symbols)} relevant symbols",
            timer.latency_ms,
        )
