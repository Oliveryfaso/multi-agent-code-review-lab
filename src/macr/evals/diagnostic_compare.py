"""Portable comparison of captured responses; no generation or service authority.

Scripted proofs remain fixtures. Retained native proofs are caller observations,
not independently authenticated by this offline reader.
"""
from dataclasses import asdict
import hashlib
from pathlib import Path
from uuid import uuid4

from macr.investigation.scope import read_safe
from macr.investigation.validation import ContractError, validate_identifier, validate_relative
from macr.memory.atomic_file import atomic_write
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
    used = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
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
    common = ('runtime', 'runtime_version', 'context_limit', 'output_limit', 'sampling', 'timeout_seconds')
    _require(all(all(p[key] == configured[0][key] for key in common) for p in configured), 'comparison_parameters_differ')
    ids = []
    for case in cases:
        _require(type(case) is dict and set(case) == {'id', 'request'})
        validate_identifier(case['id']); ids.append(case['id'])
        request = ModelRequest(**case['request']); validate_request(request)
        _require(request.response_schema == OUTPUT_SCHEMA and not request.tools
            and request.metadata.get('prompt_version') == prompt_version
            and request.max_input_tokens <= 1536 and request.max_output_tokens <= 384 and request.timeout_seconds <= 60)
        _require(all(request.max_output_tokens <= p['output_limit']
            and request.max_input_tokens + request.max_output_tokens <= p['context_limit'] for p in configured))
        source = _object(request.messages[-1]['content']).get('source')
        _require(type(source) is list and 1 <= len(source) <= 12)
        for snippet in source:
            _require(type(snippet) is dict and set(snippet) == {'file', 'start', 'end', 'body'})
            validate_relative(snippet['file'])
            _require(type(snippet['start']) is type(snippet['end']) is int
                and 1 <= snippet['start'] <= snippet['end'] and type(snippet['body']) is str)
    _require(len(set(ids)) == len(ids))
    _require(type(reference) is dict and set(reference) == {'version', 'cases'}
        and type(reference['cases']) is dict and set(reference['cases']) == set(ids)
        and all(v in VERDICTS for v in reference['cases'].values()))
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


def _parse(content, source):
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
        _require(len({_digest(ref) for ref in refs}) == len(refs))
    except (ValueError, TypeError, KeyError):
        raise ProviderError('eval_contract_invalid') from None
    for ref in refs:
        try:
            validate_relative(ref['file'])
            _require(any(ref['file'] == s['file'] and s['start'] <= ref['start'] <= ref['end'] <= s['end'] for s in source))
        except (ContractError, ValueError):
            raise ProviderError('eval_citation_invalid') from None
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
        'failure_class': 'semantic_pending', 'diagnostic_passed': False}
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
        row['answer'] = _parse(response.content, _object(request['messages'][-1]['content'])['source'])
    except ProviderError as failure:
        row.update(error_code=failure.code, failure_class='format'
            if response is not None and failure.code in FORMAT_ERRORS else 'infrastructure')
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


def compare_results(out, *, reviews=None):
    """Independent human dimensions; JSON parsing never awards a semantic pass."""
    _, manifest, reference, state, rows = _load(out)
    reviews = {} if reviews is None else reviews
    _require(type(reviews) is dict and set(reviews) <= {r['cell'] for r in rows})
    for row in rows:
        answer, review = row['answer'], reviews.get(row['cell'])
        row['semantic_review'] = 'pending' if review is None else 'reviewed'
        row['expected_verdict'] = reference['cases'][row['case_id']]
        row['verdict_correct'] = answer['verdict'] == row['expected_verdict'] if answer else None
        row['healthy_false_alarm'] = answer['verdict'] == 'supported_defect' if answer and row['expected_verdict'] == 'no_defect_supported' else None
        row['unknown_abstention'] = answer['verdict'] == 'unknown' if answer and row['expected_verdict'] == 'unknown' else None
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
        row.update(review=review, diagnostic_passed=bool(row['verdict_correct'] and citation and semantic))
        row['failure_class'] = None if row['diagnostic_passed'] else 'semantic'
    models = []
    for profile in manifest['profiles']:
        selected = [r for r in rows if r['profile_id'] == profile['profile_id']]
        models.append({'profile': profile, 'expected': len(manifest['cases']), 'recorded': len(selected),
            'diagnostic_passes': sum(r['diagnostic_passed'] for r in selected),
            **{name: sum(r['failure_class'] == name for r in selected)
               for name in ('infrastructure', 'format', 'semantic', 'semantic_pending')}})
    return {'schema_version': 1, 'prompt_version': manifest['prompt_version'], 'dataset_version': manifest['dataset_version'],
        'manifest_sha256': state['manifest_sha256'], 'reference_sha256': manifest['reference_sha256'],
        'cases': rows, 'models': models, 'stopped': state['stopped'], 'uncertain_cell': state['pending'],
        'new_model_calls': 0, 'real_model_accuracy_measured': False,
        'measurement': 'Offline captured-response review; scripted proofs are fixtures; native claims need independent owner audit.'}


def save_comparison(out, report):
    out, _, _, _, _ = _load(out)
    reviews = {row['cell']: row['review'] for row in report['cases'] if 'review' in row}
    _require(encoded(report) == encoded(compare_results(out, reviews=reviews)), 'comparison_report_drift')
    folder = out / ('report-' + str(uuid4()))
    lines = ['# Diagnostic comparison', '', report['measurement'], '',
             f"Prompt: {report['prompt_version']}; dataset: {report['dataset_version']}", '',
             '| Model | Recorded | Pass | Infrastructure | Format/contract | Semantic | Pending |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for model in report['models']:
        lines.append('| ' + ' | '.join(str(value) for value in [model['profile']['profile_id'], model['recorded'],
            model['diagnostic_passes'], model['infrastructure'], model['format'], model['semantic'], model['semantic_pending']]) + ' |')
    lines += ['', '| Cell | Origin | Outcome | Error |', '| --- | --- | --- | --- |']
    for row in report['cases']:
        lines.append(f"| {row['cell']} | {row['provenance']} | {row['failure_class'] or 'reviewed pass'} | {row['error_code'] or ''} |")
    data = ('\n'.join(lines) + '\n').encode('utf-8')
    _require(len(encoded(report)) + len(data) < RECORD_LIMIT // 2)
    folder.mkdir()
    _write(out, folder.name + '/comparison.json', report)
    _require(sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) + 2 * len(data) <= RECORD_LIMIT)
    atomic_write(folder / 'comparison.md', data)
    return {'json': folder / 'comparison.json', 'markdown': folder / 'comparison.md'}
