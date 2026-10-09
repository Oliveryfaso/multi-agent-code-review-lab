from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from macr.investigation.index import build_static_view
from macr.investigation.records import IndexQuery
from macr.investigation.scope import resolve_allowed
from macr.investigation.validation import ContractError
from macr.schemas import ToolResult
from macr.tools.timing import Timer


class CodeGraphTool:
    name = "code_graph"

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir

    def run(self, repo_path: Path, terms: list[str], candidate_files: list[str] | None = None, *, scope=None) -> ToolResult:
        with Timer() as timer:
            try:
                bundle, index, summary = build_static_view(repo_path, scope, self.cache_dir)
                for path in candidate_files or []:
                    resolve_allowed(repo_path, path, bundle.scope)
                symbols = index.symbols
                matched = index.query(bundle.ref, IndexQuery(terms=terms, limit=max(1, len(symbols)))).symbols
            except (ContractError, OSError) as exc:
                return ToolResult(False, self.name, {}, "Static index unavailable", timer.elapsed_ms(), getattr(exc, "code", "storage_unavailable"))
            by_id = {s.symbol_id: s for s in symbols}
            nodes = [{"id": s.symbol_id, "kind": s.kind, "file": s.file, "label": s.file if s.kind == "file" else s.qualified_name,
                      "symbol": s.qualified_name, "line_start": s.start, "line_end": s.end} for s in symbols]
            edges = [{"from": e.source_id, "to": e.target_ref, "kind": e.relation_type, "resolution": e.resolution,
                      "from_symbol": by_id[e.source_id].qualified_name, "to_symbol": by_id[e.target_ref].qualified_name if e.target_ref in by_id else e.target_ref,
                      "file": e.location.file, "line": e.location.start} for e in index.relations]
            neighborhoods = [{"symbol": s.qualified_name, "symbol_id": s.symbol_id, "file": s.file,
                              "definitions": [{"file": s.file, "line_start": s.start, "line_end": s.end, "kind": s.kind}],
                              "outgoing": [e for e in edges if e["from"] == s.symbol_id], "incoming": [e for e in edges if e["to"] == s.symbol_id]} for s in matched]
        data = {"nodes": nodes[:300], "edges": edges[:600], "matched_symbols": [s.qualified_name for s in matched[:30]], "neighborhoods": neighborhoods[:30],
                "node_count": len(nodes), "edge_count": len(edges), "cache": index.cache, "scope_file_count": len([f for f in bundle.scope.eligible if f.language == "python"]),
                "truncated": len(nodes)>300 or len(edges)>600 or len(matched)>30, "coverage": asdict(summary.coverage), "errors": [asdict(e) for e in summary.errors], "legacy": True}
        return ToolResult(True, self.name, data, f"Indexed {len(nodes)} qualified nodes", timer.latency_ms)
