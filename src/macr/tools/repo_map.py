from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from macr.schemas import ToolResult, relative_path
from macr.tools.timing import Timer


class RepoMapTool:
    name = "repo_map"

    SKIP_DIRS = {
        ".git",
        ".hg",
        ".mypy_cache",
        ".macr_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "patches",
        "reports",
        "traces",
    }

    def run(self, repo_path: Path, terms: list[str], max_files: int = 80, max_symbols: int = 160) -> ToolResult:
        with Timer() as timer:
            files = sorted(self._python_files(repo_path))[:1200]
            file_maps = [self._file_map(repo_path, path, terms) for path in files]
            file_maps = [item for item in file_maps if item]
            file_maps.sort(key=lambda item: (-item["score"], item["file"]))
            focus_files = [item for item in file_maps if item["score"] > 0][:max_files]
            if not focus_files:
                focus_files = file_maps[: min(12, max_files)]
            symbols = [
                symbol
                for item in focus_files
                for symbol in item.get("symbols", [])
            ][:max_symbols]

        return ToolResult(
            True,
            self.name,
            {
                "total_python_files": len(files),
                "mapped_files": len(file_maps),
                "focus_files": focus_files,
                "symbols": symbols,
                "terms": terms,
            },
            f"Mapped {len(file_maps)} Python files; selected {len(focus_files)} focus files",
            timer.latency_ms,
        )

    def _python_files(self, repo_path: Path) -> list[Path]:
        files: list[Path] = []
        for path in repo_path.rglob("*.py"):
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
        return files

    def _file_map(self, repo_path: Path, path: Path, terms: list[str]) -> dict[str, Any] | None:
        try:
            source = path.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            return None

        rel = relative_path(path, repo_path)
        symbols: list[dict[str, Any]] = []
        imports: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(self._symbol(node))
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names[:4])
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names[:4])

        score = self._score(rel, symbols, terms)
        return {
            "file": rel,
            "score": score,
            "symbol_count": len(symbols),
            "imports": imports[:12],
            "symbols": symbols[:24],
        }

    def _symbol(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> dict[str, Any]:
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        signature = node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [arg.arg for arg in node.args.args[:8]]
            signature = f"{node.name}({', '.join(args)})"
        return {
            "name": node.name,
            "kind": kind,
            "signature": signature,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
        }

    def _score(self, rel_file: str, symbols: list[dict[str, Any]], terms: list[str]) -> int:
        lower_file = rel_file.lower()
        lower_terms = [term.lower() for term in terms if len(term) >= 2]
        score = 0
        for term in lower_terms:
            if term in lower_file:
                score += 5
            for symbol in symbols:
                name = str(symbol.get("name", "")).lower()
                signature = str(symbol.get("signature", "")).lower()
                if term == name:
                    score += 8
                elif term in name:
                    score += 5
                elif term in signature:
                    score += 2
        return score
