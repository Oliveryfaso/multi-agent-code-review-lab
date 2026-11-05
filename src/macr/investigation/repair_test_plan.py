"""Frozen pytest selectors bound to static declarations, never runtime collection.

No pytest, fixture, decorator or target imports/execution occur here. Parameter
IDs remain caller requests whose exact collected identity needs a real backend.
"""
from __future__ import annotations

import ast

from .repair_edits import MAX_PATH_BYTES
from .repair_io import _syntax, utf8_size
from .validation import ContractError, validate_relative


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _require(ok, field):
    if not ok:
        raise ContractError('repair_test_plan_invalid', field)


def _path(path):
    _require(type(path) is str and utf8_size(path) <= MAX_PATH_BYTES
             and path.endswith('.py') and not any(c in path for c in '*?[];'), 'test_path')
    validate_relative(path)
    return path


def _selector(nodeid, *, declared_names=False):
    _require(type(nodeid) is str and 0 < utf8_size(nodeid) <= 1024
             and not nodeid.startswith('-') and not any(c in nodeid for c in '\x00\r\n;'), 'nodeid')
    base, bracket, suffix = nodeid.partition('[')
    _require((']' not in base) and (not bracket or suffix.endswith(']')
             and '[' not in suffix and ']' not in suffix[:-1]), 'nodeid')
    parts = base.split('::')
    _require(len(parts) in (2, 3), 'nodeid')
    path = _path(parts[0])
    names = tuple(parts[1:])
    _require(all(name.isidentifier() for name in names)
             and (declared_names or names[-1].startswith('test_')
                  and (len(names) == 1 or names[0].startswith('Test'))), 'nodeid')
    return path, names, bool(bracket)


def validate_plan_shape(plan):
    """Validate a frozen selector contract without accessing source files."""
    _require(type(plan) is dict and set(plan) == {'schema', 'framework', 'nodeids'}, 'test_plan_fields')
    _require(plan['schema'] in ('macr.repair.test-plan.v1','macr.repair.test-plan.v2')
             and plan['framework'] == 'pytest', 'test_plan_schema')
    nodeids = plan['nodeids']
    _require(type(nodeids) is list and 1 <= len(nodeids) <= 32
             and all(type(nodeid) is str for nodeid in nodeids)
             and len(set(nodeids)) == len(nodeids), 'test_nodeids')
    for nodeid in nodeids:
        _selector(nodeid,declared_names=plan['schema']=='macr.repair.test-plan.v2')
    return plan


def _bindings(node):
    if isinstance(node, FUNCTIONS + (ast.ClassDef,)):
        return {node.name}
    if isinstance(node, ast.Lambda): return set()
    if isinstance(node, ast.comprehension):
        return set().union(_bindings(node.iter), *(_bindings(item) for item in node.ifs))
    result = set()
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        result.add(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        value = node.value
        while isinstance(value, ast.Attribute): value = value.value
        if isinstance(value, ast.Name): result.add(value.id)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        result.update(item.asname or item.name.split('.')[0] for item in node.names)
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        result.add(node.name)
    if isinstance(node, ast.MatchMapping) and node.rest: result.add(node.rest)
    for child in ast.iter_child_nodes(node): result.update(_bindings(child))
    return result


def _declaration(body, name, kind):
    candidates = []
    for node in body:
        names = _bindings(node)
        if name in names or '*' in names: candidates.append(node)
    _require(len(candidates) == 1 and isinstance(candidates[0], kind)
             and candidates[0].name == name, 'test_declaration_missing_or_ambiguous')
    return candidates[0]


def _parametrized(node):
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (isinstance(target, ast.Attribute) and target.attr == 'parametrize'
                or isinstance(target, ast.Name) and target.id == 'parametrize'):
            return True
    return False


def validate_test_plan(files, test_paths, plan):
    """Bind selectors to whole protected test files; helper files may be unselected."""
    validate_plan_shape(plan)
    _require(type(files) is dict and all(type(path) is str for path in files), 'source_files')
    _require(type(test_paths) is list and 1 <= len(test_paths) <= 8
             and all(type(path) is str for path in test_paths)
             and len(set(test_paths)) == len(test_paths), 'test_paths')
    trees = {}
    for path in test_paths:
        _path(path)
        _require(path in files, 'test_path_missing')
        _, trees[path] = _syntax(files[path], path)
    for nodeid in plan['nodeids']:
        path, names, parameter_id = _selector(nodeid,
            declared_names=plan['schema']=='macr.repair.test-plan.v2')
        _require(path in trees, 'nodeid_file_scope')
        tree = trees[path]
        if len(names) == 1:
            function = _declaration(tree.body, names[0], FUNCTIONS)
            parametrized = _parametrized(function)
        else:
            owner = _declaration(tree.body, names[0], ast.ClassDef)
            function = _declaration(owner.body, names[1], FUNCTIONS)
            parametrized = _parametrized(owner) or _parametrized(function)
        _require(not parametrized or parameter_id, 'parameterized_selector_requires_id')
    return plan


def test_names(files, test_paths, plan):
    """Return exact requested IDs; this is not an attestation of test collection."""
    return sorted(validate_test_plan(files, test_paths, plan)['nodeids'])
