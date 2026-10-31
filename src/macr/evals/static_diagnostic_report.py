"""Pure closed-stage accounting and custody-only semantic aggregation.

This module reads no files, calls no model and receives no reference answers.
Private qualifications and the anonymous keeper join are supplied only by custody.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from macr.evals.deepseek_policy_spend import _usage_evidence
from macr.evals.deepseek_study_report import (
    FLAGS, _flags, _money, _require, _text, _usd, result_digest)
from macr.providers.base import validate_billing_evidence, validate_identity_evidence

PROFILE = 'static-diagnostic-v1'
ARMS = ('direct', 'evidence')
STATUSES = frozenset({'semantic_pending', 'contract_failed', 'failed', 'unknown', 'not_attempted'})
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_USAGE = ('input_tokens', 'cache_hit_tokens', 'cache_miss_tokens', 'output_tokens', 'reasoning_tokens')
_PREPARATION = ('common_seconds', 'evidence_seconds', 'packet_bytes', 'wire_bytes')
_COVERAGE = ('cells_with_packet', 'facts', 'bindings', 'relations', 'unknowns', 'truncations', 'selection_gaps')
_BLINDING = ('Source-only judgments must be locked before keeper joining. Output content '
             'may reveal strategy; role/directory separation is not OS isolation or full blinding.')


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _model(plan):
    model = plan.get('model')
    _require(type(model) is dict and set(model) == {
        'provider', 'model_id', 'thinking', 'reasoning_effort'}
        and model['provider'] == 'deepseek' and model['model_id'] == 'deepseek-flash'
        and model['thinking'] is True and model['reasoning_effort'] == 'high',
        'static_report_model_invalid')
    return model['model_id']


def _grid(plan, run, results):
    _require(type(plan) is dict and plan.get('schema') == 'macr.static-diagnostic.stage.v1'
        and plan.get('profile') == PROFILE and _hash(plan.get('plan_sha256')),
        'static_report_plan_invalid')
    _model(plan)
    phases = plan.get('phases')
    _require(type(phases) is dict and all(type(phases.get(p)) is dict for p in ('S0', 'S1')),
             'static_report_grid_invalid')
    tasks = phases['S1'].get('tasks')
    _require(type(tasks) is list and len(tasks) == 9, 'static_report_grid_invalid')
    by_task = {}
    for task in tasks:
        _require(type(task) is dict and _text(task.get('id')) and _text(task.get('project_id'))
            and task['id'] not in by_task, 'static_report_task_invalid')
        by_task[task['id']] = task
    all_cells, selected, pairs = {}, {}, set()
    for phase, expected in (('S0', 6), ('S1', 18)):
        cells = phases[phase].get('cells')
        _require(type(cells) is list and len(cells) == expected, 'static_report_grid_invalid')
        for cell in cells:
            _require(type(cell) is dict and _text(cell.get('cell_id'))
                and cell['cell_id'] not in all_cells and cell.get('phase') == phase
                and cell.get('arm') in ARMS, 'static_report_grid_invalid')
            all_cells[cell['cell_id']] = cell
            if phase == 'S1':
                _require(cell.get('task_id') in by_task
                    and cell.get('project_id') == by_task[cell['task_id']]['project_id']
                    and (cell['task_id'], cell['arm']) not in pairs, 'static_report_grid_invalid')
                pairs.add((cell['task_id'], cell['arm']))
                selected[cell['cell_id']] = cell
    _require(pairs == {(task, arm) for task in by_task for arm in ARMS}, 'static_report_grid_invalid')
    _require(type(run) is dict and run.get('schema') == 'macr.static-diagnostic.execution.v1'
        and run.get('profile') == PROFILE and run.get('plan_sha256') == plan['plan_sha256']
        and run.get('planned_cells') == 24 and type(run.get('s0_qualified')) is bool
        and type(run.get('stage_halted')) is bool, 'static_report_run_invalid')
    rows = run.get('rows')
    _require(type(rows) is list and len(rows) == 24 and type(results) is dict
        and set(results) <= set(all_cells), 'static_report_closed_run_invalid')
    by_row = {}
    for row in rows:
        _require(type(row) is dict and row.get('cell_id') in all_cells
            and row['cell_id'] not in by_row and row.get('status') in STATUSES,
            'static_report_closed_run_invalid')
        cell_id = row['cell_id']
        cell = all_cells[cell_id]
        _require(all(row.get(k) == cell[k] for k in ('task_id', 'project_id', 'phase', 'arm')),
                 'static_report_run_identity')
        result = results.get(cell_id)
        if result is None:
            _require(row['status'] == 'not_attempted' and row.get('result_sha256') is None,
                     'static_report_result_missing')
        else:
            _require(type(result) is dict and result.get('schema') == 'macr.static-diagnostic.cell.v1'
                and result.get('profile') == PROFILE
                and all(result.get(k) == cell[k] for k in ('cell_id', 'task_id', 'project_id', 'phase', 'arm'))
                and result.get('status') == row['status'], 'static_report_result_identity')
            _require(row.get('result_sha256') == result_digest(result), 'static_report_result_binding')
        by_row[cell_id] = row
    _require(set(by_row) == set(all_cells), 'static_report_closed_run_invalid')
    result_digest(run)
    return by_task, selected


def _packets(plan, run, results, cells):
    run_sha = result_digest(run)
    packets, mapping = [], {}
    for cell_id, cell in cells.items():
        result = results.get(cell_id)
        if result is None or result['status'] != 'semantic_pending':
            continue
        _require(type(result.get('answer')) is dict and type(result.get('source')) is list,
                 'static_report_reviewable_invalid')
        task = next(t for t in plan['phases']['S1']['tasks'] if t['id'] == cell['task_id'])
        _require(all(result.get(k) == task.get(k) for k in ('question', 'scenario', 'assumptions')),
                 'static_report_task_binding')
        result_sha = result_digest(result)
        review_id = result_digest({'run_sha256': run_sha, 'cell_id': cell_id, 'result_sha256': result_sha})
        mapping[review_id] = cell_id
        packets.append({'review_id': review_id, 'result_sha256': result_sha,
            **deepcopy({k: result[k] for k in ('question', 'scenario', 'assumptions', 'source', 'answer')})})
    # Hash order avoids disclosing the frozen dispatch/arm ordering to raters.
    packets.sort(key=lambda row: row['review_id'])
    return {'schema': 'macr.static-diagnostic.anonymous-packets.v1',
        'plan_sha256': plan['plan_sha256'], 'run_sha256': run_sha,
        'packets': packets, 'keeper_mapping': mapping, 'blinding_limit': _BLINDING}


def export_packets(plan, run, results):
    """Export reviewable S1 answers after whole-stage closure; keep mapping private."""
    _, cells = _grid(plan, run, results)
    return _packets(plan, run, results, cells)


def _judgments(plan, run, results, cells, score_bundle, qualifications, keeper_mapping):
    if score_bundle is None:
        _require(qualifications is None and keeper_mapping is None, 'static_report_scores_missing')
        return {}, {}
    expected = _packets(plan, run, results, cells)
    _require(type(score_bundle) is dict and set(score_bundle) == {
        'schema', 'run_sha256', 'reviews', 'determinate_reviews'}
        and score_bundle['schema'] == 'macr.static-diagnostic.review-import.v1'
        and score_bundle['run_sha256'] == result_digest(run), 'static_report_review_run_binding')
    _require(type(keeper_mapping) is dict and keeper_mapping == expected['keeper_mapping'],
             'static_report_keeper_binding')
    tasks = {cell['task_id'] for cell in cells.values()}
    _require(type(qualifications) is dict and set(qualifications) == tasks,
             'static_report_qualification_invalid')
    for value in qualifications.values():
        _require(type(value) is dict and set(value) == {'category', 'stratum', 'valid', 'reason'}
            and value['category'] in ('defect', 'healthy', 'external_gap')
            and value['stratum'] == 'full_context' and type(value['valid']) is bool
            and _text(value['reason']), 'static_report_qualification_invalid')
    _require(all(sum(q['category'] == category for q in qualifications.values()) == 3
                 for category in ('defect', 'healthy', 'external_gap')), 'static_report_qualification_balance')
    reviews, determinate = score_bundle['reviews'], score_bundle['determinate_reviews']
    _require(type(reviews) is dict and type(determinate) is dict
        and set(reviews) == set(determinate) == set(keeper_mapping), 'static_report_review_set')
    judgments = {}
    for review_id, cell_id in keeper_mapping.items():
        result, review, support = results[cell_id], reviews[review_id], determinate[review_id]
        _require(type(review) is dict and set(review) == {'result_sha256', 'status', 'rationale', 'flags'}
            and review['status'] == 'locked' and _text(review['rationale']), 'static_report_review_invalid')
        _require(review['result_sha256'] == result_digest(result), 'static_report_review_result_binding')
        flags = _flags(review['flags'])
        _require(not (flags['reasonable_abstention'] and flags['partial']), 'review_flags_contradictory')
        _require(type(support) is dict and set(support) == {
            'result_sha256', 'review_sha256', 'status', 'determinate_error', 'rationale'}
            and support['status'] == 'locked' and _text(support['rationale'])
            and type(support['determinate_error']) is bool, 'static_report_determinate_invalid')
        _require(support['result_sha256'] == result_digest(result)
            and support['review_sha256'] == result_digest(review), 'static_report_determinate_binding')
        category = qualifications[result['task_id']]['category']
        verdict = result['answer'].get('verdict')
        _require(verdict in ('supported_defect', 'no_defect_supported', 'unknown'),
                 'static_report_answer_conflict')
        abstained = verdict == 'unknown'
        _require(flags['false_positive'] == (not abstained and category == 'healthy'
                and verdict == 'supported_defect')
            and flags['false_release'] == (not abstained and category == 'defect'
                and verdict == 'no_defect_supported'), 'static_report_answer_conflict')
        _require(not (flags['full_solution'] and (category == 'external_gap' or verdict !=
                ('supported_defect' if category == 'defect' else 'no_defect_supported')))
            and not (flags['reasonable_abstention'] and category != 'external_gap')
            and not (flags['unnecessary_abstention'] and category == 'external_gap')
            and not (any(flags[k] for k in ('reasonable_abstention', 'unnecessary_abstention',
                'arm_evidence_gap')) and not abstained), 'static_report_answer_conflict')
        error = support['determinate_error']
        _require(not (abstained and error) and not (flags['full_solution'] and error)
            and not ((flags['false_positive'] or flags['false_release'] or
                (not abstained and category == 'external_gap')) and not error),
                 'static_report_determinate_conflict')
        judgments[cell_id] = {'flags': deepcopy(flags), 'determinate_error': error}
    return qualifications, judgments


def _response_evidence(result, model):
    response = result.get('response')
    if type(response) is not dict:
        return None, None
    fingerprint = None
    if response.get('state') == 'received' and response.get('model') == model:
        fp = response.get('system_fingerprint')
        if type(fp) is str and 0 < len(fp) <= 256 and fp.strip():
            fingerprint = fp
    identity = response.get('identity_evidence')
    if fingerprint is None and identity is not None:
        try:
            identity = validate_identity_evidence(identity)
        except ValueError:
            identity = {}
        if (identity.get('request_sha256') == result.get('request_sha256')
                and identity.get('requested_model') == model
                and identity.get('model_state') == identity.get('fingerprint_state') == 'validated'):
            fingerprint = identity['system_fingerprint']
    raw_usage = response.get('raw_usage') if response.get('state') == 'received' else None
    if raw_usage is None and response.get('billing_evidence') is not None:
        try:
            billing = validate_billing_evidence(response['billing_evidence'])
        except ValueError:
            billing = {}
        if (billing.get('request_sha256') == result.get('request_sha256')
                and billing.get('model') == model and billing.get('max_output_tokens') == 8192):
            raw_usage = billing['raw_usage']
    return raw_usage, fingerprint


def _actual(result, model):
    if result is None:
        return {'requests': 0, **dict.fromkeys(_USAGE, 0), 'requests_with_unknown_usage': 0,
            'known_accrued_usd': '0.000000', 'unknown_held_usd': '0.000000',
            'elapsed_seconds': 0., 'standalone_elapsed_seconds': 0.,
            'preparation': dict.fromkeys(_PREPARATION, 0),
            'coverage': dict.fromkeys(_COVERAGE, 0),
            'captured_files': 0, 'source_bytes': 0, 'provider_seconds': 0.,
            'model_calls_live': 0, 'fixture_model_calls': 0}, None, True
    accounting, prep = result.get('accounting'), result.get('preparation')
    _require(type(accounting) is dict and type(prep) is dict, 'static_report_accounting_invalid')
    count = accounting.get('request_count')
    _require(type(count) is int and count in (0, 1), 'static_report_accounting_invalid')
    known, held = _usd(accounting.get('known_accrued_usd')), _usd(accounting.get('unknown_held_usd'))
    elapsed, standalone = result.get('elapsed_seconds'), result.get('standalone_elapsed_seconds')
    _require(_finite(elapsed) and _finite(standalone), 'static_report_timing_invalid')
    preparation = {k: prep.get(k) for k in _PREPARATION}
    _require(all(_finite(v) for v in preparation.values()), 'static_report_timing_invalid')
    if result['arm'] == 'direct':
        preparation.update(evidence_seconds=0., packet_bytes=0)
    live, fake = result.get('model_calls_live'), result.get('fixture_model_calls')
    _require(type(live) is int and type(fake) is int and live >= 0 and fake >= 0
        and live + fake <= count, 'static_report_consumption_invalid')
    raw, fingerprint = _response_evidence(result, model)
    evidence = _usage_evidence(raw, 8192) if count else None
    usage = dict.fromkeys(_USAGE, None if count else 0)
    if evidence is not None:
        normalized = evidence[1]
        usage = dict(zip(_USAGE, (normalized['prompt_tokens'], normalized['prompt_cache_hit_tokens'],
            normalized['prompt_cache_miss_tokens'], normalized['completion_tokens'], normalized['reasoning_tokens'])))
    metered = (known is not None and held == 0 and
        (count == 0 and known == 0 or count == 1 and evidence is not None
         and known == evidence[0] and usage['input_tokens'] <= 8000))
    packet = result.get('evidence_packet')
    coverage = {'cells_with_packet': int(type(packet) is dict)}
    for key in _COVERAGE[1:]:
        value = packet.get(key) if type(packet) is dict else None
        coverage[key] = (len(value) if type(value) is list else None) if result['arm'] == 'evidence' else 0
    operations = prep.get('operations', {})
    _require(type(operations) is dict, 'static_report_consumption_invalid')
    captured, source_bytes = (operations.get(k) for k in ('full_source_files_read', 'full_source_bytes_read'))
    _require(all(v is None or type(v) is int and v >= 0 for v in (captured, source_bytes)),
             'static_report_consumption_invalid')
    response = result.get('response')
    latency = response.get('latency_ms') if type(response) is dict else None
    if latency is None and type(response) is dict and type(response.get('billing_evidence')) is dict:
        latency = response['billing_evidence'].get('latency_ms')
    _require(latency is None or _finite(latency), 'static_report_timing_invalid')
    return {'requests': count, **usage, 'requests_with_unknown_usage': int(count > 0 and evidence is None),
        'known_accrued_usd': _money(known), 'unknown_held_usd': _money(held),
        'elapsed_seconds': elapsed, 'standalone_elapsed_seconds': standalone,
        'preparation': preparation, 'coverage': coverage, 'captured_files': captured,
        'source_bytes': source_bytes, 'provider_seconds': None if latency is None else latency / 1000,
        'model_calls_live': live, 'fixture_model_calls': fake}, fingerprint, metered


def _sum_actual(rows):
    actuals = [row['actual'] for row in rows]
    summed = {}
    for key in ('requests', *_USAGE, 'requests_with_unknown_usage', 'elapsed_seconds',
                'standalone_elapsed_seconds', 'model_calls_live', 'fixture_model_calls',
                'captured_files', 'source_bytes', 'provider_seconds'):
        values = [actual[key] for actual in actuals]
        summed[key] = None if any(v is None for v in values) else sum(values)
    for key in ('known_accrued_usd', 'unknown_held_usd'):
        values = [_usd(actual[key]) for actual in actuals]
        summed[key] = _money(None if any(v is None for v in values) else sum(values))
    summed['preparation'] = {k: sum(a['preparation'][k] for a in actuals)
        for k in _PREPARATION}
    summed['coverage'] = {key: (sum(a['coverage'][key] for a in actuals)
        if all(a['coverage'][key] is not None for a in actuals) else None) for key in _COVERAGE}
    return summed


def _metric(numerator, denominator, settled):
    return {'numerator': numerator, 'denominator': denominator, 'state': 'final' if settled else 'pending'}


def _accounting_fields(value):
    _require(type(value) is dict, 'static_report_stage_accounting_invalid')
    selected = {key: value.get(key) for key in
        ('request_count', 'model_calls_live', 'fixture_model_calls')}
    _require(all(v is None or type(v) is int and v >= 0 for v in selected.values()),
             'static_report_stage_accounting_invalid')
    for key in ('known_accrued_usd', 'unknown_held_usd'):
        selected[key] = _money(_usd(value.get(key)))
    return selected


def _stage_accounting(run):
    """Keep S0 delivery costs visible, separately from S1 semantic denominators."""
    total = _accounting_fields({'request_count': run.get('actual_requests'),
        'model_calls_live': run.get('model_calls_live'),
        'fixture_model_calls': run.get('fixture_model_calls'),
        'known_accrued_usd': run.get('known_accrued_usd'),
        'unknown_held_usd': run.get('new_unknown_held_usd')})
    total['actual_requests'] = total.pop('request_count')
    phases = run.get('phases')
    _require(type(phases) is dict and all(type(phases.get(p)) is dict for p in ('S0', 'S1')),
             'static_report_stage_accounting_invalid')
    total['phases'] = {p: _accounting_fields(phases[p].get('accounting', {})) for p in ('S0', 'S1')}
    return total


def _summarize(rows, settled):
    counts = {state: sum(r['state'] == state for r in rows)
        for state in ('reviewed', 'review_pending', 'contract_failed', 'failed', 'unknown', 'not_attempted')}
    for kind in ('transport', 'identity'):
        counts[kind + '_failed'] = sum(r['failure_kind'] == kind for r in rows)
    flags = {flag: sum(r['judgment'].get('flags', {}).get(flag, False) and r['qualification_valid']
                      for r in rows) for flag in FLAGS}
    determinate = sum(r['judgment'].get('determinate_error', False) and r['qualification_valid'] for r in rows)
    metrics = {name: _metric(n, d, settled) for name, n, d in (
        ('complete_solvable', flags['full_solution'], 6),
        ('necessary_abstention', flags['reasonable_abstention'], 3),
        ('unnecessary_abstention', flags['unnecessary_abstention'], 6),
        ('false_positive', flags['false_positive'], 3), ('false_release', flags['false_release'], 3),
        ('partial', flags['partial'], 9), ('arm_evidence_gap', flags['arm_evidence_gap'], 9),
        ('unsupported_determinate', determinate, 9),
        ('disposition_all', flags['full_solution'] + flags['reasonable_abstention'], 9),
        ('conditional_reviewed_disposition', flags['full_solution'] + flags['reasonable_abstention'],
            sum(r['state'] == 'reviewed' and r['qualification_valid'] for r in rows)))}
    return {'planned_cells': 9, 'counts': counts, 'metrics': metrics, 'actual': _sum_actual(rows)}


def build_report(plan, run, results, *, score_bundle=None, qualifications=None, keeper_mapping=None):
    """Account for all 18 S1 cells; only locked custody records earn semantic credit."""
    tasks, cells = _grid(plan, run, results)
    qualifications, judgments = _judgments(plan, run, results, cells,
        score_bundle, qualifications, keeper_mapping)
    rows = []
    for cell_id, cell in cells.items():
        result = results.get(cell_id)
        actual, fingerprint, metered = _actual(result, _model(plan))
        status = result['status'] if result is not None else 'not_attempted'
        state = ('reviewed' if cell_id in judgments else 'review_pending') if status == 'semantic_pending' else status
        code = result.get('error_code') if result is not None else None
        failure_kind = None
        if type(code) is str and ('identity' in code or 'fingerprint' in code):
            failure_kind = 'identity'
        elif type(code) is str and any(k in code for k in ('timeout', 'transport', 'http', 'provider_unavailable')):
            failure_kind = 'transport'
        rows.append({**{k: cell[k] for k in ('cell_id', 'task_id', 'project_id', 'arm')},
            'status': status, 'state': state, 'error_code': code, 'failure_kind': failure_kind,
            'result_sha256': result_digest(result) if result is not None else None,
            'judgment': judgments.get(cell_id, {}),
            'qualification_valid': qualifications.get(cell['task_id'], {}).get('valid', False),
            'source_sha256': result.get('initial_source_sha256') if result is not None else None,
            'visible_source_sha256': result_digest(result['source']) if result is not None
                and type(result.get('source')) is list else None,
            'identity_fingerprint': fingerprint, 'identity_verified': fingerprint is not None,
            'metering_verified': metered, 'actual': actual})
    qualified = len(qualifications) == 9 and all(q['valid'] for q in qualifications.values())
    locked = score_bundle is not None and all(r['state'] != 'review_pending' for r in rows)
    strategies = {arm: _summarize([r for r in rows if r['arm'] == arm], qualified and locked) for arm in ARMS}
    pairs = []
    for task_id in tasks:
        direct, evidence = (next(r for r in rows if r['task_id'] == task_id and r['arm'] == arm) for arm in ARMS)
        source_ok = (_hash(direct['source_sha256']) and direct['source_sha256'] == evidence['source_sha256']
            and _hash(direct['visible_source_sha256'])
            and direct['visible_source_sha256'] == evidence['visible_source_sha256'])
        identity_ok = (direct['identity_verified'] and evidence['identity_verified']
            and direct['identity_fingerprint'] == evidence['identity_fingerprint'])
        comparable = (all(r['state'] == 'reviewed' and r['qualification_valid']
            and r['metering_verified'] for r in (direct, evidence)) and source_ok and identity_ok)
        outcome = 'not_comparable'
        if comparable:
            flag = 'reasonable_abstention' if qualifications[task_id]['category'] == 'external_gap' else 'full_solution'
            d, e = (int(r['judgment']['flags'][flag]) for r in (direct, evidence))
            outcome = 'win' if e > d else 'loss' if e < d else 'tie'
        pairs.append({'task_id': task_id, 'outcome': outcome, 'source_shared': source_ok,
            'identity_shared': identity_ok, 'delivery_disposition': {
                arm: int(any(row['judgment'].get('flags', {}).get(k, False)
                    for k in ('full_solution', 'reasonable_abstention'))) if row['qualification_valid'] else 0
                for arm, row in zip(ARMS, (direct, evidence))}})
    pair_counts = {kind: sum(p['outcome'] == kind for p in pairs)
                   for kind in ('win', 'loss', 'tie', 'not_comparable')}
    fingerprints = {r['identity_fingerprint'] for r in rows if r['identity_verified']}
    readiness = {'s0_qualified': run['s0_qualified'], 'stage_not_halted': not run['stage_halted'],
        'no_operational_failure': all(r['status'] in ('semantic_pending', 'contract_failed') for r in rows),
        'all_18_attempted': all(r['actual']['requests'] == 1 and r['status'] != 'not_attempted' for r in rows),
        'at_most_one_contract_failure': sum(r['state'] == 'contract_failed' for r in rows) <= 1,
        'all_reviewables_locked': locked, 'qualifications_valid': qualified,
        'shared_source': all(p['source_shared'] for p in pairs),
        'identity_continuous': len(fingerprints) == 1 and all(r['identity_verified'] for r in rows),
        'metering_complete': all(r['metering_verified'] for r in rows),
        'live_evidence': run.get('provenance') == 'approved_live'
            and all(r['actual']['model_calls_live'] == 1 for r in rows)}
    dactual, eactual = (strategies[arm]['actual'] for arm in ARMS)
    dcost, ecost = _usd(dactual['known_accrued_usd']), _usd(eactual['known_accrued_usd'])
    net = pair_counts['win'] - pair_counts['loss']
    checks = {'net_semantic_pairs_at_least_2': net >= 2,
        'no_added_unsupported': all(not e['judgment'].get('determinate_error', False)
            or d['judgment'].get('determinate_error', False)
            for task_id in tasks
            for d in [next(r for r in rows if r['task_id'] == task_id and r['arm'] == 'direct')]
            for e in [next(r for r in rows if r['task_id'] == task_id and r['arm'] == 'evidence')]),
        'cost_within_1_25': dcost is not None and ecost is not None and 4 * ecost <= 5 * dcost,
        'elapsed_within_1_25': eactual['standalone_elapsed_seconds'] <= 1.25 * dactual['standalone_elapsed_seconds']}
    ready = all(readiness.values())
    decision = ('advance_evidence_research' if all(checks.values()) else 'continue_direct_baseline') if ready else 'inconclusive'
    return {'schema': 'macr.static-diagnostic.report.v1', 'profile': PROFILE,
        'plan_sha256': plan['plan_sha256'], 'run_sha256': result_digest(run),
        'planned_cells': 18, 'task_observations': 9, 'cells': rows, 'pairs': pairs,
        'pair_counts': pair_counts, 'strategies': strategies, 'stage_accounting': _stage_accounting(run),
        'additional_input_tokens': None if any(a['input_tokens'] is None for a in (dactual, eactual))
            else eactual['input_tokens'] - dactual['input_tokens'],
        'selection': {'decision': decision, 'readiness': readiness, 'checks': checks,
            'net_semantic_pairs': net,
            'interpretation': 'Bounded engineering decision; no production reliability or general quality gain claim.'},
        'first_answers_are_additional_samples': False, 'blinding_limit': _BLINDING}


def sanitize_report(report):
    """Return only known aggregate fields; never publish case records or keeper data."""
    _require(type(report) is dict and report.get('schema') == 'macr.static-diagnostic.report.v1',
             'static_report_invalid')
    public = deepcopy({key: report[key] for key in ('schema', 'profile', 'plan_sha256', 'run_sha256',
        'planned_cells', 'task_observations', 'additional_input_tokens',
        'first_answers_are_additional_samples', 'blinding_limit')})
    public['pair_counts'] = {k: report['pair_counts'][k] for k in ('win', 'loss', 'tie', 'not_comparable')}
    accounting = report['stage_accounting']
    public['stage_accounting'] = {k: accounting[k] for k in ('actual_requests', 'model_calls_live',
        'fixture_model_calls', 'known_accrued_usd', 'unknown_held_usd')}
    public['stage_accounting']['phases'] = {
        p: _accounting_fields(accounting['phases'][p]) for p in ('S0', 'S1')}
    public['strategies'] = {}
    for arm in ARMS:
        value = report['strategies'][arm]
        actual = value['actual']
        clean_actual = {k: actual[k] for k in ('requests', *_USAGE, 'requests_with_unknown_usage',
            'known_accrued_usd', 'unknown_held_usd', 'elapsed_seconds', 'standalone_elapsed_seconds',
            'model_calls_live', 'fixture_model_calls', 'captured_files', 'source_bytes', 'provider_seconds')}
        clean_actual['preparation'] = {k: actual['preparation'][k] for k in _PREPARATION}
        clean_actual['coverage'] = {k: actual['coverage'][k] for k in _COVERAGE}
        public['strategies'][arm] = {'planned_cells': value['planned_cells'],
            'counts': {k: value['counts'][k] for k in ('reviewed', 'review_pending', 'contract_failed',
                'failed', 'unknown', 'not_attempted', 'transport_failed', 'identity_failed')},
            'metrics': {key: {k: value['metrics'][key][k] for k in ('numerator', 'denominator', 'state')}
                for key in ('complete_solvable', 'necessary_abstention', 'unnecessary_abstention',
                    'false_positive', 'false_release', 'partial', 'arm_evidence_gap', 'unsupported_determinate',
                    'disposition_all', 'conditional_reviewed_disposition')}, 'actual': clean_actual}
    selection = report['selection']
    public['selection'] = {k: selection[k] for k in ('decision', 'net_semantic_pairs', 'interpretation')}
    public['selection']['readiness'] = {k: selection['readiness'][k] for k in ('s0_qualified',
        'stage_not_halted', 'no_operational_failure', 'all_18_attempted', 'at_most_one_contract_failure', 'all_reviewables_locked',
        'qualifications_valid', 'shared_source', 'identity_continuous', 'metering_complete', 'live_evidence')}
    public['selection']['checks'] = {k: selection['checks'][k] for k in ('net_semantic_pairs_at_least_2',
        'no_added_unsupported', 'cost_within_1_25', 'elapsed_within_1_25')}
    return deepcopy(public)
