"""Bounded source observations and syntactic argument binding, never runtime types.

Only one-hop ordinary Python functions are considered. Source literals and simple
straight-line assignments are supported; calls, operators and branch results are
unknown. No repository imports/eval, builtin semantics or interprocedural flow.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import asdict

from .context import ContextReader
from .records import ContextRequest, IndexQuery, Location
from .validation import ContractError


ASSUMPTIONS = [
    'Observations describe the supplied snapshot, not an executed program.',
    'Argument bindings assume the indexed module bindings are unchanged at runtime.',
    'Literal/assignment types do not establish callee return values or causal correctness.',
    'Unresolved index relations are missing static information, not runtime errors.',
]


def _expression_type(node, known):
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        return type(node).__name__.lower()
    if isinstance(node, ast.Name):
        return known.get(node.id, 'unknown')
    return 'unknown'


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _module_bindings(tree):
    """Conservative uniqueness guard, including conditional/repeated imports."""
    counts, conditional = Counter(), set()
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[statement.name] += 1
            continue
        for node in ast.walk(statement):
            names = []
            if isinstance(node, ast.Import):
                names = [a.asname or a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [a.asname or a.name for a in node.names]
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names = [node.id]
            for name in names:
                counts[name] += 1
                if not isinstance(statement, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)):
                    conditional.add(name)
    return counts, conditional


def _bind_arguments(call, function, known):
    args = function.args
    if args.vararg or args.kwarg or args.defaults or any(x is not None for x in args.kw_defaults):
        return None, 'signature_unsupported'
    if any(isinstance(x, ast.Starred) for x in call.args) or any(x.arg is None for x in call.keywords):
        return None, 'argument_expansion_unsupported'
    positional = [a.arg for a in args.posonlyargs + args.args]
    allowed_keywords = {a.arg for a in args.args + args.kwonlyargs}
    required = positional + [a.arg for a in args.kwonlyargs]
    if len(call.args) > len(positional):
        return None, 'argument_binding_unknown'
    values = dict(zip(positional, call.args))
    for keyword in call.keywords:
        if keyword.arg not in allowed_keywords or keyword.arg in values:
            return None, 'argument_binding_unknown'
        values[keyword.arg] = keyword.value
    if set(values) != set(required):
        return None, 'argument_binding_unknown'
    return [{'parameter': name, 'type': _expression_type(values[name], known),
             'type_basis': 'source_expression' if _expression_type(values[name], known) != 'unknown' else 'unknown'}
            for name in required], None


def collect_static_facts(bundle, index, seed_symbol_ids: list[str], *, max_context_bytes: int = 1200) -> dict:
    """Collect a checkable packet from an already-built, snapshot-bound index.

    The fixed six-function/one-hop limit also applies to explicit seeds. Whole
    captured files are parsed for scope/decorator guards, through read_verified;
    only ContextReader excerpts within the unchanged byte budget yield facts.
    """
    if (not isinstance(seed_symbol_ids, list) or not 1 <= len(seed_symbol_ids) <= 6
            or any(type(s) is not str for s in seed_symbol_ids)
            or len(set(seed_symbol_ids)) != len(seed_symbol_ids)
            or type(max_context_bytes) is not int or not 0 < max_context_bytes <= 32768):
        raise ContractError('input_invalid')
    canonical = index.query(bundle.ref, IndexQuery(symbol_ids=seed_symbol_ids, limit=50)).symbols
    by_id = {s.symbol_id: s for s in canonical if s.kind in ('function', 'async_function')}
    if set(by_id) != set(seed_symbol_ids):
        raise ContractError('input_invalid')
    seed_edges = index.query(bundle.ref, IndexQuery(kind='relations', symbol_ids=seed_symbol_ids, limit=50))
    neighbors = set()
    for edge in seed_edges.relations:
        if edge.relation_type in ('calls', 'tests') and edge.resolution in ('resolved_local', 'resolved_import'):
            neighbors.update([edge.source_id, edge.target_ref])
    neighbors.difference_update(seed_symbol_ids)
    if neighbors:
        page = index.query(bundle.ref, IndexQuery(symbol_ids=sorted(neighbors), limit=50))
        by_id.update({s.symbol_id: s for s in page.symbols if s.kind in ('function', 'async_function')})
    selected = [by_id[s] for s in seed_symbol_ids]
    extra = sorted((s for key, s in by_id.items() if key not in seed_symbol_ids), key=lambda s: (s.file, s.start, s.qualified_name))
    selected.extend(extra[:6-len(selected)])
    context = ContextReader(index.store, index).read(bundle.ref, ContextRequest(
        [Location(s.file, s.start, s.end, s.symbol_id) for s in selected],
        max_bytes=max_context_bytes, max_tokens=max_context_bytes))
    packet = {
        'schema_version': 1, 'mode': 'bounded_static_facts', 'snapshot_ref': asdict(bundle.ref),
        'symbols': [asdict(s) for s in selected], 'snippets': context.snippets,
        'evidence': [asdict(e) for e in context.evidence], 'facts': [], 'bindings': [], 'unknowns': [],
        'relations': [], 'truncations': context.truncations,
        'source_bytes': sum(len(s['text'].encode('utf-8')) for s in context.snippets),
        'limits': {'max_functions': 6, 'max_hops': 1, 'max_context_bytes': max_context_bytes, 'max_facts': 32},
        'assumptions': list(ASSUMPTIONS), 'data_flow_proven': False, 'repository_code_executed': False,
        'context_bytes_read': context.bytes_read, 'additional_guard_bytes_read': 0,
    }

    def unknown(reason, symbol_id=None, location=None):
        item = {'reason': reason, 'symbol_id': symbol_id, 'location': asdict(location) if location else None}
        if item not in packet['unknowns']:
            packet['unknowns'].append(item)

    if seed_edges.next_cursor is not None:
        unknown('relation_query_limit')
    if len(extra) > 6-len(seed_symbol_ids):
        unknown('function_limit')
    if context.truncations or len(context.snippets) != len(selected):
        unknown('context_truncated')
        index.store.verify(bundle.ref)
        return packet
    edges_page = index.query(bundle.ref, IndexQuery(kind='relations', symbol_ids=[s.symbol_id for s in selected], limit=50))
    packet['relations'] = [asdict(e) for e in edges_page.relations]
    if edges_page.next_cursor is not None:
        unknown('relation_query_limit')
    selected_ids = {s.symbol_id for s in selected}
    evidence = {snippet['location']['symbol_id']: record['evidence_id']
                for snippet, record in zip(context.snippets, packet['evidence'])}
    modules, functions, straight = {}, {}, {}
    for symbol in selected:
        if symbol.file not in modules:
            data = index.store.read_verified(bundle.ref, symbol.file)
            packet['additional_guard_bytes_read'] += index.store.last_usage.bytes_read
            tree = ast.parse(data, filename=symbol.file)
            modules[symbol.file] = (tree, *_module_bindings(tree))
        tree = modules[symbol.file][0]
        node = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == symbol.qualified_name and n.lineno == symbol.start), None)
        if node is None or node.decorator_list:
            unknown('ordinary_function_required', symbol.symbol_id)
            continue
        functions[symbol.symbol_id] = node
        simple_statements = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Expr, ast.Pass)
        dynamic_expressions = (ast.Yield, ast.YieldFrom, ast.Await, ast.Lambda, ast.ListComp,
                               ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.NamedExpr, ast.IfExp, ast.BoolOp)
        straight[symbol.symbol_id] = (all(isinstance(n, simple_statements) for n in node.body)
            and not any(isinstance(n, dynamic_expressions) for n in ast.walk(node)))
        if not straight[symbol.symbol_id]:
            unknown('control_flow_unsupported', symbol.symbol_id)

    def fact(symbol, kind, node, **values):
        if len(packet['facts']) >= 32:
            unknown('fact_limit')
            return
        packet['facts'].append({'fact_id': 'f' + str(len(packet['facts']) + 1), 'kind': kind,
            'name': symbol.qualified_name, 'symbol_id': symbol.symbol_id,
            'location': asdict(Location(symbol.file, node.lineno, node.end_lineno)),
            'evidence_ids': [evidence[symbol.symbol_id]], 'basis': 'source_syntax', **values})

    for symbol in selected:
        node = functions.get(symbol.symbol_id)
        if node is None:
            continue
        for literal in ast.walk(node):
            if isinstance(literal, ast.Constant):
                fact(symbol, 'literal_type', literal, type=type(literal.value).__name__)
        if not straight[symbol.symbol_id]:
            fact(symbol, 'return_type', node, type='unknown')
            continue
        known = {}
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                fact(symbol, 'annotation', statement, annotation=ast.unparse(statement.annotation), declared_only=True)
            for call in (n for n in ast.walk(statement) if isinstance(n, ast.Call)):
                location = Location(symbol.file, call.lineno, call.end_lineno)
                if seed_edges.next_cursor is not None or edges_page.next_cursor is not None:
                    unknown('relation_query_limit', symbol.symbol_id, location)
                    continue
                matching = [e for e in edges_page.relations if e.source_id == symbol.symbol_id
                            and e.relation_type in ('calls', 'tests')
                            and (e.location.start, e.location.end) == (call.lineno, call.end_lineno)]
                if len(matching) != 1:
                    unknown('call_location_ambiguous', symbol.symbol_id, location)
                    continue
                edge = matching[0]
                if edge.resolution not in ('resolved_local', 'resolved_import'):
                    unknown('relation_' + edge.resolution, symbol.symbol_id, location)
                    continue
                if edge.target_ref not in selected_ids:
                    unknown('outside_one_hop', symbol.symbol_id, location)
                    continue
                callee = functions.get(edge.target_ref)
                if callee is None:
                    unknown('ordinary_function_required', symbol.symbol_id, location)
                    continue
                counts, conditional = modules[symbol.file][1:]
                root_name = _root_name(call.func)
                if root_name is None or counts[root_name] != 1 or root_name in conditional:
                    unknown('alias_or_module_binding_ambiguous', symbol.symbol_id, location)
                    continue
                arguments, reason = _bind_arguments(call, callee, known)
                if reason:
                    unknown(reason, symbol.symbol_id, location)
                    continue
                packet['bindings'].append({'caller_id': symbol.symbol_id, 'callee_id': edge.target_ref,
                    'callee_name': callee.name, 'location': asdict(location), 'status': 'syntactic_binding',
                    'arguments': arguments, 'evidence_ids': [evidence[symbol.symbol_id], evidence[edge.target_ref]],
                    'assumptions': ['snapshot_module_bindings_unchanged', 'no_runtime_execution_observed']})
            value = None
            name = None
            if isinstance(statement, ast.Assign):
                if len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                    name, value = statement.targets[0].id, statement.value
                else:
                    known.clear()
                    unknown('assignment_unsupported', symbol.symbol_id)
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                name, value = statement.target.id, statement.value
            elif isinstance(statement, ast.AugAssign):
                known.clear()
                unknown('assignment_unsupported', symbol.symbol_id)
            if name is not None:
                value_type = _expression_type(value, known)
                known[name] = value_type
                fact(symbol, 'assignment_type', statement, name=name, type=value_type)
                if value_type == 'unknown':
                    unknown('call_result_unknown' if isinstance(value, ast.Call) else 'expression_type_unknown', symbol.symbol_id)
            if isinstance(statement, ast.Return):
                value_type = _expression_type(statement.value, known)
                fact(symbol, 'return_type', statement, type=value_type)
                if value_type == 'unknown':
                    unknown('call_result_unknown' if isinstance(statement.value, ast.Call) else 'expression_type_unknown', symbol.symbol_id)
    index.store.verify(bundle.ref)
    return packet
