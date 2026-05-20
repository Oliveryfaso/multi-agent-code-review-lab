from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


class CodeSmellAgent:
    """Computes a pragmatic maintainability risk summary for Python repositories."""

    def analyze(self, repo_path: Path) -> dict[str, Any]:
        files = sorted(path for path in repo_path.rglob("*.py") if self._include(path.relative_to(repo_path)))
        file_reports = [self._analyze_file(repo_path, path) for path in files]
        hotspots = sorted(
            (hotspot for report in file_reports for hotspot in report["hotspots"]),
            key=lambda item: item["score"],
            reverse=True,
        )[:8]
        total_units = sum(report["unit_count"] for report in file_reports)
        smelly_units = sum(report["smelly_units"] for report in file_reports)
        ratio = round(smelly_units / total_units, 3) if total_units else 0.0
        severity = self._severity(ratio, hotspots)
        suggestions = self._suggestions(hotspots, ratio)
        return {
            "status": "ok" if severity in {"low", "medium"} else "review",
            "severity": severity,
            "smell_ratio": ratio,
            "smelly_units": smelly_units,
            "total_units": total_units,
            "python_file_count": len(file_reports),
            "hotspots": hotspots,
            "suggestions": suggestions,
        }

    def _analyze_file(self, repo_path: Path, path: Path) -> dict[str, Any]:
        rel_file = str(path.relative_to(repo_path))
        lines = path.read_text(errors="ignore").splitlines()
        hotspots: list[dict[str, Any]] = []
        unit_count = 1
        smelly_units = 0
        if len(lines) > 300:
            smelly_units += 1
            hotspots.append(self._hotspot(rel_file, 1, "large_file", len(lines) / 40, f"{len(lines)} lines in one file"))

        try:
            tree = ast.parse("\n".join(lines) + "\n")
        except SyntaxError as exc:
            return {
                "file": rel_file,
                "unit_count": unit_count,
                "smelly_units": smelly_units + 1,
                "hotspots": [self._hotspot(rel_file, exc.lineno or 1, "syntax_error", 12, "file cannot be parsed by Python AST")],
            }

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                unit_count += 1
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                smells = self._function_smells(node)
                if smells:
                    smelly_units += 1
                    hotspots.append(
                        self._hotspot(
                            rel_file,
                            node.lineno,
                            "function_smell",
                            sum(item["score"] for item in smells),
                            f"{node.name}: " + ", ".join(item["reason"] for item in smells),
                            symbol=node.name,
                        )
                    )
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                smelly_units += 1
                hotspots.append(self._hotspot(rel_file, node.lineno, "broad_except", 6, "bare except hides failure modes"))
        return {
            "file": rel_file,
            "unit_count": unit_count,
            "smelly_units": smelly_units,
            "hotspots": hotspots,
        }

    def _function_smells(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
        smells = []
        line_count = max(1, (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1)
        branch_nodes = [ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.BoolOp]
        if hasattr(ast, "Match"):
            branch_nodes.append(ast.Match)
        branch_count = sum(isinstance(child, tuple(branch_nodes)) for child in ast.walk(node))
        arg_count = len(node.args.args) + len(node.args.kwonlyargs)
        nested_functions = sum(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for child in ast.walk(node)) - 1
        returns = sum(isinstance(child, ast.Return) for child in ast.walk(node))
        if line_count > 60:
            smells.append({"score": min(12, line_count / 10), "reason": f"long function ({line_count} lines)"})
        if branch_count > 10:
            smells.append({"score": min(12, branch_count), "reason": f"high branch complexity ({branch_count})"})
        if arg_count > 6:
            smells.append({"score": 5 + arg_count / 2, "reason": f"too many parameters ({arg_count})"})
        if nested_functions > 1:
            smells.append({"score": 4 + nested_functions, "reason": f"nested functions ({nested_functions})"})
        if returns > 8:
            smells.append({"score": 4 + returns / 2, "reason": f"many return exits ({returns})"})
        return smells

    def _hotspot(
        self,
        file: str,
        line: int,
        kind: str,
        score: float,
        reason: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        return {
            "file": file,
            "line": line,
            "symbol": symbol,
            "kind": kind,
            "score": round(score, 2),
            "reason": reason,
        }

    def _severity(self, ratio: float, hotspots: list[dict[str, Any]]) -> str:
        max_score = max((item["score"] for item in hotspots), default=0)
        if ratio >= 0.45 or max_score >= 18:
            return "high"
        if ratio >= 0.25 or max_score >= 10:
            return "medium"
        return "low"

    def _suggestions(self, hotspots: list[dict[str, Any]], ratio: float) -> list[str]:
        suggestions = []
        kinds = {item["kind"] for item in hotspots}
        if ratio >= 0.25:
            suggestions.append("prioritize hotspots before adding larger feature changes")
        if "function_smell" in kinds:
            suggestions.append("split long or high-branch functions around explicit validation, IO, and domain steps")
        if "large_file" in kinds:
            suggestions.append("move cohesive functions into smaller modules with narrower ownership")
        if "broad_except" in kinds:
            suggestions.append("replace bare except blocks with typed exceptions and observable error handling")
        return suggestions[:4]

    def _include(self, path: Path) -> bool:
        ignored_parts = {".venv", "__pycache__", ".git", ".macr_cache", "external_repos"}
        return not any(part in ignored_parts for part in path.parts)
