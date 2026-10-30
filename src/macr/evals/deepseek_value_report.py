"""Pure selected-arm accounting and one finite engineering selection rule.

No files, gold or transport are accessed here. Locked custody inputs are supplied
only after execution closes; labels alone never supply mechanism correctness.
"""
from __future__ import annotations

from copy import deepcopy
from statistics import median

from macr.evals.deepseek_comparison_report import _identity
from macr.evals.deepseek_policy_spend import _usage_evidence
from macr.evals.deepseek_study_generation import VALUE_PROFILE
from macr.evals.deepseek_study_report import (
    REVIEWABLE, _actual, _ratio, _require, _review_inputs, _state, _study,
    _summarize, _text, _usd, result_digest)

DIRECT = 'direct_initial_3'
TOOL = 'adaptive_read_3'
STRATEGIES = (DIRECT, TOOL)


def _selected(study, results):
    tasks, basis = _study(study, results)
    _require(study.get('execution_profile') == VALUE_PROFILE, 'value_profile_invalid')
    cells = {key: row for key, row in basis.items() if row['strategy'] in STRATEGIES}
    _require(len(tasks) == 9 and len(cells) == 18, 'value_grid_invalid')
    _require(set(results) <= set(cells), 'value_unselected_result')
    return tasks, cells


def _metered(result, actual):
    if result is None:
        return False
    requests = result['requests']
    account = result.get('accounting', {})
    if (not 1 <= len(requests) <= 3 or account.get('state') != 'open'
            or type(account.get('request_count')) is not int
            or account['request_count'] != len(requests)
            or _usd(actual['unknown_reserved_usd']) != 0):
        return False
    costs = []
    for request in requests:
        response = request.get('response')
        if type(response) is not dict:
            return False
        raw = response.get('raw_usage')
        if raw is None and type(response.get('billing_evidence')) is dict:
            raw = response['billing_evidence'].get('raw_usage')
        evidence = _usage_evidence(raw, 8192)
        if evidence is None or evidence[1]['prompt_tokens'] > 8000:
            return False
        costs.append(evidence[0])
    return (sum(costs) == _usd(actual['known_accrued_usd'])
            and sum(costs) == _usd(account.get('known_accrued_usd'))
            and _usd(account.get('unknown_held_usd')) == 0
            and (result.get('status') not in REVIEWABLE
                 or result['strategy'] != DIRECT or len(requests) == 3))


def _determinate_inputs(cells, results, reviews, qualifications, decisions, records):
    _require(type(records) is dict and set(records) <= set(cells), 'value_determinate_unknown')
    for cell_id, value in records.items():
        result, review = results.get(cell_id), reviews.get(cell_id)
        _require(result is not None and result.get('status') in REVIEWABLE
            and review is not None and cell_id in decisions,
            'value_determinate_not_reviewed')
        _require(type(value) is dict and set(value) == {'result_sha256', 'review_sha256',
            'status', 'determinate_error', 'rationale'} and value['status'] == 'locked'
            and type(value['determinate_error']) is bool and _text(value['rationale']),
            'value_determinate_invalid')
        _require(value['result_sha256'] == result_digest(result)
            and value['review_sha256'] == result_digest(review), 'value_determinate_binding')
        verdict = result.get('answer', {}).get('verdict') if type(result.get('answer')) is dict else None
        abstained = result['status'] == 'ask_user_terminated' or verdict == 'unknown'
        flags = decisions[cell_id]['flags']
        category = qualifications[cells[cell_id]['task_id']]['category']
        required_error = (flags['false_positive'] or flags['false_release']
            or (category == 'external_gap' and not abstained))
        _require(not (abstained and value['determinate_error'])
            and not (required_error and not value['determinate_error'])
            and not (flags['full_solution'] and value['determinate_error']),
            'value_determinate_answer_conflict')
    return records


