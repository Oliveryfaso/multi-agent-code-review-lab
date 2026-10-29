"""Frozen fixed retrieval: bounded, syntax-only local calls, never task labels."""
from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from time import monotonic

from macr.evals.deepseek_policy_manifest import _digest
from macr.evals.deepseek_policy_tools import MAX_TERM_BYTES, StaticEvidenceTools, _call_name

SCHEMA = 'macr.deepseek.study.fixed-plan.v1'
VERSION = 'static-bfs-v1'


class _Module(ast.NodeVisitor):
    """Index lexical bindings; reassignment/duplicate names stay ambiguous."""

    def __init__(self, file, tree):
        self.file, self.scope = file, ''
        self.definitions = {}
        self.bindings = {'': {}}
        self.class_scopes = set()
        self.visit(tree)

    def bind(self, name, value):
        self.bindings.setdefault(self.scope, {}).setdefault(name, []).append(value)

    def _definition(self, node):
        name = '.'.join(filter(None, (self.scope, node.name)))
        self.bind(node.name, ('definition', name))
        self.definitions.setdefault(name, []).append(node)
        if isinstance(node, ast.ClassDef):
            self.class_scopes.add(name)
        previous, self.scope = self.scope, name
        self.bindings.setdefault(name, {})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            for argument in arguments:
                self.bind(argument.arg, ('unresolved',))
            for argument in (node.args.vararg, node.args.kwarg):
                if argument is not None:
                    self.bind(argument.arg, ('unresolved',))
        for child in node.body:
            self.visit(child)
        self.scope = previous

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, ('unresolved',))

    def visit_Import(self, node):
        for alias in node.names:
            # `import pkg.helper` binds pkg; `as h` binds the complete module.
            self.bind(alias.asname or alias.name.split('.')[0],
                      ('import', alias.name if alias.asname else alias.name.split('.')[0], ''))

    def visit_ImportFrom(self, node):
        package = list(PurePosixPath(self.file).parent.parts)
        if package == ['.']:
            package = []
        if node.level:
            if node.level > len(package) + 1:
                module = None
            else:
                module = '.'.join(package[:len(package) - node.level + 1] +
                                  (node.module.split('.') if node.module else []))
        else:
            module = node.module or ''
        for alias in node.names:
            if alias.name == '*':
                self.bind('*', ('unresolved',))
            else:
                self.bind(alias.asname or alias.name,
                          ('import', module, alias.name) if module is not None else ('unresolved',))

    def visit_Global(self, node):
        for name in node.names:
            self.bind(name, ('unresolved',))

    visit_Nonlocal = visit_Global

    def lookup(self, scope, name):
        while True:
            # A method closes over enclosing functions/module, not its class
            # namespace. Attribute lookup through self/cls remains unresolved.
            if scope not in self.class_scopes:
                values = self.bindings.get(scope, {}).get(name)
                if values is not None:
                    return values[0] if len(values) == 1 else ('unresolved',)
                if '*' in self.bindings.get(scope, {}):
                    return ('unresolved',)
            if not scope:
                return ('unresolved',)
            scope = scope.rpartition('.')[0]


class _Calls(ast.NodeVisitor):
    def __init__(self, node):
        self.calls = []
        for child in node.body:
            self.visit(child)

    def visit_Call(self, node):
        self.calls.append((_call_name(node.func), node.lineno))
        self.generic_visit(node)

    # Nested bodies are only visited if selected at the next BFS depth.
    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


def _module_name(file):
    parts = list(PurePosixPath(file).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def _target(modules, by_name, file, symbol, callee):
    if callee is None:
        return None
    pieces = callee.split('.')
    binding = modules[file].lookup(symbol, pieces[0])
    if binding[0] == 'definition' and len(pieces) == 1:
        candidate = (file, binding[1])
    elif binding[0] == 'import':
        _, module, imported = binding
        full = '.'.join(filter(None, (module, imported, *pieces[1:])))
        matches = []
        for name, candidates in by_name.items():
            if full.startswith(name + '.') and len(candidates) == 1:
                target_file = candidates[0]
                target_symbol = full[len(name) + 1:]
                if target_symbol in modules[target_file].definitions:
                    matches.append((target_file, target_symbol))
        if len(matches) != 1:
            return None
        candidate = matches[0]
    else:
        return None
    target_file, target_symbol = candidate
    nodes = modules[target_file].definitions.get(target_symbol, [])
    if (len(nodes) != 1 or not isinstance(nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef))
            or nodes[0].decorator_list):
        return None
    return candidate


