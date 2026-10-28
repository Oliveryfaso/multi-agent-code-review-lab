"""Bounded static evidence reads over a frozen, manifest-authorized snapshot."""
from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath
import re

from macr.evals.deepseek_policy_manifest import read_snapshot_file

MAX_FILES = 24
MAX_FILE_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 96 * 1024
MAX_RESULTS = 16
MAX_SNIPPET_LINES = 80
MAX_TERM_BYTES = 128
_TOOLS = {'search', 'read_symbol', 'read_caller'}
_ARGUMENTS = {'search': {'query'}, 'read_symbol': {'file', 'symbol'},
              'read_caller': {'symbol'}}


class StaticEvidenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def _lines(text: str) -> list[str]:
    # splitlines() also splits form feeds and other nonphysical line boundaries.
    return re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', text)[:-1]


def _term(value, *, symbol: bool = False) -> str:
    if (type(value) is not str or not value.strip()
            or len(value.encode('utf-8')) > MAX_TERM_BYTES
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise StaticEvidenceError('term_invalid')
    if symbol and not all(part.isidentifier() for part in value.split('.')):
        raise StaticEvidenceError('symbol_invalid')
    return value


def _snippet(file: str, lines: list[str], start: int, end: int) -> tuple[dict, bool]:
    bounded_end = min(end, start + MAX_SNIPPET_LINES - 1)
    return {'file': file, 'start': start, 'end': bounded_end,
            'body': ''.join(lines[start - 1:bounded_end])}, bounded_end < end


def _tree(file: str, text: str) -> ast.AST:
    if not file.endswith('.py'):
        raise StaticEvidenceError('python_file_required')
    try:
        return ast.parse(text, filename=file)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise StaticEvidenceError('python_source_invalid') from None


def _call_name(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return '.'.join([node.id, *reversed(parts)])


class _Syntax(ast.NodeVisitor):
    """Collect syntax only; dotted names and ownership are never binding proofs."""

    def __init__(self):
        self.scope: list[str] = []
        self.definitions: list[tuple[str, ast.AST]] = []
        self.calls: list[tuple[str, str, ast.Call]] = []

    def _definition(self, node):
        self.scope.append(node.name)
        self.definitions.append(('.'.join(self.scope), node))
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_Call(self, node):
        callee = _call_name(node.func)
        if callee is not None:
            self.calls.append(('.'.join(self.scope) or '<module>', callee, node))
        self.generic_visit(node)


class StaticEvidenceTools:
    """Expose literal search and Python AST observations, never target execution.

    source() fails closed for invalid initial evidence. execute() returns bounded
    error codes with empty snippets so untrusted errors cannot become evidence.
    """

    def __init__(self, manifest: dict, dataset_root: Path):
        try:
            files = manifest['snapshot']['files']
            if type(files) is not dict or not 1 <= len(files) <= MAX_FILES:
                raise StaticEvidenceError('file_limit')
            if any(type(file) is not str for file in files):
                raise StaticEvidenceError('file_invalid')
        except (KeyError, TypeError):
            raise StaticEvidenceError('manifest_invalid') from None
        self.manifest = manifest
        self.dataset_root = Path(dataset_root)
        self.files = tuple(sorted(files))

    def _read(self, files) -> list[tuple[str, str]]:
        if (type(files) not in (list, tuple) or len(files) > MAX_FILES
                or any(type(file) is not str for file in files)
                or len(set(files)) != len(files)):
            raise StaticEvidenceError('files_invalid')
        sources = []
        total = 0
        for file in files:
            if (file not in self.files or len(file.encode('utf-8')) > 512
                    or PurePosixPath(file).is_absolute() or '\\' in file
                    or any(part in ('', '.', '..') for part in file.split('/'))):
                raise StaticEvidenceError('file_unauthorized')
            try:
                text = read_snapshot_file(self.manifest, self.dataset_root, file)
            except (ValueError, OSError, UnicodeError):
                raise StaticEvidenceError('source_unavailable') from None
            size = len(text.encode('utf-8'))
            total += size
            if size > MAX_FILE_BYTES or total > MAX_SOURCE_BYTES:
                raise StaticEvidenceError('source_limit')
            sources.append((file, text))
        return sources

    def source(self, files) -> list[dict]:
        rows = []
        for file, text in self._read(files):
            lines = _lines(text)
            if lines:
                rows.append({'file': file, 'start': 1, 'end': len(lines), 'body': text})
        if len(_encoded(rows)) > MAX_SOURCE_BYTES:
            raise StaticEvidenceError('source_output_limit')
        return rows

    def _result(self, name: str, candidates) -> dict:
        result = {'tool': name, 'status': 'ok', 'snippets': [], 'truncated': False}
        if name == 'read_caller':
            result.update(resolution='unresolved_static_relation', relations=[])
        for snippet, truncated, relation in candidates:
            if len(result['snippets']) >= MAX_RESULTS:
                result['truncated'] = True
                break
            result['snippets'].append(snippet)
            if name == 'read_caller':
                result['relations'].append(relation)
            if len(_encoded(result)) > MAX_OUTPUT_BYTES:
                result['snippets'].pop()
                if name == 'read_caller':
                    result['relations'].pop()
                result['truncated'] = True
                break
            result['truncated'] |= truncated
        return result

    def execute(self, name: str, args: dict) -> dict:
        safe_name = name if type(name) is str and name in _TOOLS else 'invalid'
        try:
            if safe_name == 'invalid':
                raise StaticEvidenceError('tool_not_allowed')
            if type(args) is not dict or set(args) != _ARGUMENTS[name]:
                raise StaticEvidenceError('arguments_invalid')
            value = _term(args['query'] if name == 'search' else args['symbol'],
                          symbol=name != 'search')
            sources = self._read([args['file']] if name == 'read_symbol' else self.files)
            # Validate every read before emitting any partial source.
            candidates = []
            for file, text in sources:
                if len(candidates) > MAX_RESULTS:
                    break
                lines = _lines(text)
                if name == 'search':
                    for number, line in enumerate(lines, 1):
                        if value in line:
                            snippet, truncated = _snippet(file, lines, max(1, number - 2),
                                                          min(len(lines), number + 2))
                            candidates.append((snippet, truncated, None))
                            if len(candidates) > MAX_RESULTS:
                                break
                else:
                    syntax = _Syntax()
                    try:
                        syntax.visit(_tree(file, text))
                    except (RecursionError, MemoryError):
                        raise StaticEvidenceError('python_source_invalid') from None
                    nodes = (syntax.definitions if name == 'read_symbol' else syntax.calls)
                    for row in sorted(nodes, key=lambda item: item[-1].lineno):
                        label, node = row[-2:]
                        if label != value and ('.' in value or label.split('.')[-1] != value):
                            continue
                        start = node.lineno
                        if name == 'read_symbol':
                            start = min([start, *(d.lineno for d in node.decorator_list)])
                        snippet, truncated = _snippet(file, lines, start, node.end_lineno or start)
                        relation = None
                        if name == 'read_caller':
                            relation = {'file': file, 'start': snippet['start'], 'end': snippet['end'],
                                        'caller': row[0], 'callee': label,
                                        'resolution': 'unresolved_static_relation'}
                        candidates.append((snippet, truncated, relation))
                        if len(candidates) > MAX_RESULTS:
                            break
            return self._result(name, candidates)
        except (StaticEvidenceError, UnicodeError) as error:
            return {'tool': safe_name, 'status': 'error',
                    'error_code': getattr(error, 'code', 'arguments_invalid'), 'snippets': []}
