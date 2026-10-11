"""Portable comparison of captured responses; no generation or service authority.

Scripted proofs remain fixtures. Retained native proofs are caller observations,
not independently authenticated by this offline reader.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from macr.investigation.scope import read_safe
from macr.investigation.validation import ContractError, validate_identifier, validate_relative
from macr.memory.atomic_file import atomic_write, tree_usage
from macr.memory.run_store import encoded
from macr.providers.base import ModelRequest, ProviderError, validate_request, validate_response
from macr.providers.local_http import _object
from macr.providers.profiles import validate_profile

VERDICTS = ('supported_defect', 'no_defect_supported', 'unknown')
OUTPUT_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['verdict', 'cause', 'when', 'refs', 'unknowns'],
    'properties': {'verdict': {'enum': list(VERDICTS)},
        'cause': {'type': ['string', 'null'], 'maxLength': 600},
        'when': {'type': ['string', 'null'], 'maxLength': 300},
        'refs': {'type': 'array', 'maxItems': 3, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['file', 'start', 'end'], 'properties': {'file': {'type': 'string'},
                'start': {'type': 'integer', 'minimum': 1}, 'end': {'type': 'integer', 'minimum': 1}}}},
        'unknowns': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string', 'maxLength': 200}}}}
RECORD_LIMIT = 2 * 1024 * 1024
FORMAT_ERRORS = {'model_output_invalid', 'model_output_incomplete', 'eval_contract_invalid', 'eval_citation_invalid'}


def _require(ok, code='comparison_invalid'):
    if not ok:
        raise ValueError(code)


def _digest(value):
    # Protocol bindings prevent scoring a different prompt, response or reference.
    return hashlib.sha256(encoded(value)).hexdigest()


def _root(out):
    out = Path(out).absolute()
    allowed = Path(__file__).resolve().parents[3] / 'artifacts'
    _require(out != allowed and out.is_relative_to(allowed) and out == out.resolve(), 'comparison_path_invalid')
    return out


def _read(root, name):
    return _object(read_safe(root, name, RECORD_LIMIT))


def _write(root, name, value):
    data = encoded(value) + b'\n'
    used = tree_usage(root)['logical_bytes']
    _require(used + 2 * len(data) <= RECORD_LIMIT, 'comparison_record_limit')
    atomic_write(root / name, data)


def prepare_comparison(out, cases, profiles, *, prompt_version, dataset_version, reference):
    """Freeze explicit common inputs/config and a separate scoring reference."""
    out = _root(out)
    _require(not out.exists(), 'comparison_already_exists')
    for version in (prompt_version, dataset_version):
        validate_identifier(version)
    _require(type(cases) is list and 1 <= len(cases) <= 12 and type(profiles) is list and 1 <= len(profiles) <= 4)
    configured = [asdict(validate_profile(profile)) for profile in profiles]
    _require(len({p['profile_id'] for p in configured}) == len(configured))
    common = ('runtime', 'runtime_version', 'chat_template_version',
              'context_limit', 'output_limit', 'sampling', 'timeout_seconds')
    _require(all(all(p[key] == configured[0][key] for key in common) for p in configured), 'comparison_parameters_differ')
    ids = []
    for case in cases:
        _require(type(case) is dict and set(case) == {'id', 'request'})
        validate_identifier(case['id']); ids.append(case['id'])
        request = ModelRequest(**case['request']); validate_request(request)
        _require(request.response_schema == OUTPUT_SCHEMA and not request.tools
            and request.metadata.get('prompt_version') == prompt_version
            and request.max_input_tokens <= 1536 and request.max_output_tokens <= 384 and request.timeout_seconds <= 300)
        _require(all(request.max_output_tokens <= p['output_limit']
            and request.max_input_tokens + request.max_output_tokens <= p['context_limit']
            and request.timeout_seconds <= p['timeout_seconds'] for p in configured))
        source = _object(request.messages[-1]['content']).get('source')
        _require(type(source) is list and 1 <= len(source) <= 12)
        for snippet in source:
            _require(type(snippet) is dict and set(snippet) == {'file', 'start', 'end', 'body'})
            validate_relative(snippet['file'])
            _require(type(snippet['start']) is type(snippet['end']) is int
                and 1 <= snippet['start'] <= snippet['end'] and type(snippet['body']) is str)
    _require(len(set(ids)) == len(ids))
    _require(type(reference) is dict and {'version', 'cases'} <= set(reference) <= {'version', 'cases', 'topics', 'rubric'}
        and type(reference['cases']) is dict and set(reference['cases']) == set(ids)
        and all(v in VERDICTS for v in reference['cases'].values()))
    if 'topics' in reference:
        _require(type(reference['topics']) is dict and set(reference['topics']) == set(ids))
        for topic in reference['topics'].values():
            validate_identifier(topic)
    if 'rubric' in reference:
        # Retain the complete human scoring basis in the same reference binding.
        # It is opaque to semantic scoring and never enters a ModelRequest.
        rubric = reference['rubric']
        fields = {'category', 'topic', 'mechanism', 'trigger', 'missing_evidence',
                  'evidence_roles', 'accepted_shape_witness', 'reject_examples', 'safe_probe'}
        _require('topics' in reference and type(rubric) is dict and set(rubric) == set(ids)
            and len(encoded(rubric)) <= 64 * 1024, 'comparison_rubric_invalid')
        for key, row in rubric.items():
            _require(type(row) is dict and set(row) == fields
                and row['category'] == reference['cases'][key]
                and row['topic'] == reference['topics'][key], 'comparison_rubric_invalid')
    validate_identifier(reference['version'])
    manifest = {'schema_version': 1, 'prompt_version': prompt_version, 'dataset_version': dataset_version,
                'cases': cases, 'profiles': configured, 'reference_sha256': _digest(reference),
                'order': [p['profile_id'] + '/' + case for p in configured for case in ids]}
    _require(len({cell.replace('/', '--') for cell in manifest['order']}) == len(manifest['order']))
    state = {'manifest_sha256': _digest(manifest), 'rows': [], 'pending': None, 'stopped': False}
    _require(len(encoded(manifest)) + len(encoded(reference)) + len(encoded(state)) < RECORD_LIMIT // 2)
    out.mkdir(parents=True)
    for name, value in [('manifest.json', manifest), ('reference.json', reference), ('state.json', state)]:
        _write(out, name, value)
    return {'cells': len(manifest['order']), 'new_model_calls': 0, 'manifest_sha256': state['manifest_sha256']}


def _load(out):
    out = _root(out)
    manifest, reference, state = [_read(out, name) for name in ('manifest.json', 'reference.json', 'state.json')]
    _require(state['manifest_sha256'] == _digest(manifest)
        and manifest['reference_sha256'] == _digest(reference), 'comparison_input_or_reference_drift')
    rows = []
    for index, item in enumerate(state['rows']):
        _require(item['cell'] == manifest['order'][index], 'comparison_order_drift')
        row = _read(out, item['cell'].replace('/', '--') + '.json')
        _require(_digest(row) == item['record_sha256'], 'comparison_response_drift')
        rows.append(row | {'record_sha256': item['record_sha256']})
    if len(rows) < len(manifest['order']):
        next_cell = manifest['order'][len(rows)]
        if (out / (next_cell.replace('/', '--') + '.claim')).exists():
            state['pending'] = next_cell
    return out, manifest, reference, state, rows


def _parse(content, source, *, normalize_refs=True):
    try:
        _require(len(content.encode('utf-8')) <= 8192)
        answer = _object(content)
    except (ValueError, UnicodeError, ProviderError):
        raise ProviderError('model_output_invalid') from None
    try:
        _require(set(answer) == {'verdict', 'cause', 'when', 'refs', 'unknowns'} and answer['verdict'] in VERDICTS)
        unknown = answer['verdict'] == 'unknown'
        for name, limit in [('cause', 600), ('when', 300)]:
            value = answer[name]
            _require(value is None if unknown else type(value) is str and 0 < len(value.strip()) <= limit)
        refs, gaps = answer['refs'], answer['unknowns']
        _require(type(refs) is list and len(refs) <= 3 and (refs or unknown)
            and type(gaps) is list and len(gaps) <= 6 and (gaps or not unknown)
            and all(type(gap) is str and 0 < len(gap.strip()) <= 200 for gap in gaps))
        for ref in refs:
            _require(type(ref) is dict and set(ref) == {'file', 'start', 'end'} and type(ref['file']) is str
                and type(ref['start']) is type(ref['end']) is int and 1 <= ref['start'] <= ref['end'])
    except (ValueError, TypeError, KeyError):
        raise ProviderError('eval_contract_invalid') from None
    for ref in refs:
        try:
            validate_relative(ref['file'])
            _require(any(ref['file'] == s['file'] and s['start'] <= ref['start'] <= ref['end'] <= s['end'] for s in source))
        except (ContractError, ValueError):
            raise ProviderError('eval_citation_invalid') from None
    # Validate every original citation before applying set semantics.
    unique, seen = [], set()
    for ref in refs:
        key = (ref['file'], ref['start'], ref['end'])
        if key not in seen:
            unique.append(ref)
            seen.add(key)
    if not normalize_refs and len(unique) != len(refs):
        raise ProviderError('eval_contract_invalid')
    answer['refs'] = unique
    return answer


def record_response(out, profile_id, case_id, *, response=None, error=None, proof=None, provenance):
    """Consume one captured result in fixed order; a failed write blocks replay.

    The live owner, not this recorder, must stop dispatch on infrastructure failure.
    """
    out, manifest, _, state, _ = _load(out)
    cell = profile_id + '/' + case_id
    _require(provenance in ('scripted', 'retained') and (response is None) != (error is None))
    _require(not state['stopped'] and state['pending'] is None
        and len(state['rows']) < len(manifest['order']) and cell == manifest['order'][len(state['rows'])], 'comparison_consumed_or_stopped')
    profile = next(p for p in manifest['profiles'] if p['profile_id'] == profile_id)
    request = next(c['request'] for c in manifest['cases'] if c['id'] == case_id)
    raw = asdict(response) if response is not None else None
    row = {'cell': cell, 'profile_id': profile_id, 'case_id': case_id, 'provenance': provenance,
        'request_sha256': _digest(request), 'profile_sha256': _digest(profile),
        'response': raw, 'proof': proof, 'answer': None, 'error_code': None,
        'failure_class': 'semantic_pending', 'diagnostic_passed': False,
        'raw_contract_valid': None, 'normalized_contract_valid': None,
        'normalized_failure_class': 'semantic_pending', 'normalized_error_code': None,
        'normalized_diagnostic_passed': False}
    try:
        if error is not None:
            _require(isinstance(error, ProviderError))
            validate_identifier(error.code)
            raise error
        validate_response(response)
        if (type(response.latency_ms) is not int or response.latency_ms < 0
                or type(response.finish_reason) is not str or not response.finish_reason
                or type(response.tool_calls) is not list or not all(type(c) is dict for c in response.tool_calls)):
            raise ProviderError('provider_contract')
        if response.model != profile['model_id']:
            raise ProviderError('model_identity_drift')
        expected = {'request_sha256': row['request_sha256'], 'profile_sha256': row['profile_sha256'],
            'response_sha256': _digest(raw), 'http_status': 200,
            'input_tokens': response.usage.get('input_tokens'), 'output_tokens': response.usage.get('output_tokens'),
            'release': True, 'idle': True, 'remove_waiting': True}
        if (type(proof) is not dict or proof != expected or response.usage_kind != 'measured'
                or any(type(proof.get(key)) is not bool for key in ('release', 'idle', 'remove_waiting'))
                or any(type(proof.get(key)) is not int for key in ('http_status', 'input_tokens', 'output_tokens'))
                or expected['input_tokens'] > request['max_input_tokens']
                or expected['output_tokens'] > request['max_output_tokens']):
            raise ProviderError('native_proof_unverified')
        if response.finish_reason not in ('stop', 'length') or response.tool_calls:
            raise ProviderError('provider_contract')
        if response.finish_reason == 'length':
            raise ProviderError('model_output_incomplete')
        source = _object(request['messages'][-1]['content'])['source']
        row['answer'] = _parse(response.content, source)
        row['normalized_contract_valid'] = True
        try:
            _parse(response.content, source, normalize_refs=False)
            row['raw_contract_valid'] = True
        except ProviderError as failure:
            row.update(raw_contract_valid=False, error_code=failure.code, failure_class='format')
    except ProviderError as failure:
        kind = 'format' if response is not None and failure.code in FORMAT_ERRORS else 'infrastructure'
        row.update(error_code=failure.code, failure_class=kind,
            raw_contract_valid=False if kind == 'format' else None,
            normalized_contract_valid=False if kind == 'format' else None,
            normalized_failure_class=kind, normalized_error_code=failure.code)
    _require(len(encoded(row)) <= 128 * 1024, 'comparison_cell_limit')
    # Exclusive marker also survives failure of the first pending-state write.
    with (out / (cell.replace('/', '--') + '.claim')).open('xb'):
        pass
    state['pending'] = cell
    _write(out, 'state.json', state)
    _write(out, cell.replace('/', '--') + '.json', row)
    digest = _digest(row)
    state['rows'].append({'cell': cell, 'record_sha256': digest})
    state.update(pending=None, stopped=row['failure_class'] == 'infrastructure')
    _write(out, 'state.json', state)
    return row | {'record_sha256': digest}


def _strict_json(content):
    def unique(items):
        value = {}
        for key, item in items:
            _require(key not in value)
            value[key] = item
        return value

    def invalid(value):
        raise ValueError()

    try:
        content.encode('utf-8')
        json.loads(content, object_pairs_hook=unique, parse_constant=invalid)
        return True
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False


def _metrics(rows, expected, *, uncertain=False):
    quality = [row for row in rows if row['response'] is not None and row['failure_class'] != 'infrastructure']
    passes = sum(row['diagnostic_passed'] for row in quality)
    pending = sum(row['failure_class'] == 'semantic_pending' for row in quality)
    strict = sum(row['strict_json_valid'] is True for row in quality)
    contract = sum(row['contract_valid'] is True for row in quality)
    normalized_contract = sum(row['normalized_contract_valid'] is True for row in quality)
    normalized_passes = sum(row['normalized_diagnostic_passed'] for row in quality)
    normalized_pending = sum(row['normalized_failure_class'] == 'semantic_pending' for row in quality)
    return {'expected': expected, 'recorded': len(rows),
        'captured_responses': sum(row['response'] is not None for row in rows),
        'not_attempted': expected - len(rows) - int(uncertain), 'uncertain_cells': int(uncertain),
        'quality_cases': len(quality), 'strict_json_passes': strict,
        'strict_json_rate': strict / len(quality) if quality else None,
        'contract_passes': contract, 'contract_rate': contract / len(quality) if quality else None,
        'raw_contract_passes': contract, 'raw_contract_rate': contract / len(quality) if quality else None,
        'normalized_contract_passes': normalized_contract,
        'normalized_contract_rate': normalized_contract / len(quality) if quality else None,
        'semantic_reviewed': sum(row['semantic_review'] == 'reviewed' for row in quality),
        'diagnostic_passes': passes, 'diagnostic_rate': passes / len(quality) if quality and not pending else None,
        'normalized_semantic_reviewed': sum(row['normalized_semantic_review'] == 'reviewed' for row in quality),
        'normalized_diagnostic_passes': normalized_passes,
        'normalized_diagnostic_rate': normalized_passes / len(quality) if quality and not normalized_pending else None,
        **{name: sum(row['failure_class'] == name for row in rows)
           for name in ('infrastructure', 'format', 'semantic', 'semantic_pending')},
        **{'normalized_' + name: sum(row['normalized_failure_class'] == name for row in rows)
           for name in ('infrastructure', 'format', 'semantic', 'semantic_pending')}}


def compare_results(out, *, reviews=None):
    """Independent human dimensions; JSON parsing never awards a semantic pass."""
    _, manifest, reference, state, rows = _load(out)
    reviews = {} if reviews is None else reviews
    _require(type(reviews) is dict and set(reviews) <= {r['cell'] for r in rows})
    for row in rows:
        review = reviews.get(row['cell'])
        infrastructure = row['failure_class'] == 'infrastructure'
        row['strict_json_valid'] = None if infrastructure else _strict_json(row['response']['content'])
        row['raw_contract_valid'] = row.get('raw_contract_valid', None if infrastructure else row['answer'] is not None)
        row['contract_valid'] = row['raw_contract_valid']
        answer = None
        row['normalized_contract_valid'] = None if infrastructure else False
        row['normalized_failure_class'] = row['failure_class']
        row['normalized_error_code'] = row['error_code']
        if not infrastructure and row['error_code'] != 'model_output_incomplete':
            source = _object(next(c['request'] for c in manifest['cases']
                if c['id'] == row['case_id'])['messages'][-1]['content'])['source']
            try:
                answer = _parse(row['response']['content'], source)
                row.update(normalized_contract_valid=True, normalized_failure_class='semantic_pending',
                    normalized_error_code=None)
            except ProviderError as failure:
                row['normalized_error_code'] = failure.code
        row['normalized_answer'] = answer
        row['normalized_diagnostic_passed'] = False
        row['semantic_review'] = 'not_applicable' if not row['contract_valid'] else 'pending' if review is None else 'reviewed'
        row['normalized_semantic_review'] = 'not_applicable' if answer is None else 'pending' if review is None else 'reviewed'
        row['expected_verdict'] = reference['cases'][row['case_id']]
        if 'topics' in reference:
            row['topic'] = reference['topics'][row['case_id']]
        row['normalized_verdict_correct'] = answer['verdict'] == row['expected_verdict'] if answer else None
        row['normalized_healthy_false_alarm'] = answer['verdict'] == 'supported_defect' if answer and row['expected_verdict'] == 'no_defect_supported' else None
        row['normalized_unknown_abstention'] = answer['verdict'] == 'unknown' if answer and row['expected_verdict'] == 'unknown' else None
        for field in ('verdict_correct', 'healthy_false_alarm', 'unknown_abstention'):
            row[field] = row['normalized_' + field] if row['contract_valid'] else None
        if review is None:
            continue
        dimensions = ('mechanism', 'trigger', 'citation_support', 'abstention_reason')
        _require(answer is not None and type(review) is dict
            and set(review) == {'record_sha256', *dimensions, 'evidence_refs', 'reason'}
            and review['record_sha256'] == row['record_sha256']
            and all(review[d] in ('supported', 'partial', 'contradicted', 'unknown') for d in dimensions)
            and type(review['reason']) is str and 0 < len(review['reason'].strip()) <= 2000
            and type(review['evidence_refs']) is list
            and all(ref in answer['refs'] for ref in review['evidence_refs'])
            and (review['citation_support'] != 'supported' or review['evidence_refs']), 'comparison_review_invalid')
        citation = review['citation_support'] == 'supported'
        if answer['verdict'] == 'unknown' and not answer['refs']:
            citation = review['citation_support'] == 'unknown'
        semantic = (review['abstention_reason'] == 'supported' if answer['verdict'] == 'unknown'
                    else review['mechanism'] == review['trigger'] == 'supported')
        passed = bool(row['normalized_verdict_correct'] and citation and semantic)
        row.update(review=review, diagnostic_passed=bool(row['contract_valid'] and passed),
            normalized_diagnostic_passed=passed, normalized_failure_class=None if passed else 'semantic')
        if row['contract_valid']:
            row['failure_class'] = None if passed else 'semantic'
    models = []
    for profile in manifest['profiles']:
        selected = [r for r in rows if r['profile_id'] == profile['profile_id']]
        prefix = profile['profile_id'] + '/'
        uncertain = state['pending'][len(prefix):] if state['pending'] and state['pending'].startswith(prefix) else None
        categories = {'expected_verdict': {verdict: _metrics(
            [r for r in selected if r['expected_verdict'] == verdict],
            sum(value == verdict for value in reference['cases'].values()),
            uncertain=uncertain is not None and reference['cases'][uncertain] == verdict) for verdict in VERDICTS}}
        if 'topics' in reference:
            categories['topic'] = {topic: _metrics([r for r in selected if r['topic'] == topic],
                sum(value == topic for value in reference['topics'].values()),
                uncertain=uncertain is not None and reference['topics'][uncertain] == topic)
                for topic in sorted(set(reference['topics'].values()))}
        models.append({'profile': profile, **_metrics(selected, len(manifest['cases']), uncertain=uncertain is not None),
            'categories': categories})
    return {'schema_version': 1, 'prompt_version': manifest['prompt_version'], 'dataset_version': manifest['dataset_version'],
        'manifest_sha256': state['manifest_sha256'], 'reference_sha256': manifest['reference_sha256'],
        'cases': rows, 'models': models, 'stopped': state['stopped'], 'uncertain_cell': state['pending'],
        'new_model_calls': 0, 'real_model_accuracy_measured': False,
        'measurement': 'Offline captured-response review; scripted proofs are fixtures; native claims need independent owner audit.'}


def summarize_services(results,*,planned_services):
    """Service lifecycle only. A preflight error is neither a start nor a score."""
    _require(type(planned_services) is int and 1<=planned_services<=2 and type(results) is list and len(results)<=planned_services)
    attempts=started=requests=0;started_rows=[]
    for row in results:
        _require(type(row) is dict and type(row.get('success')) is bool and type(row.get('process')) is dict)
        count,calls=row.get('server_start_attempts'),row.get('generation_requests');pid=row['process'].get('pid')
        _require(type(count) is int and count in (0,1) and type(calls) is int and 0<=calls<=3
            and (pid is None or type(pid) is int and pid>0) and (not calls or count==1 and pid is not None)
            and (pid is None or count==1) and (not row['success'] or count==1 and pid is not None),'service_result_invalid')
        attempts+=count;requests+=calls;started+=pid is not None;started_rows.append(pid is not None)
    return {'success':len(results)==planned_services and all(r['success'] for r in results),
        'service_start_attempts':attempts,'services_started':started,'generation_requests':requests,
        'not_started_services':planned_services-started,
        'second_service_cancelled':planned_services==2 and (len(started_rows)<2 or not started_rows[1])}


def report_paths(folder):
    """JSON-compatible references, including platform-native path separators."""
    return {'json': str(folder / 'comparison.json'), 'markdown': str(folder / 'comparison.md')}


def save_comparison(out, report):
    """Persist the verified report and return JSON-compatible path strings."""
    out, _, _, _, _ = _load(out)
    reviews = {row['cell']: row['review'] for row in report['cases'] if 'review' in row}
    _require(encoded(report) == encoded(compare_results(out, reviews=reviews)), 'comparison_report_drift')
    folder = out / ('report-' + str(uuid4()))
    lines = ['# Diagnostic comparison', '', report['measurement'], '',
             f"Prompt: {report['prompt_version']}; dataset: {report['dataset_version']}", '',
             '| Model | Records | Captured | Quality cases | Raw score | Normalized score | Infrastructure | Format/contract | Semantic | Pending |',
             '| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |']
    for model in report['models']:
        score='pending' if model['semantic_pending'] else 'N/A' if model['diagnostic_rate'] is None else str(model['diagnostic_passes'])+'/'+str(model['quality_cases'])
        normalized_score='pending' if model['normalized_semantic_pending'] else 'N/A' if model['normalized_diagnostic_rate'] is None else str(model['normalized_diagnostic_passes'])+'/'+str(model['quality_cases'])
        lines.append('| ' + ' | '.join(str(value) for value in [model['profile']['profile_id'], model['recorded'],
            model['captured_responses'],model['quality_cases'],score,normalized_score,model['infrastructure'],model['format'],model['semantic'],model['semantic_pending']]) + ' |')
    lines += ['', 'Rates use captured quality cases, including failed structure/contract. Infrastructure is excluded; pending semantic review has no semantic rate.', '',
        'Contract and semantic scores retain the raw strict contract. Normalized scores use only fully validated, stably deduplicated citation positions; original captures and record hashes are unchanged.', '',
        '| Model | Category | Expected | Recorded | Captured | Not attempted | Uncertain | Quality | Strict JSON | Raw contract | Normalized contract | Reviewed | Raw semantic pass | Normalized semantic pass | Infrastructure | Format | Semantic fail | Pending |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: |']
    for model in report['models']:
        for dimension, categories in model['categories'].items():
            for category, metrics in categories.items():
                quality = metrics['quality_cases']
                strict = f"{metrics['strict_json_passes']}/{quality}" if quality else 'N/A'
                contract = f"{metrics['contract_passes']}/{quality}" if quality else 'N/A'
                normalized_contract = f"{metrics['normalized_contract_passes']}/{quality}" if quality else 'N/A'
                score = 'pending' if metrics['semantic_pending'] else f"{metrics['diagnostic_passes']}/{quality}" if quality else 'N/A'
                normalized_score = 'pending' if metrics['normalized_semantic_pending'] else f"{metrics['normalized_diagnostic_passes']}/{quality}" if quality else 'N/A'
                lines.append('| ' + ' | '.join(str(value) for value in [model['profile']['profile_id'],
                    dimension + ':' + category, metrics['expected'], metrics['recorded'], metrics['captured_responses'],
                    metrics['not_attempted'], metrics['uncertain_cells'], quality, strict, contract, normalized_contract,
                    metrics['semantic_reviewed'], score, normalized_score, metrics['infrastructure'], metrics['format'],
                    metrics['semantic'], metrics['semantic_pending']]) + ' |')
    lines += ['', '| Cell | Origin | Outcome | Error |', '| --- | --- | --- | --- |']
    for row in report['cases']:
        lines.append(f"| {row['cell']} | {row['provenance']} | {row['failure_class'] or 'reviewed pass'} | {row['error_code'] or ''} |")
    data = ('\n'.join(lines) + '\n').encode('utf-8')
    _require(len(encoded(report)) + len(data) < RECORD_LIMIT // 2)
    folder.mkdir()
    _write(out, folder.name + '/comparison.json', report)
    _require(tree_usage(out)['logical_bytes'] + 2 * len(data) <= RECORD_LIMIT)
    atomic_write(folder / 'comparison.md', data)
    return report_paths(folder)