def build_fixed_plan(manifest: dict, dataset_root: Path) -> dict:
    """Choose <=4 symbols by two-layer BFS, independent of model/task answers.

    The graph is a conservative lexical observation, not runtime binding proof.
    Only authorized, hash-verified files are parsed. No analyzed code is imported.
    Caller executes groups with the same StaticEvidenceTools used by D; this
    function neither emits invented tool calls nor changes evidence clipping.
    """
    started = monotonic()
    tools = StaticEvidenceTools(manifest, dataset_root)
    source = tools.source(list(tools.files))
    modules, by_name, unresolved = {}, {}, []
    scanned_bytes = sum(len(row['body'].encode('utf-8')) for row in source)
    for row in source:
        file = row['file']
        try:
            tree = ast.parse(row['body'], filename=file)
            modules[file] = _Module(file, tree)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            unresolved.append({'file': file, 'symbol': '<module>', 'callee': None,
                               'reason': 'python_source_invalid'})
            continue
        by_name.setdefault(_module_name(file), []).append(file)
    entry = manifest['task']['entrypoint']
    origin = (entry['file'], entry['symbol'])
    frontier, seen, selected = [origin], {origin}, []
    for distance in (1, 2):
        reached = set()
        for file, symbol in sorted(frontier):
            nodes = modules.get(file).definitions.get(symbol, []) if file in modules else []
            if len(nodes) != 1 or not isinstance(nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
                unresolved.append({'file': file, 'symbol': symbol, 'callee': None,
                                   'reason': 'entry_or_symbol_unresolved'})
                continue
            for callee, _line in _Calls(nodes[0]).calls:
                target = _target(modules, by_name, file, symbol, callee)
                if target is None:
                    unresolved.append({'file': file, 'symbol': symbol, 'callee': callee,
                                       'reason': 'unresolved_static_relation'})
                elif target not in seen:
                    reached.add(target)
        frontier = sorted(reached)
        seen.update(reached)
        for file, symbol in frontier:
            # read_symbol accepts a short name as a suffix match. Do not count
            # multiple resulting definitions as one selected symbol. Check the
            # existing AST index rather than performing hidden extra reads.
            matches = [name for name in modules[file].definitions
                       if name == symbol or ('.' not in symbol and name.split('.')[-1] == symbol)]
            if len(matches) != 1 or len(symbol.encode('utf-8')) > MAX_TERM_BYTES:
                unresolved.append({'file': file, 'symbol': symbol, 'callee': symbol,
                                   'reason': 'read_symbol_ambiguous'})
                continue
            selected.append({'distance': distance, 'file': file, 'symbol': symbol})
        # Traversal stays at two levels even when >4 reachable symbols exist.
    selected = sorted(selected, key=lambda row: (row['distance'], row['file'], row['symbol']))[:4]
    actions = [{'name': 'read_symbol', 'arguments': {'file': row['file'], 'symbol': row['symbol']}}
               for row in selected]
    distinct = {(row['file'], row['symbol'], row['callee'], row['reason']): row for row in unresolved}
    unresolved = sorted(distinct.values(), key=lambda row:
                        (row['file'], row['symbol'], row['callee'] or '', row['reason']))
    packet = {'schema': SCHEMA, 'version': VERSION, 'groups': [actions[:2], actions[2:]],
              'symbols': selected, 'unresolved': unresolved, 'scanned_bytes': scanned_bytes,
              'source_sha256': manifest['snapshot']['sha256']}
    packet['sha256'] = _digest(packet)
    packet['elapsed_seconds'] = round(monotonic() - started, 6)
    return packet
