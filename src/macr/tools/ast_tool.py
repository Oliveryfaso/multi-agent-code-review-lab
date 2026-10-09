from __future__ import annotations
import ast
from pathlib import Path
from macr.schemas import ToolResult
from macr.tools.timing import Timer
from macr.investigation.scope import RepositoryScope
from macr.investigation.records import ScopeManifest, ScopeProfile, SnapshotBundle
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.index import RepositoryIndex
from macr.investigation.validation import ContractError, validate_relative

class PythonAstTool:
    name = 'parse_ast'

    def run(self, repo_path: Path, rel_file: str, terms: list[str], focus_lines: list[int] | None = None, *, scope: ScopeManifest | None = None, snapshot: SnapshotBundle | None = None, store: SnapshotStore | None = None) -> ToolResult:
        with Timer() as timer:
            try:
                validate_relative(rel_file)
                store = store or SnapshotStore()
                snapshot = snapshot or store.capture(repo_path, scope or RepositoryScope().inventory(repo_path, ScopeProfile()))
                source = store.read_verified(snapshot.ref, rel_file)
                tree = ast.parse(source)
                index = RepositoryIndex(store)
                summary = index.build(snapshot)
                lower = [term.lower() for term in terms]
                focus = focus_lines or []
                symbols = [{"name": s.qualified_name.rsplit('.', 1)[-1], "qualified_name": s.qualified_name, "symbol_id": s.symbol_id, "kind": s.kind, "line_start": s.start, "line_end": s.end, "matched_by": "focus_line" if any(s.start <= line <= s.end for line in focus) else "term"} for s in index.symbols if s.file == rel_file and s.kind != 'file' and (not lower or any(t in s.qualified_name.lower() for t in lower) or any(s.start <= line <= s.end for line in focus))]
                imports = [{"module": alias.name, "line": node.lineno} for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
                return ToolResult(True, self.name, {"file": rel_file, "symbols": symbols, "imports": imports[:20], "truncated": len(imports) > 20, "content_id": snapshot.ref.content_id, "legacy": True}, "Parsed verified bytes", timer.elapsed_ms())
            except ContractError as exc:
                return ToolResult(False, self.name, {}, "AST request rejected", timer.elapsed_ms(), exc.code)
            except (OSError, SyntaxError, UnicodeError):
                return ToolResult(False, self.name, {}, "AST parse failed", timer.elapsed_ms(), "parse_error")
