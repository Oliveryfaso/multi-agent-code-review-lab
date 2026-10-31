"""Content-bound static computation and literal, offline full-source comparison."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from time import monotonic

from macr.evals.diagnostic_compact import (
    VERSION, build_candidate_request, candidate_quality_warnings, parse_candidate, prepare_scope)
from macr.evals.deepseek_policy_runner import _response_record
from macr.evals.deepseek_study_live import _ScriptedWire
from macr.providers.base import ConversationRequest, ModelBudget, ProviderError
from macr.providers.deepseek import DeepSeekProvider
from macr.schemas import Evidence
from .index import PARSER_VERSION
from .records import IndexQuery
from .source_text import COORDINATE_ENCODING_VERSION, PHYSICAL_LINE_VERSION, SourceText
from .static_facts import ASSUMPTIONS, collect_static_facts
from .validation import ContractError, validate_relative

PROFILE = 'static-diagnostic-v1'
MAX_SOURCE_BYTES = 16384
MAX_FILES = 12
_ARMS = ('direct', 'evidence')
_SUPPLEMENTAL = (
    'A supplemental static evidence packet may precede the final task. It is '
    'source-derived syntax under stated assumptions, not runtime execution, '
    'proved return values or a diagnosis. Its absence is not a source gap. '
    'Both routes receive the complete same captured source; use only its citable IDs.')


def _require(condition, code='diagnostic_packet_input_invalid'):
    if not condition:
        raise ContractError(code)


def _encoded(value):
    try:
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('diagnostic_packet_input_invalid') from None


def _digest(value):
    return sha256(_encoded(value)).hexdigest()


def _bounded_text(value, limit):
    _require(type(value) is str and bool(value.strip()))
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise ContractError('diagnostic_packet_input_invalid') from None
    _require(size <= limit)


def _inputs(entries, question, scenario, assumptions, budget):
    _require(type(entries) is list and 1 <= len(entries) <= 6
        and all(type(item) is str for item in entries))
    _require(len(set(entries)) == len(entries) and isinstance(budget, ModelBudget))
    for text in (question, scenario):
        _bounded_text(text, 4096)
    _require(type(assumptions) is list and len(assumptions) <= 12)
    for text in assumptions:
        _bounded_text(text, 4096)
    parsed = []
    for entry in entries:
        _bounded_text(entry, 256)
        _require(entry.count(':') == 1)
        file, name = entry.split(':')
        validate_relative(file)
        _require(file.endswith('.py') and name and all(p.isidentifier() for p in name.split('.')))
        parsed.append((file, name))
    _encoded({'entries': entries, 'task': [question, scenario, assumptions], 'budget': asdict(budget)})
    return parsed


def _packet(raw, *, content_id, scope, coverage, selection_gaps):
    evidence, identifiers = [], {}
    for row in raw.get('evidence', []):
        stable = {'content_id': content_id, 'location': row['location'],
            'body_sha256': sha256(row['excerpt'].encode('utf-8')).hexdigest()}
        ref = 's-' + _digest(stable)[:24]
        identifiers[row['evidence_id']] = ref
        evidence.append({'id': ref, **stable, 'origin': row['origin'], 'validity': row['validity']})

    def normalize(value):
        if type(value) is dict:
            return {key: ([identifiers[item] for item in item_value] if key == 'evidence_ids'
                    else normalize(item_value)) for key, item_value in value.items()}
        if type(value) is list:
            return [normalize(item) for item in value]
        return value

    value = {'schema': 'macr.static-diagnostic.packet.v1', 'profile': PROFILE,
        'content_id': content_id, 'scope_sha256': scope.scope_sha256,
        'parser_version': PARSER_VERSION, 'physical_line_version': PHYSICAL_LINE_VERSION,
        'coordinate_encoding_version': COORDINATE_ENCODING_VERSION,
        'evidence': evidence, 'selection_gaps': selection_gaps, 'coverage': coverage,
        **{name: normalize(raw.get(name, [])) for name in
            ('symbols', 'snippets', 'facts', 'bindings', 'relations', 'unknowns', 'truncations')},
        'assumptions': raw.get('assumptions', list(ASSUMPTIONS)),
        'limits': raw.get('limits', {'max_functions': 6, 'max_hops': 1,
            'max_context_bytes': 1200, 'max_facts': 32}) | {'max_bindings': 16, 'max_candidates': 50},
        'data_flow_proven': False, 'repository_code_executed': False}
    _require(len(_encoded(value)) <= 32768, 'diagnostic_packet_capsule_limit')
    return value


def prepare_pair(bundle, index, *, entries, question, scenario, assumptions, budget):
    """Read the complete capture once and compute E-only bounded static facts."""
    started = monotonic()
    parsed = _inputs(entries, question, scenario, assumptions, budget)
    store = index.store
    store.verify(bundle.ref)
    manifest = store._manifest(bundle.ref)
    files = manifest.files
    _require(1 <= len(files) <= MAX_FILES and all(row['size_bytes'] > 0 for row in files)
        and sum(row['size_bytes'] for row in files) <= MAX_SOURCE_BYTES,
        'diagnostic_packet_source_limit')
    _require({row['path'] for row in files} == set(bundle.files)
        == {row.path for row in bundle.scope.eligible}, 'diagnostic_packet_scope_invalid')
    sources, identity, total = [], [], 0
    for row in files:
        body = store.read_verified(bundle.ref, row['path'])
        decoded = SourceText.from_bytes(body, row['path'])
        _require(bool(decoded.physical_lines), 'diagnostic_packet_source_limit')
        total += len(body)
        identity.append({'file': row['path'], 'sha256': sha256(body).hexdigest(), 'bytes': len(body)})
        sources.append(Evidence(row['path'], 1, len(decoded.physical_lines),
            'Complete verified captured source', snippet=decoded.text, source_tool='snapshot_full_source'))
    initial_hash = _digest(identity)
    task_hash = _digest({'profile': PROFILE, 'initial_source_sha256': initial_hash,
        'entries': entries, 'question': question, 'scenario': scenario,
        'assumptions': assumptions, 'budget': asdict(budget)})
    scope = prepare_scope(sources, case_id='case-' + task_hash[:32],
        attempt_id='attempt-' + task_hash[32:], max_body_chars=MAX_SOURCE_BYTES)
    _require(all(row.complete and row.citable and row.body == source.snippet
        for row, source in zip(scope.source, sources)), 'diagnostic_packet_source_incomplete')
    request = build_candidate_request(scope, question=question, scenario=scenario,
        assumptions=assumptions, version=VERSION, **asdict(budget))
    request.messages[0]['content'] += '\n\n' + _SUPPLEMENTAL
    common_done = monotonic()
    summary = index.build(bundle)
    indexed = monotonic()
    seeds, gaps = [], []
    for file, name in parsed:
        page = index.query(bundle.ref, IndexQuery(terms=[name], limit=50))
        matches = [s for s in page.symbols if s.file == file and s.qualified_name == name
            and s.kind in ('function', 'async_function')]
        if len(matches) != 1 or page.next_cursor is not None:
            gaps.append({'entry': file + ':' + name, 'reason': 'missing_or_ambiguous_entry'
                if page.next_cursor is None else 'selection_query_limit'})
        elif matches[0].symbol_id not in seeds:
            seeds.append(matches[0].symbol_id)
    raw = collect_static_facts(bundle, index, seeds, max_context_bytes=1200,
        max_functions=6, max_facts=32, max_bindings=16, max_candidates=50) if seeds else {}
    collected = monotonic()
    capsule = _packet(raw, content_id=bundle.ref.content_id, scope=scope,
        coverage=asdict(summary.coverage), selection_gaps=gaps)
    direct = asdict(request)
    evidence_request = deepcopy(direct)
    evidence_request['messages'].insert(-1, {'role': 'user',
        'content': _encoded({'static_evidence_packet': capsule}).decode('utf-8')})
    for value in (direct, evidence_request):
        _require(len(_encoded(value)) <= 65536, 'diagnostic_packet_request_limit')
    store.verify(bundle.ref)
    ended = monotonic()
    return {'schema': 'macr.static-diagnostic.pair.v1', 'profile': PROFILE,
        'scope': scope.to_dict(), 'packet': capsule, 'packet_sha256': _digest(capsule),
        'initial_source_sha256': initial_hash, 'requests': {'direct': direct, 'evidence': evidence_request},
        'operations': {'explicit_snapshot_verifications': 2, 'full_source_files_read': len(files),
            'full_source_bytes_read': total, 'index_build_count': 1,
            'entry_query_count': len(parsed), 'collector_call_count': int(bool(seeds)),
            'context_bytes_read': raw.get('context_bytes_read', 0),
            'additional_guard_bytes_read': raw.get('additional_guard_bytes_read', 0)},
        'timing_seconds': {'source_scope': common_done - started, 'index': indexed - common_done,
            'collect': collected - indexed, 'normalize': ended - collected,
            'preparation_total': ended - started}}


def run_offline_pair(bundle, index, *, entries, question, scenario, assumptions, budget,
                     replies, order=('direct', 'evidence')):
    """Use only copied literal envelopes; this function has no live transport input."""
    import json
    from macr.evals.diagnostic_compact import restore_scope
    _require(type(order) in (list, tuple) and len(order) == 2 and set(order) == set(_ARMS))
    _require(type(replies) is dict and set(replies) == set(_ARMS), 'diagnostic_packet_replies_invalid')
    try:
        wires = {arm: _ScriptedWire((replies[arm],)) for arm in _ARMS}
    except ValueError:
        raise ContractError('diagnostic_packet_replies_invalid') from None
    prepared = prepare_pair(bundle, index, entries=entries, question=question,
        scenario=scenario, assumptions=assumptions, budget=budget)
    scope = restore_scope(prepared['scope'])
    rows, calls, halted, fingerprint = [], 0, False, None
    for arm in order:
        started = monotonic()
        request = ConversationRequest(**prepared['requests'][arm],
            thinking={'type': 'enabled'}, reasoning_effort='high')
        row = {'arm': arm, 'status': 'not_attempted', 'error_code': None, 'answer': None,
            'quality_warnings': [],
            'request': asdict(request), 'response': _response_record(None),
            'fixture_model_calls': 0, 'model_calls_live': 0}
        if halted:
            rows.append(row)
            continue
        try:
            index.store.verify(bundle.ref)
        except ContractError as error:
            row.update(status='failed', error_code=error.code)
            rows.append(row); halted = True
            continue
        try:
            provider = DeepSeekProvider(model='deepseek-flash', transport=wires[arm], allow_remote=False)
            _require(len(provider.encode_conversation_request(request)) <= 65536,
                     'diagnostic_packet_request_limit')
        except (ContractError, ProviderError) as error:
            row.update(status='failed', error_code=error.code,
                       elapsed_seconds=monotonic() - started)
            rows.append(row)
            continue
        response, failure = None, None
        provider_started = monotonic()
        calls += 1
        row['fixture_model_calls'] = 1
        try:
            response = provider.converse(request)
        except ProviderError as error:
            response, failure = error.response, error
        row['provider_seconds'] = monotonic() - provider_started
        row['response'] = _response_record(response)
        if failure is not None:
            row.update(status='failed', error_code=failure.code)
            for name in ('diagnostic', 'billing_evidence', 'identity_evidence'):
                value = getattr(failure, name)
                if value is not None:
                    row['response'][name] = value
        else:
            try:
                _require(response.model == 'deepseek-flash', 'model_identity_drift')
                current = response.system_fingerprint
                _require(type(current) is str and bool(current.strip()), 'system_fingerprint_unknown')
                _require(fingerprint is None or fingerprint == current, 'system_fingerprint_drift')
                fingerprint = current
                _require(not response.tool_calls and response.finish_reason == 'stop', 'unexpected_model_action')
                answer = parse_candidate(response.content, scope, version=VERSION)
                row.update(answer=answer, quality_warnings=candidate_quality_warnings(answer),
                    status='semantic_pending')
            except (ContractError, ProviderError) as error:
                row.update(status='failed', error_code=error.code)
        try:
            index.store.verify(bundle.ref)
        except ContractError as error:
            row.update(status='failed', error_code=error.code, answer=None, quality_warnings=[])
            halted = True
        row['elapsed_seconds'] = monotonic() - started
        rows.append(row)
    return {'schema': 'macr.static-diagnostic.offline-result.v1', 'profile': PROFILE,
        'prepared': prepared, 'planned_cells': 2, 'rows': rows,
        'provenance': 'scripted_offline_fixture', 'fixture_model_calls': calls,
        'model_calls_live': 0, 'semantic_proof': False, 'halted': halted,
        'timing_seconds': {'preparation': prepared['timing_seconds']['preparation_total'],
            'provider': sum(row.get('provider_seconds', 0) for row in rows),
            'arms': sum(row.get('elapsed_seconds', 0) for row in rows)}}
