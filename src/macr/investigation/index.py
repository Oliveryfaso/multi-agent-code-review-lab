"""Static qualified index. Resolution labels never claim runtime execution."""
from __future__ import annotations

import ast
import json
import hashlib
from dataclasses import asdict
from pathlib import Path

from .records import ActionUsage, CoverageRecord, IndexPage, IndexQuery, IndexSummary, Location, RelationRecord, RunError, SnapshotBundle, SnapshotRef, SymbolRecord
from .snapshots import SnapshotError, SnapshotStore
from .validation import ContractError, decode_record
from .scope import read_safe

PARSER_VERSION = "qualified-ast-v2"


def module_name(relative: str) -> str:
    name = relative[:-3].replace("/", ".")
    return name[:-9] if name.endswith(".__init__") else name


class _Collector(ast.NodeVisitor):
    def __init__(self, relative: str, content_id: str, tree: ast.AST, lines: int):
        self.file, self.module, self.content_id = relative, module_name(relative), content_id
        self.stack, self.symbols, self.calls, self.imports = [], [], [], []
        self.aliases, self.bound, self.decorated = {}, {}, set()
        self.module_symbol = self._symbol("<module>", "file", 1, max(1, lines), "")
        self.visit(tree)

    def _symbol(self, qualified, kind, start, end, signature):
        symbol = SymbolRecord(f"{self.content_id}:{self.file}:{qualified}:{start}", self.file, self.module, qualified, kind, start, end, signature)
        self.symbols.append(symbol)
        return symbol

    def prefix(self):
        return self.stack[-1][0] if self.stack else ""

    def source(self):
        return self.stack[-1][2] if self.stack else self.module_symbol.symbol_id

    def visit_ClassDef(self, node):
        qualified = ".".join(filter(None, (self.prefix(), node.name)))
        symbol = self._symbol(qualified, "class", node.lineno, node.end_lineno, node.name)
        for expression in node.bases + node.decorator_list:
            self.visit(expression)
        self.stack.append((qualified, "class", symbol.symbol_id))
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._function(node, "function")

    def visit_AsyncFunctionDef(self, node):
        self._function(node, "async_function")

    def _function(self, node, kind):
        qualified = ".".join(filter(None, (self.prefix(), node.name)))
        args = [arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
        if node.args.vararg:
            args.append("*" + node.args.vararg.arg)
        if node.args.kwarg:
            args.append("**" + node.args.kwarg.arg)
        symbol = self._symbol(qualified, kind, node.lineno, node.end_lineno, f"{node.name}({', '.join(args)})")
        if node.decorator_list:
            self.decorated.add(symbol.symbol_id)
        # Defaults/decorators belong to the enclosing definition environment.
        for expression in node.args.defaults + [x for x in node.args.kw_defaults if x] + node.decorator_list:
            self.visit(expression)
        self.bound[qualified] = {name.lstrip("*") for name in args}
        self.stack.append((qualified, "function", symbol.symbol_id))
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    def visit_Assign(self, node):
        self.bound.setdefault(self.prefix(), set()).update(n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name))
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self.bound.setdefault(self.prefix(), set()).update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node):
        self.bound.setdefault(self.prefix(), set()).update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
        self.visit(node.value)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.setdefault(self.prefix(), set()).add(node.id)

    def _dynamic_scope(self, node):
        qualified = f'{self.prefix()}.<dynamic>@{node.lineno}:{node.col_offset}'
        self.stack.append((qualified, 'dynamic_scope', self.source()))
        self.generic_visit(node)
        self.stack.pop()

    visit_Lambda = visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _dynamic_scope

    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.aliases.setdefault(self.prefix(), {})[local] = alias.name if alias.asname else alias.name.split(".")[0]
            self.imports.append((self.source(), alias.name, node.lineno))

    def visit_ImportFrom(self, node):
        base = node.module or ""
        if node.level:
            package = self.module.split(".") if self.file.endswith("/__init__.py") else self.module.split(".")[:-1]
            if node.level > len(package) + 1:
                base = "<unknown-relative>"
            else:
                base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
        for alias in node.names:
            target = ".".join(filter(None, (base, alias.name)))
            self.aliases.setdefault(self.prefix(), {})[alias.asname or alias.name] = target
            self.imports.append((self.source(), target, node.lineno))

    def visit_Call(self, node):
        self.calls.append((self.source(), list(self.stack), node))
        self.generic_visit(node)


