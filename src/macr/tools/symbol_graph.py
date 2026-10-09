from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from macr.investigation.index import build_static_view
from macr.investigation.records import IndexQuery
from macr.investigation.scope import resolve_allowed
from macr.investigation.validation import ContractError
from macr.schemas import ToolResult
from macr.tools.timing import Timer


class SymbolGraphTool:
    name = "symbol_graph"

    def run(self, repo_path: Path, terms: list[str], candidate_files: list[str] | None = None, *, scope=None) -> ToolResult:
        with Timer() as timer:
            try:
                bundle, index, summary = build_static_view(repo_path, scope)
                for path in candidate_files or []:
                    resolve_allowed(repo_path, path, bundle.scope)
                matched = index.query(bundle.ref, IndexQuery(terms=terms, limit=max(1, len(index.symbols)))).symbols
            except (ContractError, OSError) as exc:
                return ToolResult(False, self.name, {}, "Static index unavailable", timer.elapsed_ms(), getattr(exc, "code", "storage_unavailable"))
            matches = [{"symbol": s.qualified_name, "symbol_id": s.symbol_id,
                        "definitions": [{"file": s.file, "line_start": s.start, "line_end": s.end, "kind": s.kind}],
                        "references": [{"file": e.location.file, "line": e.location.start, "resolution": e.resolution, "target_id": e.target_ref} for e in index.relations if e.target_ref == s.symbol_id]} for s in matched if s.kind != "file"]
        return ToolResult(True, self.name, {"matched_symbols": matches[:30], "definition_count": len([s for s in index.symbols if s.kind != "file"]),
                          "reference_symbol_count": len({e.target_ref for e in index.relations}), "scope_file_count": len([f for f in bundle.scope.eligible if f.language == "python"]),
                          "truncated": len(matches)>30, "coverage": asdict(summary.coverage), "errors": [asdict(e) for e in summary.errors], "legacy": True},
                          f"Indexed {len(matches)} qualified definitions", timer.latency_ms)
