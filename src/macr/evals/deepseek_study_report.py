"""Pure, receipt-bound study accounting and hidden-label review export.

This adapter opens no files. Only a subsequent custodian merge supplies task
categories and final semantic judgments; accepted JSON alone never earns credit.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING
from hashlib import sha256
import json
import math
import re
from macr.evals.deepseek_study_generation import get_generation

STRATEGIES = ('direct_initial_3', 'fixed_read_3', 'adaptive_read_3', 'direct_full_3')
FLAGS = frozenset({'full_solution', 'reasonable_abstention', 'partial',
    'unnecessary_abstention', 'false_positive', 'false_release', 'arm_evidence_gap'})
REVIEWABLE = frozenset({'semantic_pending', 'ask_user_terminated'})
_CONTRACT_ERRORS = frozenset({'model_response_invalid', 'model_output_incomplete',
    'model_output_invalid', 'diagnosis_json_invalid', 'eval_contract_invalid',
    'eval_citation_invalid', 'unexpected_model_action', 'tool_call_invalid',
    'terminal_action_missing', 'duplicate_tool_call', 'third_decision_must_terminate',
    'mixed_terminal_action', 'ask_user_contract_invalid', 'finish_contract_invalid',
    'duplicate_read_tool', 'read_tool_arguments_invalid',
    'read_tool_limit_or_forbidden', 'read_tool_budget_exhausted'})
_USAGE_KEYS = ('input_tokens', 'cache_hit_tokens', 'cache_miss_tokens',
               'output_tokens', 'reasoning_tokens')
_MONEY = re.compile(r'(?:0|[1-9][0-9]*)\.[0-9]{6}\Z')


def _require(value, code):
    if not value:
        raise ValueError(code)


def result_digest(value: dict) -> str:
    """Bind a review to the exact, unchanged result, including its receipts."""
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError('report_json_invalid') from None
    return sha256(raw).hexdigest()


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 8000


def _study(study, results):
    _require(type(study) is dict and type(results) is dict, 'study_grid_invalid')
    tasks, cells = study.get('tasks'), study.get('cells')
    _require(type(tasks) is list and 0 < len(tasks) <= 9 and type(cells) is list,
             'study_grid_invalid')
    by_task, by_cell, pairs = {}, {}, set()
    for task in tasks:
        _require(type(task) is dict and _text(task.get('id')) and
                 _text(task.get('project_id')), 'study_task_invalid')
        _require(task['id'] not in by_task, 'study_grid_invalid')
        by_task[task['id']] = task
    for cell in cells:
        _require(type(cell) is dict and _text(cell.get('cell_id')) and
            cell.get('task_id') in by_task and cell.get('strategy') in STRATEGIES and
            cell.get('project_id') == by_task[cell['task_id']]['project_id'], 'study_grid_invalid')
        pair = (cell['task_id'], cell['strategy'])
        _require(cell['cell_id'] not in by_cell and pair not in pairs, 'study_grid_invalid')
        by_cell[cell['cell_id']] = cell
        pairs.add(pair)
    _require(pairs == {(task, strategy) for task in by_task for strategy in STRATEGIES},
             'study_grid_invalid')
    _require(set(results) <= set(by_cell), 'result_cell_unknown')
    for cell_id, result in results.items():
        cell = by_cell[cell_id]
        _require(type(result) is dict and result.get('task_id') == cell['task_id'] and
                 result.get('strategy') == cell['strategy'], 'result_identity_mismatch')
        result_digest(result)
    return by_task, by_cell


def _flags(value):
    _require(type(value) is dict and set(value) == FLAGS and
             all(type(flag) is bool for flag in value.values()), 'review_flags_invalid')
    _require(not (value['full_solution'] and any(value[key] for key in FLAGS - {'full_solution'})),
             'review_flags_contradictory')
    _require(not (value['reasonable_abstention'] and
        any(value[key] for key in ('unnecessary_abstention', 'false_positive', 'false_release'))),
        'review_flags_contradictory')
    return value


def _review_inputs(tasks, cells, results, reviews, adjudications):
    _require(type(reviews) is dict and set(reviews) <= set(cells), 'review_cell_unknown')
    _require(type(adjudications) is dict and set(adjudications) <= {'tasks', 'cells'},
             'adjudication_invalid')
    qualifications = adjudications.get('tasks', {})
    decisions = adjudications.get('cells', {})
    _require(type(qualifications) is dict and set(qualifications) <= set(tasks),
             'adjudication_task_unknown')
    _require(type(decisions) is dict and set(decisions) <= set(reviews),
             'adjudication_cell_unknown')
    for value in qualifications.values():
        _require(type(value) is dict and set(value) == {'category', 'stratum', 'valid', 'reason'}
            and value['category'] in {'defect', 'healthy', 'external_gap'}
            and value['stratum'] in {'full_context', 'retrieval_required'}
            and type(value['valid']) is bool and _text(value['reason']),
            'adjudication_task_invalid')
    for cell_id, review in reviews.items():
        result = results.get(cell_id)
        _require(result is not None and result.get('status') in REVIEWABLE,
                 'review_nonreviewable_cell')
        _require(type(review) is dict and set(review) == {'result_sha256', 'status',
            'rationale', 'flags'} and review['status'] == 'locked' and _text(review['rationale']),
            'review_invalid')
        _require(review['result_sha256'] == result_digest(result), 'review_result_binding')
        _flags(review['flags'])
    for cell_id, decision in decisions.items():
        _require(type(decision) is dict and set(decision) == {'result_sha256',
            'review_sha256', 'flags', 'rationale'} and _text(decision['rationale']),
            'adjudication_cell_invalid')
        _require(decision['result_sha256'] == result_digest(results[cell_id]),
                 'adjudication_result_binding')
        _require(decision['review_sha256'] == result_digest(reviews[cell_id]),
                 'adjudication_review_binding')
        flags = _flags(decision['flags'])
        qualification = qualifications.get(cells[cell_id]['task_id'])
        _require(qualification is not None, 'adjudication_task_missing')
        category = qualification['category']
        _require(not (flags['full_solution'] and category == 'external_gap') and
            not (flags['reasonable_abstention'] and category != 'external_gap') and
            not (flags['false_positive'] and category != 'healthy') and
            not (flags['false_release'] and category != 'defect') and
            not (flags['unnecessary_abstention'] and category == 'external_gap'),
            'adjudication_category_conflict')
        answer = results[cell_id].get('answer')
        verdict = answer.get('verdict') if type(answer) is dict else None
        abstained = results[cell_id].get('status') == 'ask_user_terminated' or verdict == 'unknown'
        _require(flags['false_positive'] == (not abstained and category == 'healthy'
                    and verdict == 'supported_defect') and
                 flags['false_release'] == (not abstained and category == 'defect'
                    and verdict == 'no_defect_supported'), 'adjudication_answer_conflict')
        _require(not (flags['full_solution'] and verdict not in
                       {'supported_defect', 'no_defect_supported'}) and
                 not (flags['full_solution'] and verdict !=
                     ('supported_defect' if category == 'defect' else 'no_defect_supported')) and
                 not (any(flags[key] for key in ('reasonable_abstention',
                     'unnecessary_abstention', 'arm_evidence_gap')) and not abstained),
                 'adjudication_answer_conflict')
    return qualifications, decisions


def _number(value):
    return type(value) is int and value >= 0


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _usd(value):
    if value is None:
        return None
    _require(type(value) is str and _MONEY.fullmatch(value), 'report_money_invalid')
    try:
        return int(Decimal(value) * 1_000_000)
    except (ValueError, InvalidOperation):
        raise ValueError('report_money_invalid') from None


def _money(value):
    return None if value is None else f'{value // 1_000_000}.{value % 1_000_000:06d}'


def _request_usage(request):
    response = request.get('response')
    raw = response.get('raw_usage') if type(response) is dict else None
    if raw is None and type(response) is dict:
        billing = response.get('billing_evidence')
        raw = billing.get('raw_usage') if type(billing) is dict else None
    if type(raw) is not dict or not all(_number(raw.get(key)) for key in
                                        ('prompt_tokens', 'completion_tokens')):
        return dict.fromkeys(_USAGE_KEYS)
    input_tokens, output_tokens = raw['prompt_tokens'], raw['completion_tokens']
    hit, miss = raw.get('prompt_cache_hit_tokens'), raw.get('prompt_cache_miss_tokens')
    if not (_number(hit) and _number(miss) and hit + miss == input_tokens):
        hit, miss = None, None
    details = raw.get('completion_tokens_details')
    reasoning = details.get('reasoning_tokens') if type(details) is dict else None
    if not _number(reasoning) or reasoning > output_tokens:
        reasoning = None
    return dict(zip(_USAGE_KEYS, (input_tokens, hit, miss, output_tokens, reasoning)))


def _actual(result):
    if result is None:
        return {'requests': 0, **dict.fromkeys(_USAGE_KEYS, 0), 'tool_reads': 0,
            'requests_with_unknown_usage': 0, 'known_accrued_usd': '0.000000',
            'unknown_reserved_usd': '0.000000', 'standard_full_miss_usd': '0.000000',
            'elapsed_seconds': 0.0, 'timing_seconds': {'preparation': 0., 'provider': 0., 'tool': 0.}}
    requests = result.get('requests', [])
    _require(type(requests) is list and all(type(row) is dict for row in requests),
             'report_requests_invalid')
    usage = [_request_usage(request) for request in requests]
    actual = {'requests': len(requests), 'requests_with_unknown_usage':
              sum(row['input_tokens'] is None for row in usage)}
    for key in _USAGE_KEYS:
        actual[key] = (sum(row[key] for row in usage) if
                       all(row[key] is not None for row in usage) else None)
    accounting = result.get('accounting', {})
    _require(type(accounting) is dict, 'report_accounting_invalid')
    known = _usd(result.get('known_accrued_usd', accounting.get('known_accrued_usd')))
    reserved = _usd(result.get('unknown_reserved_usd', accounting.get('unknown_held_usd')))
    actual.update(known_accrued_usd=_money(known), unknown_reserved_usd=_money(reserved))
    miss = None if actual['input_tokens'] is None else sum(int((Decimal(row['input_tokens']) *
        Decimal('.30') + Decimal(row['output_tokens']) * Decimal('1.20')).to_integral_value(
            rounding=ROUND_CEILING)) for row in usage)
    actual['standard_full_miss_usd'] = _money(miss)
    reads, elapsed = result.get('tool_reads'), result.get('elapsed_seconds')
    _require(_number(reads) and _finite(elapsed), 'report_consumption_invalid')
    actual.update(tool_reads=reads, elapsed_seconds=elapsed)
    timing = result.get('timing_seconds', {})
    _require(type(timing) is dict, 'report_timing_invalid')
    actual['timing_seconds'] = {key: timing.get(key) for key in ('preparation', 'provider', 'tool')}
    _require(all(value is None or _finite(value) for value in actual['timing_seconds'].values()),
             'report_timing_invalid')
    return actual


def _sum_actual(rows):
    actuals = [row['actual'] for row in rows]
    result = {}
    for key in ('requests', *_USAGE_KEYS, 'tool_reads', 'requests_with_unknown_usage', 'elapsed_seconds'):
        values = [actual[key] for actual in actuals]
        result[key] = None if any(value is None for value in values) else sum(values)
    for key in ('known_accrued_usd', 'unknown_reserved_usd', 'standard_full_miss_usd'):
        values = [_usd(actual[key]) for actual in actuals]
        result[key] = _money(None if any(value is None for value in values) else sum(values))
    result['timing_seconds'] = {}
    for key in ('preparation', 'provider', 'tool'):
        values = [actual['timing_seconds'][key] for actual in actuals]
        result['timing_seconds'][key] = None if any(value is None for value in values) else sum(values)
    return result


def _state(result, actual, merged):
    if result is None:
        return 'not_attempted'
    if result.get('status') in REVIEWABLE:
        return 'reviewed' if merged else 'review_pending'
    if (result.get('status') == 'remote_outcome_unknown' or
            actual['requests_with_unknown_usage'] > 0):
        return 'unknown'
    if result.get('status') == 'failed' and result.get('error_code') in _CONTRACT_ERRORS:
        return 'contract_failed'
    return 'failed'


def _ratio(numerator, denominator, complete, settled=True):
    return {'numerator': numerator, 'denominator': denominator if complete else None,
            'state': 'final' if complete and settled else 'pending'}


def _summarize(rows, qualifications):
    complete = all(row['task_id'] in qualifications for row in rows)
    categories = {key: sum(qualifications.get(row['task_id'], {}).get('category') == key
        for row in rows) for key in ('defect', 'healthy', 'external_gap')}
    counts = {key: sum(row['state'] == key for row in rows) for key in
              ('reviewed', 'review_pending', 'contract_failed', 'unknown', 'not_attempted', 'failed')}
    totals = {key: sum(row['judgment'].get(key, False) and not row['invalid'] for row in rows)
              for key in FLAGS}
    categories_complete = complete and len(rows) > 0
    settled = categories_complete and counts['review_pending'] == 0
    solution, abstain = totals['full_solution'], totals['reasonable_abstention']
    reviewed = sum(row['state'] == 'reviewed' and not row['invalid'] for row in rows)
    metrics = {
        'complete_solvable': _ratio(solution, categories['defect'] + categories['healthy'], categories_complete, settled),
        'complete_all': _ratio(solution, len(rows), True, settled),
        'necessary_abstention': _ratio(abstain, categories['external_gap'], categories_complete, settled),
        'disposition_all': _ratio(solution + abstain, len(rows), True, settled),
        'false_positive': _ratio(totals['false_positive'], categories['healthy'], categories_complete, settled),
        'false_release': _ratio(totals['false_release'], categories['defect'], categories_complete, settled),
        'unnecessary_abstention': _ratio(totals['unnecessary_abstention'],
                                        categories['defect'] + categories['healthy'], categories_complete, settled),
        'arm_evidence_gap': _ratio(totals['arm_evidence_gap'], len(rows), True, settled),
        'partial': _ratio(totals['partial'], len(rows), True, settled),
        'contract_failed': _ratio(counts['contract_failed'], len(rows), True),
        'unknown_or_not_attempted': _ratio(counts['unknown'] + counts['not_attempted'], len(rows), True),
        'conditional_reviewed_disposition': _ratio(solution + abstain, reviewed, True, settled),
    }
    return {'planned_cells': len(rows), 'counts': counts, 'metrics': metrics,
            'actual': _sum_actual(rows)}


def build_study_report(study: dict, results: dict, *, reviews: dict | None = None,
                       adjudications: dict | None = None) -> dict:
    """Account for every frozen cell; semantic flags require locked, bound review.

    ``adjudications`` is a later custodian-supplied merge, not an execution input.
    Its task classifications are never inferred from model verdicts or filenames.
    """
    tasks, cells = _study(study, results)
    generation = get_generation(study.get('execution_profile'))
    qualifications, decisions = _review_inputs(tasks, cells, results,
        {} if reviews is None else reviews, {} if adjudications is None else adjudications)
    rows = []
    for cell_id, cell in cells.items():
        result = results.get(cell_id)
        actual = _actual(result)
        judgment = decisions.get(cell_id, {}).get('flags', {})
        qualification = qualifications.get(cell['task_id'], {})
        rows.append({'cell_id': cell_id, 'task_id': cell['task_id'], 'project_id': cell['project_id'],
            'strategy': cell['strategy'], 'state': _state(result, actual, bool(judgment)),
            'run_status': result.get('status') if result is not None else 'not_attempted',
            'error_code': result.get('error_code') if result is not None else None,
            'judgment': judgment, 'invalid': qualification.get('valid') is False,
            'actual': actual, 'first_answer_observed': result is not None and
                result.get('first_answer') is not None and cell['strategy'] in
                {'direct_initial_3', 'direct_full_3'}})
    by_pair = {(row['task_id'], row['strategy']): row for row in rows}
    pairs = {}
    for label, baseline in (('adaptive_vs_fixed', 'fixed_read_3'),
                            ('adaptive_vs_initial', 'direct_initial_3')):
        comparisons = []
        for task_id in tasks:
            left, right = by_pair[task_id, 'adaptive_read_3'], by_pair[task_id, baseline]
            category = qualifications.get(task_id, {}).get('category')
            outcome = 'pending'
            basis = None
            if left['invalid']:
                outcome = 'invalid'
            elif any(row['state'] in {'not_attempted', 'unknown', 'failed'} for row in (left, right)):
                outcome = 'not_comparable'
            elif category is not None and all(row['state'] in {'reviewed', 'contract_failed'}
                                               for row in (left, right)):
                criterion = 'reasonable_abstention' if category == 'external_gap' else 'full_solution'
                difference = int(left['judgment'].get(criterion, False)) - int(right['judgment'].get(criterion, False))
                outcome = 'win' if difference > 0 else 'loss' if difference < 0 else 'tie'
                basis = ('known_contract_failure_disposition' if any(row['state'] == 'contract_failed'
                    for row in (left, right)) else 'independently_reviewed')
            cost_left, cost_right = _usd(left['actual']['known_accrued_usd']), _usd(right['actual']['known_accrued_usd'])
            comparisons.append({'task_id': task_id, 'project_id': tasks[task_id]['project_id'],
                'category': category, 'stratum': qualifications.get(task_id, {}).get('stratum'),
                'outcome': outcome, 'comparison_eligible': basis is not None,
                'comparison_basis': basis, 'adaptive_state': left['state'], 'baseline_state': right['state'],
                'adaptive_error': left['error_code'], 'baseline_error': right['error_code'],
                'known_accrued_usd_delta': None if cost_left is None or cost_right is None else
                    str((Decimal(cost_left) - Decimal(cost_right)) / 1_000_000),
                'elapsed_seconds_delta': left['actual']['elapsed_seconds'] - right['actual']['elapsed_seconds']})
        pairs[label] = comparisons
    pair_counts = {label: {
        'planned_pairs': len(comparisons),
        'complete_pairs': sum(row['comparison_eligible'] for row in comparisons),
        'incomplete_pairs': sum(not row['comparison_eligible'] for row in comparisons),
        'complete_quality_pairs': sum(row['comparison_basis'] == 'independently_reviewed'
                                      for row in comparisons),
        'complete_operational_pairs': sum(row['comparison_basis'] == 'known_contract_failure_disposition'
                                          for row in comparisons)} for label, comparisons in pairs.items()}
    strategies = {strategy: _summarize([row for row in rows if row['strategy'] == strategy], qualifications)
                  for strategy in STRATEGIES}
    groups = {'project': {}, 'stratum': {}}
    for dimension, values in (('project', sorted({task['project_id'] for task in tasks.values()})),
                               ('stratum', ['full_context', 'retrieval_required'])):
        for value in values:
            selected = [row for row in rows if (row['project_id'] if dimension == 'project' else
                         qualifications.get(row['task_id'], {}).get('stratum')) == value]
            groups[dimension][value] = {strategy: _summarize([row for row in selected
                if row['strategy'] == strategy], qualifications) for strategy in STRATEGIES}
    return {'schema': 'macr.deepseek.study.report.v1', 'profile_sha256': study.get('profile_sha256'),
        'planned_cells': len(cells), 'task_observations': len(tasks),
        'project_count': len({task['project_id'] for task in tasks.values()}),
        'first_answer_observations': sum(row['first_answer_observed'] for row in rows),
        'first_answers_are_additional_samples': False,
        'invalid_tasks': [key for key, value in qualifications.items() if not value['valid']],
        'caps': {'max_requests_per_cell': 3, 'max_output_tokens_per_request': generation['max_output_per_request'],
                 'max_output_tokens_per_cell': generation['max_total_output_tokens'],
                 'planned_input_tokens_per_cell': 24000,
                 'input_tokens_are_planning_limits': True},
        'cells': rows, 'strategies': strategies, 'pairs': pairs, 'pair_counts': pair_counts, 'groups': groups,
        'interpretation': 'Descriptive paired observations from three project units; no significance or generalization claim.'}


def export_review_packets(study: dict, results: dict) -> dict:
    """Return packets plus a SEPARATE keeper mapping; send only packets to reviewers.

    Evidence/answer content can hint at strategy. This hides labels and accounting,
    and does not promise that an experienced reviewer cannot guess an arm.
    """
    tasks, cells = _study(study, results)
    packets, mapping = [], {}
    for cell_id, cell in cells.items():
        result = results.get(cell_id)
        if result is None or result.get('status') not in REVIEWABLE:
            continue
        task = tasks[cell['task_id']]
        digest = result_digest(result)
        review_id = result_digest({'study': study.get('profile_sha256'), 'cell': cell_id,
                                   'result': digest})[:32]
        evidence = result.get('review_evidence', {})
        source = result.get('source', result.get('visible_source', evidence.get('source', task.get('source', []))))
        _require(type(source) is list, 'review_source_invalid')
        safe_source = []
        for snippet in source:
            _require(type(snippet) is dict and {'file', 'start', 'end', 'body'} <= set(snippet),
                     'review_source_invalid')
            safe_source.append({key: snippet[key] for key in ('file', 'start', 'end', 'body')})
        packets.append({'review_id': review_id, 'result_sha256': digest,
            'question': task.get('question'), 'scenario': task.get('scenario'),
            'assumptions': task.get('assumptions', []), 'source': safe_source,
            'answer': result.get('answer'), 'first_answer': result.get('first_answer'),
            'gap': result.get('gap')})
        mapping[review_id] = cell_id
    return {'packets': packets, 'keeper_mapping': mapping,
        'blinding_limit': 'Evidence may reveal the strategy; only labels, model and accounting are hidden.'}
