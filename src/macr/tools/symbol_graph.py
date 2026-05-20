from __future__ import annotations

import ast
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.timing import Timer


class SymbolGraphTool:
    name = "symbol_graph"

    def run(self, repo_path: Path, terms: list[str], candidate_files: list[str] | None = None) -> ToolResult:
        with Timer() as timer:
            definitions: dict[str, list[dict]] = {}
            references: dict[str, list[dict]] = {}
            files = [repo_path / item for item in candidate_files or [] if item.endswith(".py")]
            if not files:
                files = list(repo_path.rglob("*.py"))
            for path in files[:80]:
                try:
                    source = path.read_text()
                    tree = ast.parse(source)
                    rel = str(path.relative_to(repo_path))
                except (OSError, SyntaxError, ValueError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        definitions.setdefault(node.name, []).append(
                            {
                                "file": rel,
                                "line_start": node.lineno,
                                "line_end": getattr(node, "end_lineno", node.lineno),
                                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                            }
                        )
                    elif isinstance(node, ast.Name):
                        references.setdefault(node.id, []).append({"file": rel, "line": node.lineno})
                    elif isinstance(node, ast.Attribute):
                        references.setdefault(node.attr, []).append({"file": rel, "line": node.lineno})

            matched = []
            lower_terms = [term.lower() for term in terms]
            for symbol, defs in definitions.items():
                if not lower_terms or any(term in symbol.lower() for term in lower_terms):
                    matched.append(
                        {
                            "symbol": symbol,
                            "definitions": defs,
                            "references": references.get(symbol, [])[:20],
                        }
                    )
        return ToolResult(
            True,
            self.name,
            {
                "matched_symbols": matched[:30],
                "definition_count": sum(len(items) for items in definitions.values()),
                "reference_symbol_count": len(references),
            },
            f"Built symbol graph with {len(matched[:30])} matched symbols",
            timer.latency_ms,
        )
