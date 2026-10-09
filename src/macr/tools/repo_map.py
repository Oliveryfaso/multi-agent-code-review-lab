from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from macr.investigation.index import build_static_view
from macr.investigation.scope import EXCLUDED_DIRS
from macr.investigation.validation import ContractError
from macr.schemas import ToolResult
from macr.tools.timing import Timer


class RepoMapTool:
    name = "repo_map"
    SKIP_DIRS = EXCLUDED_DIRS

    def run(self, repo_path: Path, terms: list[str], max_files: int = 80, max_symbols: int = 160, *, scope=None) -> ToolResult:
        with Timer() as timer:
            try:
                if type(max_files) is not int or type(max_symbols) is not int or min(max_files, max_symbols) <= 0:
                    raise ContractError(field="result_limit")
                bundle, index, summary = build_static_view(repo_path, scope)
            except (ContractError, OSError) as exc:
                return ToolResult(False, self.name, {}, "Static index unavailable", timer.elapsed_ms(), getattr(exc, "code", "storage_unavailable"))
            files = []
            for entry in bundle.scope.eligible:
                if entry.language != "python":
                    continue
                symbols = [{"name": s.qualified_name.split(".")[-1], "qualified_name": s.qualified_name, "symbol_id": s.symbol_id, "kind": s.kind,
                            "signature": s.signature, "line_start": s.start, "line_end": s.end} for s in index.symbols if s.file == entry.path and s.kind != "file"]
                score = sum(5 if term.casefold() in entry.path.casefold() else 0 for term in terms)
                score += sum(8 if term.casefold() == symbol["name"].casefold() else 5 if term.casefold() in symbol["qualified_name"].casefold() else 0 for term in terms for symbol in symbols)
                files.append({"file": entry.path, "score": score, "symbol_count": len(symbols), "symbols": symbols, "imports": []})
            files.sort(key=lambda f: (-f["score"], f["file"]))
            focus = [f for f in files if f["score"]>0][:max_files] or files[:min(12, max_files)]
            symbols = [s for f in focus for s in f["symbols"]]
        return ToolResult(True, self.name, {"total_python_files": len(files), "scope_file_count": len(files), "mapped_files": summary.coverage.parsed,
                          "focus_files": focus, "symbols": symbols[:max_symbols], "terms": terms, "truncated": len(files)>len(focus) or len(symbols)>max_symbols,
                          "coverage": asdict(summary.coverage), "errors": [asdict(e) for e in summary.errors], "legacy": True}, f"Indexed {len(files)} Python files", timer.latency_ms)