def _call_parts(node):
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        prefix = _call_parts(node.value)
        return prefix + [node.attr] if prefix else None
    return None


class RepositoryIndex:
    def __init__(self, store: SnapshotStore, cache_root: Path | None = None):
        self.store = store
        allowed = (Path(__file__).resolve().parents[3] / "artifacts").resolve()
        self.cache_root = (cache_root or allowed / "index").resolve()
        if not self.cache_root.is_relative_to(allowed):
            raise ContractError("forbidden_path", "index_cache")
        self.symbols, self.relations, self.cache = [], [], {}
        self.content_id = None
        self.last_usage = ActionUsage()

    def build(self, bundle: SnapshotBundle) -> IndexSummary:
        self.store.verify(bundle.ref)
        self.last_usage = ActionUsage(tool_calls=1, bytes_read=self.store.last_usage.bytes_read)
        python_files = [f for f in bundle.scope.eligible if f.language == "python"]
        path = self.cache_root / f"{bundle.ref.content_id}-{PARSER_VERSION}.json"
        self.cache = {"hit": False, "path": str(path), "fingerprint": bundle.ref.content_id, "indexed_file_count": len(python_files)}
        errors = []
        try:
            raw_cache = read_safe(path.parent, path.name, 64*1024*1024)
            self.last_usage.bytes_read += len(raw_cache)
            cached = json.loads(raw_cache)
            checksum = cached.pop('payload_sha256')
            if hashlib.sha256(json.dumps(cached, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest() != checksum:
                raise ValueError('cache integrity')
            if cached["schema_version"] != 1 or cached["parser"] != PARSER_VERSION or cached["content_id"] != bundle.ref.content_id:
                raise ValueError("cache version")
            self.symbols = [decode_record(SymbolRecord, raw) for raw in cached["symbols"]]
            self.relations = [decode_record(RelationRecord, raw) for raw in cached["relations"]]
            errors = [decode_record(RunError, raw) for raw in cached["errors"]]
            file_bounds = {f.path: f.lines or 0 for f in python_files}
            ids = {s.symbol_id for s in self.symbols}
            if len(ids) != len(self.symbols) or any(s.file not in file_bounds or s.end > max(1, file_bounds[s.file]) or s.symbol_id != f'{bundle.ref.content_id}:{s.file}:{s.qualified_name}:{s.start}' for s in self.symbols):
                raise ValueError('cache source binding')
            if any(e.source_id not in ids or e.location.file not in file_bounds or e.resolution in ('resolved_local', 'resolved_import') and e.target_ref not in ids for e in self.relations) or any(e.code != 'parse_error' or e.detail not in file_bounds for e in errors):
                raise ValueError('cache relation binding')
            self.cache["hit"] = True
        except (OSError, ValueError, KeyError, TypeError, ContractError):
            collectors = []
            for item in python_files:
                data = self.store.read_verified(bundle.ref, item.path)
                self.last_usage.bytes_read += self.store.last_usage.bytes_read
                try:
                    collectors.append(_Collector(item.path, bundle.ref.content_id, ast.parse(data), len(data.splitlines())))
                except (SyntaxError, UnicodeError):
                    errors.append(RunError("parse_error", item.path))
            self.symbols = [symbol for collector in collectors for symbol in collector.symbols]
            self.relations = self._relations(collectors)
            payload = {"schema_version": 1, "parser": PARSER_VERSION, "content_id": bundle.ref.content_id,
                       "symbols": [asdict(s) for s in self.symbols], "relations": [asdict(e) for e in self.relations], "errors": [asdict(e) for e in errors]}
            # Integrity for derived cache bytes only, not a claim of cryptographic authenticity.
            payload['payload_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, sort_keys=True, allow_nan=False), encoding="utf-8")
            except OSError:
                raise ContractError("storage_unavailable", "index_cache") from None
        self.content_id = bundle.ref.content_id
        coverage = CoverageRecord(inventory=sum(len(getattr(bundle.scope, name)) for name in ("eligible", "excluded", "unsupported", "unreadable")),
                                  eligible=len(bundle.scope.eligible), indexed=len(bundle.scope.eligible), parsed=len(python_files) - len(errors), errors=errors)
        return IndexSummary(bundle.ref.content_id, coverage, errors, self.last_usage)

    def _relations(self, collectors):
        by_name, modules = {}, {}
        decorated = {identifier for c in collectors for identifier in c.decorated}
        for symbol in self.symbols:
            by_name.setdefault((symbol.file, symbol.qualified_name), []).append(symbol)
            if symbol.kind == "file":
                modules.setdefault(symbol.module, []).append(symbol)
                if symbol.module.startswith("src."):
                    modules.setdefault(symbol.module[4:], []).append(symbol)

        def imported(target):
            if target in modules and len(modules[target]) == 1:
                return modules[target][0].symbol_id
            for prefix in sorted(modules, key=len, reverse=True):
                if target.startswith(prefix + ".") and len(modules[prefix]) == 1:
                    candidates = by_name.get((modules[prefix][0].file, target[len(prefix)+1:]), [])
                    if len(candidates) == 1:
                        return candidates[0].symbol_id
            return None

        edges = []
        for collector in collectors:
            for source, target, line in collector.imports:
                destination = imported(target)
                edges.append(RelationRecord(source, destination or target, "imports", Location(collector.file, line, line), "resolved_import" if destination else "unresolved"))
            for source, stack, call in collector.calls:
                parts = _call_parts(call.func)
                destination, level = None, "unresolved"
                if parts and not any(kind == 'dynamic_scope' for _, kind, _ in stack):
                    prefixes = [qual for qual, kind, _ in reversed(stack) if kind == "function"]
                    if stack and stack[-1][1] == "class":
                        prefixes.insert(0, stack[-1][0])
                    prefixes.append("")
                    for prefix in prefixes:
                        if parts[0] in collector.bound.get(prefix, set()):
                            break
                        alias = collector.aliases.get(prefix, {}).get(parts[0])
                        if alias:
                            destination = imported(".".join([alias] + parts[1:]))
                            level = "resolved_import" if destination else "unresolved"
                            break
                        if len(parts) == 1:
                            qualified = ".".join(filter(None, (prefix, parts[0])))
                            candidates = by_name.get((collector.file, qualified), [])
                            if len(candidates) == 1:
                                destination, level = candidates[0].symbol_id, "resolved_local"
                                break
                            if candidates:
                                break
                    if destination in decorated:
                        level = "syntactic_candidate"
                target = destination or ".".join(parts or ["<dynamic>"])
                kind = "tests" if "tests" in Path(collector.file).parts or Path(collector.file).name.startswith("test_") else "calls"
                edges.append(RelationRecord(source, target, kind, Location(collector.file, call.lineno, call.end_lineno), level))
        return edges

    def query(self, ref: SnapshotRef, query: IndexQuery) -> IndexPage:
        query = decode_record(IndexQuery, asdict(query))
        self.store.verify(ref)
        self.last_usage = ActionUsage(tool_calls=1, bytes_read=self.store.last_usage.bytes_read)
        if ref.content_id != self.content_id:
            raise SnapshotError("stale_snapshot", "index_content")
        if query.kind == "symbols":
            items = [s for s in self.symbols if (not query.symbol_ids or s.symbol_id in query.symbol_ids)
                     and (not query.terms or any(t.casefold() in s.qualified_name.casefold() or t.casefold() in s.file.casefold() for t in query.terms))]
        else:
            items = [e for e in self.relations if not query.symbol_ids or e.source_id in query.symbol_ids or e.target_ref in query.symbol_ids]
        end = query.cursor + query.limit
        page = items[query.cursor:end]
        return IndexPage(symbols=page if query.kind == "symbols" else [], relations=page if query.kind == "relations" else [], next_cursor=end if end < len(items) else None)


def build_static_view(root: Path, scope=None, cache_root=None):
    """Legacy facade adapter uses the same sanitized, content-bound index."""
    from .records import ScopeProfile
    from .scope import RepositoryScope
    scope = scope or RepositoryScope().inventory(root, ScopeProfile())
    store = SnapshotStore()
    bundle = store.capture(root, scope)
    index = RepositoryIndex(store, cache_root)
    summary = index.build(bundle)
    return bundle, index, summary
