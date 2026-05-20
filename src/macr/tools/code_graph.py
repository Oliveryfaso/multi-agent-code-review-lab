from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from macr.schemas import ToolResult
from macr.tools.timing import Timer


class CodeGraphTool:
    name = "code_graph"

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.cwd() / ".macr_cache" / "code_graph"

    def run(self, repo_path: Path, terms: list[str], candidate_files: list[str] | None = None) -> ToolResult:
        with Timer() as timer:
            graph, cache_info = self._load_or_build(repo_path)
            nodes = graph["nodes"]
            edges = graph["edges"]
            definitions = graph["definitions"]
            matched_symbols = self._matched_symbols(terms, definitions)
            neighborhoods = self._neighborhoods(matched_symbols, definitions, edges)
        return ToolResult(
            True,
            self.name,
            {
                "nodes": list(nodes.values())[:300],
                "edges": edges[:600],
                "matched_symbols": matched_symbols[:30],
                "neighborhoods": neighborhoods[:30],
                "node_count": len(nodes),
                "edge_count": len(edges),
                "cache": cache_info,
            },
            f"{'Loaded' if cache_info['hit'] else 'Built'} code graph with {len(nodes)} nodes and {len(edges)} edges",
            timer.latency_ms,
        )

    def _load_or_build(self, repo_path: Path) -> tuple[dict, dict]:
        repo_path = repo_path.resolve()
        files = sorted(repo_path.rglob("*.py"))[:1200]
        fingerprint = self._fingerprint(repo_path, files)
        cache_path = self._cache_path(repo_path)
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("fingerprint") == fingerprint:
                return cached["graph"], {
                    "hit": True,
                    "path": str(cache_path),
                    "fingerprint": fingerprint,
                    "indexed_file_count": cached.get("indexed_file_count", 0),
                }
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        graph = self._build_graph(repo_path, files)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_path.write_text(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "indexed_file_count": len(files),
                        "graph": graph,
                    },
                    ensure_ascii=False,
                )
            )
        except OSError:
            pass
        return graph, {
            "hit": False,
            "path": str(cache_path),
            "fingerprint": fingerprint,
            "indexed_file_count": len(files),
        }

    def _build_graph(self, repo_path: Path, files: list[Path]) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        definitions: dict[str, list[dict]] = {}
        for path in files:
            try:
                source = path.read_text()
                tree = ast.parse(source)
                rel = str(path.relative_to(repo_path))
            except (OSError, SyntaxError, ValueError):
                continue
            module_id = f"file:{rel}"
            nodes[module_id] = {"id": module_id, "kind": "file", "file": rel, "label": rel}
            visitor = _GraphVisitor(rel, nodes, edges, definitions)
            visitor.visit(tree)
        return {"nodes": nodes, "edges": edges, "definitions": definitions}

    def _fingerprint(self, repo_path: Path, files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in files:
            try:
                stat = path.stat()
                rel = str(path.relative_to(repo_path))
            except (OSError, ValueError):
                continue
            digest.update(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}\n".encode("utf-8"))
        return digest.hexdigest()

    def _cache_path(self, repo_path: Path) -> Path:
        key = hashlib.sha256(str(repo_path.resolve()).encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{key}.json"

    def _matched_symbols(self, terms: list[str], definitions: dict[str, list[dict]]) -> list[str]:
        lower_terms = [term.lower() for term in terms if len(term) >= 2]
        matched: list[str] = []
        for symbol in sorted(definitions):
            if not lower_terms or any(term in symbol.lower() for term in lower_terms):
                matched.append(symbol)
        return matched

    def _neighborhoods(self, symbols: list[str], definitions: dict[str, list[dict]], edges: list[dict]) -> list[dict]:
        neighborhoods: list[dict] = []
        for symbol in symbols:
            defs = definitions.get(symbol, [])
            incoming = [edge for edge in edges if edge.get("to_symbol") == symbol][:20]
            outgoing = [edge for edge in edges if edge.get("from_symbol") == symbol][:20]
            related_symbols = sorted(
                {
                    edge.get("from_symbol")
                    for edge in incoming
                    if edge.get("from_symbol") and edge.get("from_symbol") != symbol
                }
                | {
                    edge.get("to_symbol")
                    for edge in outgoing
                    if edge.get("to_symbol") and edge.get("to_symbol") != symbol
                }
            )
            neighborhoods.append(
                {
                    "symbol": symbol,
                    "definitions": defs,
                    "incoming": incoming,
                    "outgoing": outgoing,
                    "related_symbols": related_symbols,
                }
            )
        return neighborhoods


class _GraphVisitor(ast.NodeVisitor):
    def __init__(self, rel_file: str, nodes: dict[str, dict], edges: list[dict], definitions: dict[str, list[dict]]) -> None:
        self.rel_file = rel_file
        self.nodes = nodes
        self.edges = edges
        self.definitions = definitions
        self.scope_stack: list[dict] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._edge("file", self.rel_file, "imports", alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            imported = f"{module}.{alias.name}" if module else alias.name
            self._edge("file", self.rel_file, "imports", imported, node.lineno, to_symbol=alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node.name, "function", node.lineno, getattr(node, "end_lineno", node.lineno))
        self.scope_stack.append({"symbol": node.name, "kind": "function", "line": node.lineno})
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node.name, "class", node.lineno, getattr(node, "end_lineno", node.lineno))
        self.scope_stack.append({"symbol": node.name, "kind": "class", "line": node.lineno})
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._call_name(node.func)
        current = self.scope_stack[-1]["symbol"] if self.scope_stack else self.rel_file
        current_kind = self.scope_stack[-1]["kind"] if self.scope_stack else "file"
        if callee:
            relation = "tests" if self.rel_file.startswith("tests/") and current.startswith("test_") else "calls"
            self._edge(current_kind, current, relation, callee, node.lineno, from_symbol=current, to_symbol=callee)
        self.generic_visit(node)

    def _definition(self, name: str, kind: str, line_start: int, line_end: int) -> None:
        node_id = f"{kind}:{self.rel_file}:{name}"
        self.nodes[node_id] = {
            "id": node_id,
            "kind": kind,
            "file": self.rel_file,
            "label": name,
            "line_start": line_start,
            "line_end": line_end,
        }
        self.definitions.setdefault(name, []).append(
            {
                "file": self.rel_file,
                "line_start": line_start,
                "line_end": line_end,
                "kind": kind,
            }
        )
        self._edge("file", self.rel_file, "defines", name, line_start, to_symbol=name)

    def _edge(
        self,
        from_kind: str,
        from_label: str,
        relation: str,
        to_label: str,
        line: int,
        from_symbol: str | None = None,
        to_symbol: str | None = None,
    ) -> None:
        self.edges.append(
            {
                "from_kind": from_kind,
                "from": from_label,
                "relation": relation,
                "to": to_label,
                "file": self.rel_file,
                "line": line,
                "from_symbol": from_symbol,
                "to_symbol": to_symbol,
            }
        )

    def _call_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None