def build_value_report(study, results, *, reviews=None, adjudications=None,
                       determinate_reviews=None):
    """Report only the 18 selected cells; missing observations cannot select tools.

    Independent judgments include exact-result/review-bound determinate-error
    bits covering mechanism, trigger and evidence correctness. This later import
    is an observer operation, never an input to generation or tool access.
    """
    tasks, cells = _selected(study, results)
    reviews = {} if reviews is None else reviews
    qualifications, decisions = _review_inputs(tasks, cells, results, reviews,
        {} if adjudications is None else adjudications)
    _require(all(not (row['flags']['reasonable_abstention'] and row['flags']['partial'])
        for row in decisions.values()), 'value_partial_abstention_conflict')
    determinate = _determinate_inputs(cells, results, reviews, qualifications, decisions,
        {} if determinate_reviews is None else determinate_reviews)
    categories_complete = len(qualifications) == 9
    if categories_complete:
        categories = [row['category'] for row in qualifications.values()]
        _require(all(categories.count(name) == 3 for name in ('defect', 'healthy', 'external_gap'))
            and all(row['stratum'] == 'full_context' for row in qualifications.values()),
            'value_qualification_grid_invalid')
    rows, all_fingerprints, cell_fingerprints = [], set(), {}
    for cell_id, cell in cells.items():
        result = results.get(cell_id)
        actual = _actual(result)
        fingerprints, identity_ok = _identity(result) if result is not None else ([], False)
        all_fingerprints.update(fingerprints)
        cell_fingerprints[cell_id] = set(fingerprints)
        judgment = decisions.get(cell_id, {}).get('flags', {})
        qualification = qualifications.get(cell['task_id'], {})
        error_bit = determinate.get(cell_id, {}).get('determinate_error')
        rows.append({'cell_id': cell_id, 'task_id': cell['task_id'],
            'project_id': cell['project_id'], 'strategy': cell['strategy'],
            'state': _state(result, actual, bool(judgment)),
            'run_status': result.get('status') if result is not None else 'not_attempted',
            'error_code': result.get('error_code') if result is not None else None,
            'judgment': judgment, 'invalid': qualification.get('valid') is False,
            'determinate_error': error_bit, 'identity_verified': identity_ok,
            'operational_verified': identity_ok and _metered(result, actual),
            'initial_evidence_sha256': result.get('initial_evidence_sha256') if result is not None else None,
            'actual': actual, 'first_answer_observed': cell['strategy'] == DIRECT
                and result is not None and result.get('first_answer') is not None})
    by_pair = {(row['task_id'], row['strategy']): row for row in rows}
    pairs = []
    for task_id in tasks:
        left, right = by_pair[task_id, TOOL], by_pair[task_id, DIRECT]
        category = qualifications.get(task_id, {}).get('category')
        initial = [row['initial_evidence_sha256'] for row in (left, right)]
        same_input = all(type(value) is str and len(value) == 64
            and not set(value) - set('0123456789abcdef') for value in initial) and initial[0] == initial[1]
        outcome, basis = 'pending', None
        if left['invalid']:
            outcome = 'invalid'
        elif not same_input or any(row['state'] in {'not_attempted', 'unknown', 'failed'}
                 or not row['operational_verified'] for row in (left, right)) or len(
                cell_fingerprints[left['cell_id']] | cell_fingerprints[right['cell_id']]) != 1:
            outcome = 'not_comparable'
        elif category is not None and all(row['state'] in {'reviewed', 'contract_failed'}
                                         for row in (left, right)):
            criterion = 'reasonable_abstention' if category == 'external_gap' else 'full_solution'
            delta = int(left['judgment'].get(criterion, False)) - int(right['judgment'].get(criterion, False))
            outcome = 'win' if delta > 0 else 'loss' if delta < 0 else 'tie'
            basis = ('known_contract_failure_disposition' if any(row['state'] == 'contract_failed'
                for row in (left, right)) else 'independently_reviewed')
        pairs.append({'task_id': task_id, 'outcome': outcome,
            'comparison_eligible': basis is not None, 'comparison_basis': basis,
            'tool_state': left['state'], 'direct_state': right['state'],
            'same_initial_evidence': same_input})
    groups = {}
    for strategy in STRATEGIES:
        selected = [row for row in rows if row['strategy'] == strategy]
        group = _summarize(selected, qualifications)
        times = [row['actual']['elapsed_seconds'] for row in selected
                 if row['run_status'] != 'not_attempted']
        group['latency_seconds'] = {'count': len(times), 'total': sum(times),
            'median': median(times) if times else None}
        group['metrics']['unsupported_determinate'] = _ratio(sum(
            row['determinate_error'] is True for row in selected), 9, categories_complete,
            all(row['state'] == 'contract_failed' or row['determinate_error'] is not None
                for row in selected))
        groups[strategy] = group
    counts = {key: sum(row['outcome'] == key for row in pairs)
              for key in ('win', 'loss', 'tie', 'pending', 'not_comparable', 'invalid')}
    net = counts['win'] - counts['loss']
    costs = {key: _usd(value['actual']['known_accrued_usd']) for key, value in groups.items()}
    complete = {'all_eighteen_attempted': len(results) == 18,
        'six_solvable_three_gap': categories_complete and not any(row['invalid'] for row in rows),
        'all_semantic_reviews_locked': all(row['state'] in {'reviewed', 'contract_failed'} for row in rows),
        'all_determinate_reviews_locked': all(row['state'] == 'contract_failed'
            or row['determinate_error'] is not None for row in rows),
        'same_initial_evidence': all(pair['same_initial_evidence'] for pair in pairs),
        'metering_and_identity_complete': len(all_fingerprints) == 1
            and all(row['operational_verified'] for row in rows)}
    checks = {'net_beneficial_pairs_at_least_two': net >= 2,
        'no_added_unsupported': (not any(by_pair[task, TOOL]['determinate_error'] is True
            and by_pair[task, DIRECT]['determinate_error'] is not True for task in tasks))
            if complete['all_determinate_reviews_locked'] else None,
        'cost_within_125_percent': (all(value is not None for value in costs.values())
            and 4 * costs[TOOL] <= 5 * costs[DIRECT])
            if complete['all_eighteen_attempted'] and complete['metering_and_identity_complete'] else None,
        'time_within_125_percent': (4 * groups[TOOL]['actual']['elapsed_seconds']
            <= 5 * groups[DIRECT]['actual']['elapsed_seconds'])
            if complete['all_eighteen_attempted'] else None}
    decision = ('inconclusive_default_direct' if not all(complete.values()) else
        'retain_tool_experimental' if all(checks.values()) else 'prefer_direct')
    return {'schema': 'macr.deepseek.value.report.v1',
        'profile_sha256': study.get('profile_sha256'), 'planned_cells': 18,
        'task_observations': 9, 'cells': rows, 'strategies': groups,
        'pairs': pairs, 'pair_counts': counts,
        'first_answer_observations': sum(row['first_answer_observed'] for row in rows),
        'first_answers_are_additional_samples': False,
        'selection': {'decision': decision, 'default': 'direct_api',
            'readiness': complete, 'checks': checks, 'net_beneficial_pairs': net,
            'interpretation': 'Finite nine-task engineering selection; no significance or generalization claim.'}}


