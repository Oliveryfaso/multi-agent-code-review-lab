from __future__ import annotations
from pathlib import Path
from macr.investigation.scope import RepositoryScope
from macr.investigation.records import ScopeManifest, ScopeProfile, SnapshotBundle
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError
from macr.schemas import ToolResult
from macr.tools.timing import Timer

class TextSearchTool:
    name = 'search_text'

    def run(self, repo_path: Path, query: str, max_results: int = 20, *, scope: ScopeManifest | None = None, snapshot: SnapshotBundle | None = None, store: SnapshotStore | None = None) -> ToolResult:
        with Timer() as timer:
            if type(max_results) is not int or not 1 <= max_results <= 1000 or not isinstance(query, str) or not query or '\x00' in query:
                return ToolResult(False, self.name, {"matches": []}, "Search request rejected", timer.elapsed_ms(), "contract_invalid")
            try:
                store = store or SnapshotStore()
                snapshot = snapshot or store.capture(repo_path, scope or RepositoryScope().inventory(repo_path, ScopeProfile()))
                matches, errors, read, truncated = [], [], 0, False
                for entry in snapshot.scope.eligible:
                    data = store.read_verified(snapshot.ref, entry.path)
                    read += len(data)
                    try:
                        text = data.decode('utf-8')
                    except UnicodeError:
                        errors.append({"file": entry.path, "code": "encoding_error"})
                        continue
                    for line, body in enumerate(text.splitlines(), 1):
                        if query in body:
                            matches.append({"file": entry.path, "line": line, "text": body.strip()[:400]})
                            if len(matches) >= max_results:
                                truncated = True
                                break
                    if truncated:
                        break
                return ToolResult(bool(matches), self.name, {"query": query, "matches": matches, "truncated": truncated, "errors": errors, "bytes_read": read, "content_id": snapshot.ref.content_id, "legacy": True}, "Searched verified literal bytes", timer.elapsed_ms(), None if matches else "empty_recall")
            except (ContractError, OSError) as exc:
                return ToolResult(False, self.name, {"matches": []}, "Search snapshot rejected", timer.elapsed_ms(), getattr(exc, 'code', 'storage_unavailable'))
