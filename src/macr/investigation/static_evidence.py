"""Worker-internal deterministic static preparation from explicitly captured bytes.

The supervisor owns public collection, lifecycle and publication. This module
never imports providers, executes target code or commits an output workspace.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import tokenize
from dataclasses import asdict
from pathlib import Path

from macr import __version__
from .evidence_budget import BudgetExceeded
from .evidence_records import EvidenceRequest, StaticEvidenceReport
from .evidence_report import stable_result, _FRAME, _relative_frame_path
from .evidence_workspace import EvidenceWorkspace
from .index import PARSER_VERSION, RepositoryIndex, _Collector
from .records import CoverageRecord, IndexSummary, RunError, SnapshotBundle, SnapshotRef
from .scope import RepositoryScope, exclusion
from .snapshots import SnapshotStore
from .source_text import SourceText, PHYSICAL_LINE_VERSION, COORDINATE_ENCODING_VERSION
from .static_facts import ASSUMPTIONS, collect_static_facts
from .validation import ContractError, decode_record

SELECTION_VERSION = 'exact-qualified-physical-span-v2'
POLICY_VERSION = 'scope-policy-v1'


def _id(kind, values):
    encoded = json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    return kind + ':' + hashlib.sha256(encoded).hexdigest()


def _source_version(ref):
    return {'content_id': ref.content_id, 'snapshot_ref': asdict(ref), 'parser_version': PARSER_VERSION,
            'physical_line_version': PHYSICAL_LINE_VERSION, 'coordinate_encoding_version': COORDINATE_ENCODING_VERSION,
            'policy_version': POLICY_VERSION}


def _run(status):
    return {'status': status, 'tool_version': __version__, 'python_version': sys.version.split()[0],
            'selection_version': SELECTION_VERSION, 'phase': 'prepared', 'publication': 'pending'}


def _store(workspace):
    return SnapshotStore(workspace.snapshots_root, allowed_root=workspace.root,
                         profile=workspace.profile, budget=workspace.budget)


def _bind_workspace(request, workspace):
    request.validate()
    if (not isinstance(workspace, EvidenceWorkspace) or Path(request.repo).absolute() != workspace.repo
            or Path(request.out).absolute() != workspace.root or request.profile.to_dict() != workspace.profile.to_dict()):
        raise ContractError('input_invalid', 'workspace_request')
    workspace.check_storage('prepare_storage')


def _limit(field, stage, effective, observed):
    return {'field': field, 'stage': stage, 'effective': effective, 'observed': observed}


def _scope_limits(scope):
    reasons = []
    for error in scope.errors:
        if error.code == 'scope_limit':
            fields = error.detail.split(';')
            values = dict(item.split('=', 1) for item in fields[1:] if '=' in item)
            try:
                reasons.append(_limit(fields[0], values['stage'], float(values['limit']), float(values['observed'])))
            except (KeyError, ValueError):
                raise ContractError('internal_error', 'scope_limit_detail') from None
    return reasons


def _failure(request, workspace, error, *, scope=None):
    limits = []
    if isinstance(error, BudgetExceeded):
        limits.append(_limit(error.field, error.stage, error.limit, error.observed))
    if scope:
        limits.extend(_scope_limits(scope))
    status = ('partial' if error.code == 'scope_limit' else 'cancelled' if error.code == 'cancelled'
              else 'rejected' if error.code in ('forbidden_path', 'stale_snapshot', 'citation_invalid', 'case_collision')
              else 'failed')
    coverage = _coverage(scope, None, [], [], 0, 0, request.profile) if scope else {}
    return StaticEvidenceReport(request, status, run=_run(status),
        selection={'requested': request.entries + request.symbols, 'resolved': [],
                   'unresolved': [{'entry': entry, 'reason': error.code} for entry in request.entries + request.symbols],
                   'ambiguous': [], 'reported_frames': []}, coverage=coverage,
        observations={'symbols': [], 'facts': [], 'bindings': [], 'relations': [], 'unknowns': [],
                      'assumptions': list(ASSUMPTIONS), 'data_flow_proven': False},
        gaps=[{'code': error.code, 'required': True, 'stage': workspace.budget.stage}],
        errors=[{'code': error.code, 'field': error.field, 'stage': workspace.budget.stage, 'retryable': False}],
        usage={'model_calls': 0, 'tokens': None, **workspace.budget.usage},
        limits={'effective': request.profile.to_dict(), 'exceeded': limits}).validate()


def prepare_evidence(request: EvidenceRequest, workspace: EvidenceWorkspace) -> StaticEvidenceReport:
    """Internal prepare only. Return verified evidence or a source-less failure."""
    scope = None
    try:
        _bind_workspace(request, workspace)
        for entry in request.entries:
            if exclusion(entry.partition(':')[0], request.profile.scope_profile()):
                raise ContractError('forbidden_path', 'entry')
        scope = RepositoryScope().inventory(workspace.repo, request.profile.scope_profile(), budget=workspace.budget)
        if scope.errors:
            first = scope.errors[0]
            raise ContractError(first.code, 'scope_inventory')
        store = _store(workspace)
        bundle = store.capture(workspace.repo, scope)
        index = RepositoryIndex(store, workspace.index_root, allowed_root=workspace.root,
                                profile=workspace.profile, budget=workspace.budget)
        summary = index.build(bundle)
        report = _assemble(request, workspace, bundle, index, summary)
        validate_report_sources(report, workspace)
        report.usage.update(workspace.budget.usage)
        return report.validate()
    except ContractError as error:
        return _failure(request, workspace, error, scope=scope)
    except OSError:
        return _failure(request, workspace, ContractError('storage_unavailable', 'prepare'), scope=scope)


def _bounded(items, cap):
    # Canonical coverage sampling stays stable across inventory and saved-manifest order.
    items = sorted(items, key=lambda item: (item['file'], item['reason']))
    return {'items': items[:cap], 'omitted_count': max(0, len(items) - cap)}


def _coverage(scope, summary, resolved, evidence, excerpt_bytes, guard_bytes, profile):
    selected_files = {item['file'] for item in resolved}
    unavailable = {error.detail: error.code for error in summary.errors} if summary else {}
    parsed = summary.coverage.parsed if summary else 0
    complete_ids = {item['evidence_id'] for item in evidence if item['complete_range']}
    full = sum(item.get('empty_source') is not None or item.get('evidence_id') in complete_ids for item in resolved)
    return {'discovered_count': sum(len(getattr(scope, name)) for name in ('eligible', 'excluded', 'unsupported', 'unreadable')),
            'eligible_text_count': len(scope.eligible), 'indexed_text_count': len(scope.eligible) if summary else 0,
            'python_file_count': sum(item.language == 'python' for item in scope.eligible), 'parsed_python_count': parsed,
            'excluded_count': len(scope.excluded), 'unsupported_count': len(scope.unsupported),
            'unreadable_count': len(scope.unreadable), 'selected_entry_count': len(resolved),
            'full_entry_count': full, 'partial_entry_count': len(resolved) - full,
            'excerpt_read_bytes': excerpt_bytes, 'guard_read_bytes': guard_bytes,
            'unread': _bounded([{'file': item.path, 'reason': 'not_selected_for_display'} for item in scope.eligible
                                if item.path not in selected_files], profile.max_candidates),
            'unparsed': _bounded([{'file': item.path, 'reason': unavailable.get(item.path, 'text_only')}
                                  for item in scope.eligible if item.language != 'python' or item.path in unavailable], profile.max_candidates),
            'excluded': _bounded([{'file': item.path, 'reason': item.reason} for item in scope.excluded], profile.max_candidates),
            'unsupported': _bounded([{'file': item.path, 'reason': item.reason} for item in scope.unsupported], profile.max_candidates),
            'unreadable': _bounded([{'file': item.path, 'reason': item.reason} for item in scope.unreadable], profile.max_candidates)}


def _frames(request):
    frames, observed = [], 0
    for raw in request.traceback.split('\n'):
        match = _FRAME.fullmatch(raw.rstrip('\r'))
        if match:
            path, line, name = match.groups()
            try:
                number = int(line)
            except ValueError:
                number = 0
            item = {'file': path, 'line': number, 'name': (name or '').strip(), 'origin': 'reported_traceback'}
            try:
                path = _relative_frame_path(path, request.repo)
                if exclusion(path, request.profile.scope_profile()):
                    item['status'] = 'forbidden'
                elif number < 1:
                    item['status'] = 'invalid'
                else:
                    item.update(file=path, status='pending')
            except (ValueError, ContractError):
                item.update(file='<external>', status='external')
            observed += 1
            if len(frames) < request.profile.max_traceback_frames:
                frames.append(item)
        elif re.match(r'^\s*File\b', raw):
            observed += 1
            if len(frames) < request.profile.max_traceback_frames:
                frames.append({'file': '<unavailable>', 'line': 0, 'name': '',
                               'origin': 'reported_traceback', 'status': 'invalid'})
    return frames, observed


def _decorator_spans(source, budget):
    """Locate logical decorator statements once within the bounded captured file."""
    lines = iter(line.rstrip('\r\n') + ('\n' if line.endswith(('\r', '\n')) else '')
                 for line in source.physical_lines)
    starts, pending, prefix = {}, [], []
    try:
        for token in tokenize.generate_tokens(lambda: next(lines, '')):
            budget.check('declaration_tokens')
            if token.type in (tokenize.INDENT, tokenize.DEDENT, tokenize.NL, tokenize.COMMENT, tokenize.ENDMARKER):
                continue
            if token.type != tokenize.NEWLINE:
                if len(prefix) < 2:
                    prefix.append(token)
                continue
            if prefix:
                first = prefix[0]
                if first.string == '@':
                    if pending and first.start[1] != pending[-1][1]:
                        pending = []
                    pending.append((first.start[0], first.start[1], token.start[0]))
                else:
                    definition = first.string in ('def', 'class') or (first.string == 'async'
                        and len(prefix) > 1 and prefix[1].string == 'def')
                    if definition and pending and first.start[1] == pending[-1][1]:
                        starts[first.start] = pending
                    pending = []
            prefix = []
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {}
    return starts


def _declaration(symbol, source, tree, decorators):
    if symbol.kind == 'file':
        return (1, len(source.physical_lines)) if source.physical_lines else None
    def find(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = '.'.join(filter(None, (prefix, child.name)))
                if qualified == symbol.qualified_name and child.lineno == symbol.start and child.end_lineno == symbol.end:
                    if not child.decorator_list:
                        return child.lineno, child.end_lineno
                    markers = decorators.get((child.lineno, child.col_offset), [])
                    if len(markers) != len(child.decorator_list) or any(
                        not start <= expression.lineno <= expression.end_lineno <= end < child.lineno
                        or column != child.col_offset
                        or source.physical_lines[start - 1].encode('utf-8')[column:column + 1] != b'@'
                        for (start, column, end), expression in zip(markers, child.decorator_list)):
                        raise ContractError('declaration_unavailable', 'decorator_start')
                    return markers[0][0], child.end_lineno
                result = find(child, qualified)
            else:
                result = find(child, prefix)
            if result:
                return result
        return None
    span = find(tree) if tree is not None else None
    if span is None:
        raise ContractError('citation_invalid', 'declaration_identity')
    return span


def _assemble(request, workspace, bundle, index, summary):
    profile, store, ref = request.profile, index.store, bundle.ref
    eligible = {item.path: item for item in bundle.scope.eligible}
    parse_errors = {error.detail for error in summary.errors}
    index.symbols.sort(key=lambda item: (item.file, item.start, item.end, item.qualified_name, item.kind))
    index.relations.sort(key=lambda item: (item.location.file, item.location.start, item.location.end,
                                          item.source_id, item.target_ref, item.relation_type, item.resolution))
    frames, frame_count = _frames(request)
    selection = {'requested': request.entries + request.symbols, 'resolved': [], 'unresolved': [],
                 'ambiguous': [], 'reported_frames': frames,
                 'reported_frames_omitted_count': max(0, frame_count - len(frames))}
    evidence, gaps, reasons = [], [], []
    sources, trees, decorators, parse_status = {}, {}, {}, {}
    excerpt_bytes = 0
    remaining = profile.max_body_bytes
    ref_attempts = 0
    if frame_count > len(frames):
        gaps.append({'code': 'traceback_frame_limit', 'required': True})
        reasons.append(_limit('max_traceback_frames', 'selection', profile.max_traceback_frames, frame_count))
    selected_symbols = []
    evidence_by_symbol = {}
    admitted = set()
    reported_seen = set()

    def admitted_frame(frame):
        if frame is not None:
            identity = (frame['file'], frame['line'], frame['name'])
            frame['status'] = 'duplicate' if identity in reported_seen else 'valid'
            reported_seen.add(identity)

    def load(file):
        nonlocal excerpt_bytes
        if file not in sources:
            data = store.read_verified(ref, file)
            excerpt_bytes += store.last_usage.bytes_read
            sources[file] = SourceText.from_bytes(data, file)
            status = 'parsed_python' if eligible[file].language == 'python' and file not in parse_errors else 'parse_unavailable' if eligible[file].language == 'python' else 'text_only'
            tree = None
            if status == 'parsed_python':
                try:
                    tree = ast.parse(data, filename=file)
                    index._check_ast(tree)
                except (SyntaxError, UnicodeError, ValueError, RecursionError):
                    raise ContractError('citation_invalid', 'indexed_parse_identity') from None
            trees[file], parse_status[file] = tree, status
            decorators[file] = _decorator_spans(sources[file], workspace.budget) if tree is not None else {}
        return sources[file]

    def missing(entry, reason, origin):
        selection['unresolved'].append({'entry': entry, 'reason': reason, 'origin': origin})
        gaps.append({'code': reason, 'entry': entry, 'required': origin != 'one_hop'})

    def choose(entry, origin, frame=None):
        nonlocal remaining, ref_attempts
        if frame is not None:
            # Early resolution failures retain their detailed gaps and are terminal.
            frame['status'] = 'invalid'
        file, separator, selector = entry.partition(':')
        symbol, span, kind = None, None, 'symbol'
        if origin == 'explicit_symbol':
            candidates = [item for item in index.symbols if item.qualified_name == entry and item.kind != 'file']
        else:
            if file not in eligible:
                missing(entry, 'entry_unavailable', origin)
                return
            source = load(file)
            if not separator:
                kind = 'file'
                candidates = [item for item in index.symbols if item.file == file and item.kind == 'file'] if source.physical_lines else []
                span = (1, len(source.physical_lines)) if source.physical_lines else None
            elif re.fullmatch(r'[0-9]+(?:-[0-9]+)?', selector):
                numbers = selector.split('-')
                start, end = int(numbers[0]), int(numbers[-1])
                if not 1 <= start <= end <= len(source.physical_lines):
                    missing(entry, 'line_unavailable', origin)
                    return
                if len(numbers) == 2:
                    kind, span, candidates = 'range', (start, end), []
                else:
                    try:
                        candidates = [item for item in index.symbols if item.file == file and item.kind != 'file'
                                      and _declaration(item, source, trees[file], decorators[file])[0] <= start <= item.end]
                    except ContractError as error:
                        if error.code != 'declaration_unavailable':
                            raise
                        missing(entry, error.code, origin)
                        return
                    if candidates:
                        deepest = max(item.qualified_name.count('.') for item in candidates)
                        candidates = [item for item in candidates if item.qualified_name.count('.') == deepest]
                    else:
                        kind, span = 'line', (start, end)
            else:
                candidates = [item for item in index.symbols if item.file == file and item.qualified_name == selector
                              and item.kind != 'file']
        candidates = sorted(candidates, key=lambda item: (item.file, item.start, item.end, item.qualified_name, item.kind))
        if len(candidates) > 1:
            selection['ambiguous'].append({'entry': entry, 'origin': origin,
                'candidates': [asdict(item) for item in candidates[:profile.max_candidates]],
                'omitted_count': max(0, len(candidates) - profile.max_candidates)})
            gaps.append({'code': 'entry_ambiguous', 'entry': entry, 'required': origin != 'one_hop'})
            return
        if candidates:
            symbol = candidates[0]
            file = symbol.file
            source = load(file)
            try:
                span = _declaration(symbol, source, trees[file], decorators[file])
            except ContractError as error:
                if error.code != 'declaration_unavailable':
                    raise
                missing(entry, error.code, origin)
                return
        elif kind == 'symbol':
            missing(entry, 'symbol_unavailable', origin)
            return
        else:
            source = load(file)
        if frame is not None:
            name = frame['name']
            # Reported frames are clues, never proof of execution at this version.
            expected = symbol.qualified_name.split('.')[-1] if symbol else '<module>'
            if name and name != expected:
                frame['status'] = 'mismatch'
                missing(entry, 'reported_frame_mismatch', origin)
                return
        key = (file, symbol.symbol_id if symbol else None, span)
        if key in admitted:
            admitted_frame(frame)
            return
        main_entries = sum(item['origin'] != 'one_hop' for item in selection['resolved']) + len(selection['ambiguous']) + len(selection['unresolved'])
        if origin != 'one_hop' and main_entries >= profile.max_entries:
            missing(entry, 'entry_limit', origin)
            reasons.append(_limit('max_entries', 'selection', profile.max_entries, main_entries + 1))
            if frame:
                frame['status'] = 'limited'
            return
        row = {'entry': entry, 'kind': kind, 'origin': origin, 'file': file,
               'symbol_id': symbol.symbol_id if symbol else None, 'evidence_id': None,
               'requested_span': {'start': span[0], 'end': span[1]} if span else None}
        if span is None:
            row['empty_source'] = {'content_id': ref.content_id, 'snapshot_id': ref.snapshot_id,
                                   'file': file, 'physical_line_count': 0, 'size_bytes': 0,
                                   'encoding': source.encoding, 'parse_status': parse_status[file]}
        elif len(evidence) >= profile.max_refs:
            ref_attempts += 1
            gaps.append({'code': 'ref_limit', 'entry': entry, 'required': origin != 'one_hop'})
            reasons.append(_limit('max_refs', 'source_excerpt', profile.max_refs, ref_attempts))
        else:
            excerpt = source.excerpt(*span, remaining)
            original_size = len(source.excerpt(*span).text.encode('utf-8'))
            if excerpt.text:
                ref_attempts += 1
                ev = {'content_id': ref.content_id, 'snapshot_id': ref.snapshot_id, 'file': file,
                      'requested_span': row['requested_span'],
                      'actual_span': {'start': excerpt.actual_start, 'end': excerpt.actual_end},
                      'text': excerpt.text, 'complete_range': excerpt.complete_range,
                      'complete_symbol': bool(symbol and excerpt.complete_range),
                      'symbol_id': row['symbol_id'], 'origin': 'explicit_range' if kind == 'range' else origin,
                      'encoding': source.encoding, 'parse_status': parse_status[file],
                      'truncation_reason': excerpt.truncation_reason}
                ev['evidence_id'] = _id('source', {key: value for key, value in ev.items() if key != 'snapshot_id'})
                row['evidence_id'] = ev['evidence_id']
                evidence.append(ev)
                remaining -= len(excerpt.text.encode('utf-8'))
                if symbol and excerpt.complete_range:
                    evidence_by_symbol[symbol.symbol_id] = ev['evidence_id']
                    selected_symbols.append(symbol)
            if not excerpt.complete_range:
                gaps.append({'code': 'body_truncated', 'entry': entry, 'required': origin != 'one_hop'})
                reasons.append(_limit('max_body_bytes', 'source_excerpt', profile.max_body_bytes,
                                      profile.max_body_bytes - remaining + original_size - len(excerpt.text.encode('utf-8'))))
        selection['resolved'].append(row)
        admitted.add(key)
        admitted_frame(frame)
        if parse_status[file] != 'parsed_python':
            gaps.append({'code': parse_status[file], 'entry': entry, 'required': False})

    for entry in request.entries:
        choose(entry, 'explicit_entry')
    for entry in request.symbols:
        choose(entry, 'explicit_symbol')
    for frame in reversed(selection['reported_frames']):
        if frame['status'] == 'pending':
            choose(f"{frame['file']}:{frame['line']}", 'reported_traceback', frame)
        elif frame['status'] in ('invalid', 'forbidden'):
            missing('<reported_frame>', 'reported_frame_invalid', 'reported_traceback')
    if request.traceback:
        gaps.append({'code': 'reported_trace_not_verified', 'required': False})
        if not selection['reported_frames']:
            missing('<reported_traceback>', 'reported_trace_format', 'reported_traceback')
        elif not selection['resolved'] and not selection['unresolved'] and not selection['ambiguous']:
            missing('<reported_traceback>', 'reported_trace_external', 'reported_traceback')
    observations = {'symbols': [], 'facts': [], 'bindings': [], 'relations': [], 'unknowns': [],
                    'assumptions': list(ASSUMPTIONS), 'data_flow_proven': False}
    seeds = [item.symbol_id for item in selected_symbols if item.kind in ('function', 'async_function')]
    guard_bytes = 0
    if seeds:
        accepted = seeds[:min(profile.max_functions, profile.max_candidates)]
        packet = collect_static_facts(bundle, index, accepted, max_context_bytes=profile.max_collector_bytes,
            max_functions=profile.max_functions, max_facts=profile.max_facts,
            max_bindings=profile.max_bindings, max_candidates=profile.max_candidates, profile=profile)
        guard_bytes = packet['additional_guard_bytes_read']
        excerpt_bytes += packet['context_bytes_read']
        observations['symbols'] = packet['symbols']
        observations['unknowns'] = packet['unknowns']
        if len(seeds) > profile.max_functions:
            observations['unknowns'].append({'reason': 'function_limit', 'symbol_id': None, 'location': None})
            packet['limits']['observed']['max_functions'] = max(len(seeds), packet['limits']['observed']['max_functions'])
        if len(seeds) > profile.max_candidates:
            observations['unknowns'].append({'reason': 'candidate_limit', 'symbol_id': None, 'location': None})
            packet['limits']['observed']['max_candidates'] = max(len(seeds), packet['limits']['observed']['max_candidates'])
        # Related one-hop declarations are shown and byte-verified before derived references survive.
        for raw in packet['symbols']:
            if raw['symbol_id'] not in evidence_by_symbol:
                symbol = next(item for item in index.symbols if item.symbol_id == raw['symbol_id'])
                choose(symbol.file + ':' + symbol.qualified_name, 'one_hop')
        old_evidence = {item['evidence_id']: item['location']['symbol_id'] for item in packet['evidence']}
        def translate(item, kind):
            original = list(item.get('evidence_ids', []))
            symbols = [old_evidence.get(identifier) for identifier in original]
            if not symbols or any(symbol not in evidence_by_symbol for symbol in symbols):
                return None
            result = dict(item)
            result['evidence_ids'] = [evidence_by_symbol[symbol] for symbol in symbols]
            if kind == 'fact':
                result.pop('fact_id', None)
                result['fact_id'] = _id('fact', result)
            if kind == 'binding':
                result['binding_id'] = _id('binding', result)
            return result
        observations['facts'] = [value for item in packet['facts'] if (value := translate(item, 'fact'))]
        observations['bindings'] = [value for item in packet['bindings'] if (value := translate(item, 'binding'))]
        for item in packet['relations']:
            caller, callee = item['source_id'], item['target_ref']
            if caller not in evidence_by_symbol:
                continue
            if item['resolution'] in ('resolved_local', 'resolved_import') and callee not in evidence_by_symbol:
                continue
            relation = dict(item)
            relation['evidence_ids'] = [evidence_by_symbol[caller]] + ([evidence_by_symbol[callee]] if callee in evidence_by_symbol and callee != caller else [])
            relation['relation_id'] = _id('relation', relation)
            observations['relations'].append(relation)
        for reason in sorted({item['reason'] for item in observations['unknowns']}):
            gaps.append({'code': 'collector_' + reason, 'required': False})
            field = {'fact_limit': 'max_facts', 'binding_limit': 'max_bindings',
                     'function_limit': 'max_functions', 'relation_query_limit': 'max_candidates',
                     'candidate_limit': 'max_candidates',
                     'context_truncated': 'max_collector_bytes'}.get(reason)
            if field:
                reasons.append(_limit(field, 'static_facts', getattr(profile, field),
                                      packet['limits']['observed'][field]))
    # Symbol identity survives a partial excerpt; derived facts still require complete evidence.
    observed_ids = {item['symbol_id'] for item in observations['symbols']}
    referenced_ids = {item['symbol_id'] for item in evidence if item['symbol_id']}
    observations['symbols'].extend(asdict(symbol) for symbol in index.symbols
                                   if symbol.symbol_id in referenced_ids - observed_ids)
    if len(evidence) > profile.max_refs:
        raise BudgetExceeded('max_refs', 'report_refs', profile.max_refs, len(evidence))
    for key in ('facts', 'bindings', 'relations'):
        observations[key].sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))
    observations['unknowns'].sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))
    # A required main/body gap is partial; unsupported syntax alone is a limitation.
    status = 'partial' if any(item['required'] for item in gaps) else 'completed'
    report = StaticEvidenceReport(request, status, run=_run(status), source_version=_source_version(ref),
        selection=selection, evidence=evidence, observations=observations,
        coverage=_coverage(bundle.scope, summary, selection['resolved'], evidence, excerpt_bytes, guard_bytes, profile),
        gaps=gaps, usage={'model_calls': 0, 'tokens': None, 'source_display_bytes': profile.max_body_bytes - remaining,
                         **workspace.budget.usage}, limits={'effective': profile.to_dict(), 'exceeded': reasons})
    return report.validate()


class _CapturedIndex:
    """Canonical raw-AST view using the existing collector/resolver, never cache."""
    _check_ast = RepositoryIndex._check_ast
    _relations = RepositoryIndex._relations
    query = RepositoryIndex.query

    def __init__(self, store, profile, budget):
        self.store, self.profile, self.budget = store, profile, budget
        self.symbols, self.relations, self.content_id = [], [], None

    def build(self, bundle):
        collectors, errors = [], []
        self.store.verify(bundle.ref)
        for item in bundle.scope.eligible:
            if item.language != 'python':
                continue
            data = self.store.read_verified(bundle.ref, item.path)
            try:
                source = SourceText.from_bytes(data, item.path)
                tree = ast.parse(data, filename=item.path)
                self._check_ast(tree)
                collectors.append(_Collector(item.path, bundle.ref.content_id, tree, len(source.physical_lines), self.budget))
            except BudgetExceeded:
                raise
            except ContractError as error:
                if error.code == 'cancelled':
                    raise
                errors.append(RunError('unsupported_coordinate_encoding' if error.code == 'unsupported_coordinate_encoding' else 'parse_error', item.path))
            except (SyntaxError, UnicodeError, ValueError, RecursionError):
                errors.append(RunError('parse_error', item.path))
        self.symbols = [symbol for collector in collectors for symbol in collector.symbols]
        self.relations = self._relations(collectors)
        self.content_id = bundle.ref.content_id
        return IndexSummary(self.content_id, CoverageRecord(parsed=sum(item.language == 'python' for item in bundle.scope.eligible) - len(errors)), errors)


def validate_report_sources(report: StaticEvidenceReport, workspace: EvidenceWorkspace) -> StaticEvidenceReport:
    """Check effective refs and derived syntax from saved raw AST; all I/O is charged.

    Failure raises without certifying the report. prepare_evidence catches it and
    clears all effective source/selection/observations. Final full saved-source
    verification is mandatory, including unselected captured files.
    """
    report.validate()
    _bind_workspace(report.request, workspace)
    if report.source_version is None:
        if report.evidence or report.selection.get('resolved'):
            raise ContractError('citation_invalid', 'source_less_selection')
        return report
    ref = decode_record(SnapshotRef, report.source_version.get('snapshot_ref'))
    if report.source_version != _source_version(ref):
        raise ContractError('citation_invalid', 'source_version')
    store = _store(workspace)
    manifest = store._manifest(ref)
    folder = Path(ref.content_manifest_ref).parent
    from .evidence_profiles import load_json
    from .records import ScopeManifest
    scope = decode_record(ScopeManifest, load_json(workspace.read(folder.relative_to(workspace.root).as_posix() + '/scope.json',
        limit=workspace.profile.max_scope_manifest_bytes).decode('utf-8'),
        max_bytes=workspace.profile.max_scope_manifest_bytes, max_depth=workspace.profile.max_input_depth))
    scope.eligible.sort(key=lambda item: item.path)
    if asdict(scope.profile) != manifest.scope_profile or [item.path for item in scope.eligible] != [item['path'] for item in manifest.files]:
        raise ContractError('citation_invalid', 'scope_manifest')
    for item, raw in zip(scope.eligible, manifest.files):
        data = store.read_verified(ref, item.path)
        from .source_text import raw_physical_line_count
        if (item.size_bytes != raw['size_bytes'] or item.lines != raw_physical_line_count(data)
                or item.language != raw['language']
                or item.language != ('python' if Path(item.path).suffix == '.py' else 'text')):
            raise ContractError('citation_invalid', 'scope_source_bounds')
    bundle = SnapshotBundle(ref, scope, {})
    index = _CapturedIndex(store, workspace.profile, workspace.budget)
    summary = index.build(bundle)
    canonical = _assemble(report.request, workspace, bundle, index, summary)
    actual, expected = stable_result(report), stable_result(canonical)
    # Lifecycle is owned by Task4; prepare semantics and source references stay canonical.
    actual.pop('run')
    expected.pop('run')
    if actual != expected:
        raise ContractError('citation_invalid', 'source_semantics')
    store.verify(ref)
    workspace.budget.check('final_source_verified')
    return report