def export_value_packets(study, results):
    """Return actual-evidence packets and a separate custodian-only mapping."""
    tasks, cells = _selected(study, results)
    packets, mapping = [], {}
    for cell_id, cell in cells.items():
        result = results.get(cell_id)
        if result is None or result.get('status') not in REVIEWABLE:
            continue
        task = tasks[cell['task_id']]
        receipt = result_digest(result)
        review_id = result_digest({'study': study.get('profile_sha256'),
            'cell': cell_id, 'result': receipt})[:32]
        evidence = result.get('review_evidence', {})
        _require(type(evidence) is dict, 'review_source_invalid')
        source = result.get('source', result.get('visible_source', evidence.get('source', [])))
        _require(type(source) is list, 'review_source_invalid')
        safe = []
        for snippet in source:
            _require(type(snippet) is dict and {'file', 'start', 'end', 'body'} <= set(snippet),
                'review_source_invalid')
            safe.append({key: snippet[key] for key in ('file', 'start', 'end', 'body')})
        packets.append(deepcopy({'review_id': review_id, 'result_sha256': receipt,
            'question': task.get('question'), 'scenario': task.get('scenario'),
            'assumptions': task.get('assumptions', []), 'source': safe,
            'answer': result.get('answer'), 'first_answer': result.get('first_answer'),
            'gap': result.get('gap')}))
        mapping[review_id] = cell_id
    return {'packets': packets, 'keeper_mapping': mapping,
        'blinding_limit': 'Content may reveal strategy; only labels, model and accounting are hidden.'}
