"""Pure, proposal-only contract for a paired repair comparison.

Both strategies receive the full original source, issue and visible tests and
may spend the same cumulative request/token/USD allowance on revision. Debugger
additionally supplies computed evidence and available validation feedback. That
contrast measures the whole strategy, not orchestration alone. A same-evidence
auxiliary needs its own frozen plan; it cannot be reconstructed after success.
``verification_origin`` describes final independent outcome verification, common
to both arms. In-generation checks belong to ``feedback_validation_attempts``.

This module reads no files and dispatches nothing. Hashes bind local records;
they do not authenticate a provider, isolation, hidden-test exclusion or review.
The development example uses placeholder commitments, not a runnable experiment.
"""
from __future__ import annotations

from copy import deepcopy
import random
import re

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

SCHEMA = 'macr.repair-comparison.spec.v1'
ARMS = ('direct_api', 'api_debugger')
STATUSES = ('not_attempted', 'skipped', 'answer_recorded', 'contract_failed',
            'transport_failed', 'verification_unavailable', 'verification_timeout',
            'budget_exceeded', 'unknown')
OUTCOMES = ('full_repair', 'healthy_correct', 'necessary_abstention',
            'unnecessary_abstention', 'false_positive', 'false_release', 'partial')
SUCCESS = frozenset({'full_repair', 'healthy_correct', 'necessary_abstention'})
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_SPEC_FIELDS = {'schema', 'comparison_id', 'purpose', 'tasks', 'model', 'limits',
    'scoring', 'repeats', 'order_seed', 'initial_evidence', 'contrast',
    'same_evidence_auxiliary', 'stop_policy'}
_ROW_FIELDS = {'status', 'candidate_origin', 'verification_origin', 'verdict',
    'initial_context_sha256', 'model_identity_sha256', 'requests', 'input_tokens',
    'output_tokens', 'known_usd', 'unknown_reserved_usd', 'timing_ms', 'review',
    'model_configuration_sha256', 'output_contract_sha256', 'feedback_validation_attempts',
    'final_output_sha256', 'candidate_sha256', 'verification_receipt_sha256'}


def result_digest(value):
    """Canonical receipt identity; independent of historical study runners."""
    try:
        return sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError('report_json_invalid') from None


def _usd(value):
    if value is None:
        return None
    _require(type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]*)\.[0-9]{6}', value), 'report_money_invalid')
    try:
        return int(Decimal(value) * 1_000_000)
    except (ValueError, InvalidOperation):
        raise ValueError('report_money_invalid') from None


def _money(value):
    return None if value is None else f'{value // 1_000_000}.{value % 1_000_000:06d}'


def _require(value, code):
    if not value:
        raise ValueError(code)


def _object(value, fields, code):
    _require(type(value) is dict and set(value) == fields, code)


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _integer(value, lower=0, upper=10**12):
    return type(value) is int and lower <= value <= upper


def _amount(value):
    _require(type(value) is str and len(value) <= 20, 'comparison_money_invalid')
    return _usd(value)


def compile_comparison(spec: dict) -> dict:
    """Freeze equal opportunities and a near-balanced paired order; never enable execution."""
    _object(spec, _SPEC_FIELDS, 'comparison_spec_fields')
    _require(spec['schema'] == SCHEMA and type(spec['comparison_id']) is str
        and _ID.fullmatch(spec['comparison_id']) and spec['purpose'] in
        {'development_contract_only', 'preregistered_proposal'}, 'comparison_spec_invalid')
    _require(spec['initial_evidence'] == 'identical_full_source_issue_visible_tests'
        and spec['contrast'] == 'whole_strategy_with_extra_computed_and_validation_information'
        and spec['stop_policy'] == 'unknown_identity_or_cost_halts_no_retry',
        'comparison_evidence_or_stop_policy')
    tasks = spec['tasks']
    _require(type(tasks) is list and 1 <= len(tasks) <= 32, 'comparison_tasks_invalid')
    seen = set()
    for task in tasks:
        _object(task, {'task_id', 'snapshot_sha256', 'issue_sha256',
            'visible_tests_sha256', 'initial_context_sha256'}, 'comparison_task_fields')
        _require(type(task['task_id']) is str and _ID.fullmatch(task['task_id'])
            and task['task_id'] not in seen and all(_hash(v) for k, v in task.items()
            if k != 'task_id'), 'comparison_task_invalid')
        seen.add(task['task_id'])
    _object(spec['model'], {'configuration_sha256', 'output_contract_sha256',
        'identity_policy'}, 'comparison_model_fields')
    _require(spec['model']['identity_policy'] == 'strict_run_fingerprint' and
        all(_hash(spec['model'][k]) for k in ('configuration_sha256', 'output_contract_sha256')),
        'comparison_model_invalid')
    limits = spec['limits']
    _object(limits, {'max_requests', 'input_tokens', 'output_tokens', 'max_usd'},
            'comparison_limit_fields')
    _require(_integer(limits['max_requests'], 1, 8) and
        _integer(limits['input_tokens'], 1, 8_000_000) and
        _integer(limits['output_tokens'], 1, 1_000_000) and
        0 < _amount(limits['max_usd']) <= 100_000_000, 'comparison_limits_invalid')
    scoring = spec['scoring']
    _object(scoring, {'rubric_sha256', 'hidden_tests_sha256',
        'custodian_commitment_sha256', 'generation_access'}, 'comparison_scoring_fields')
    _require(scoring['generation_access'] == 'excluded' and
        all(_hash(scoring[k]) for k in scoring if k != 'generation_access'),
        'comparison_scoring_invalid')
    auxiliary = spec['same_evidence_auxiliary']
    if auxiliary is not None:
        _object(auxiliary, {'plan_sha256', 'shared_evidence_sha256'}, 'comparison_auxiliary_fields')
        _require(all(_hash(v) for v in auxiliary.values()), 'comparison_auxiliary_invalid')
    _require(_integer(spec['repeats'], 1, 8) and _integer(spec['order_seed'], 0, 2**32 - 1),
             'comparison_order_invalid')
    pairs = [(task['task_id'], repeat) for repeat in range(spec['repeats']) for task in tasks]
    random.Random(spec['order_seed']).shuffle(pairs)
    cells = []
    for index, (task_id, repeat) in enumerate(pairs):
        order = ARMS if index % 2 == 0 else ARMS[::-1]
        for arm in order:
            cell = {'task_id': task_id, 'repeat': repeat, 'arm': arm}
            cell['cell_id'] = result_digest({'comparison_id': spec['comparison_id'], **cell})[:32]
            cells.append(cell)
    plan = {'schema': 'macr.repair-comparison.plan.v1', 'spec': deepcopy(spec),
        'cells': cells, 'planned_cells': len(cells),
        'max_requests': len(cells) * limits['max_requests'],
        'max_usd': _money(len(cells) * _amount(limits['max_usd'])),
        'remote_enabled': False, 'execution_authorized': False,
        'arms': {'direct_api': 'full_source_iterative_self_review',
                 'api_debugger': 'same_full_source_plus_computed_and_validation_information'}}
    plan['comparison_sha256'] = result_digest(plan)
    return plan


def validate_comparison(plan: dict) -> dict:
    """Reject edited order, unequal limits or any attempt to turn this into authority."""
    _require(type(plan) is dict and type(plan.get('spec')) is dict, 'comparison_plan_invalid')
    expected = compile_comparison(plan['spec'])
    _require(plan == expected, 'comparison_plan_binding')
    return expected


def generation_view(plan: dict, task_id: str) -> dict:
    """Allowlisted generation policy; never pass the full scoring plan to a model.

    Source/issue bytes remain the normal entry's strict neutral-task manifest.
    This capsule contains their frozen commitments, model policy and limits only.
    It does not enforce OS isolation or prove the source excludes hidden tests.
    """
    plan = validate_comparison(plan)
    task = next((t for t in plan['spec']['tasks'] if t['task_id'] == task_id), None)
    _require(task is not None, 'comparison_generation_task_missing')
    return {'schema': 'macr.repair-comparison.generation.v1', 'task': deepcopy(task),
        'model': deepcopy(plan['spec']['model']), 'limits': deepcopy(plan['spec']['limits'])}


def _empty(task, spec):
    return {'status': 'not_attempted', 'candidate_origin': 'none', 'verification_origin': 'none',
        'verdict': None, 'initial_context_sha256': task['initial_context_sha256'],
        'model_identity_sha256': None, 'requests': 0, 'input_tokens': 0, 'output_tokens': 0,
        'model_configuration_sha256': spec['model']['configuration_sha256'],
        'output_contract_sha256': spec['model']['output_contract_sha256'],
        'feedback_validation_attempts': 0,
        'final_output_sha256': None, 'candidate_sha256': None,
        'verification_receipt_sha256': None,
        'known_usd': '0.000000', 'unknown_reserved_usd': '0.000000',
        'timing_ms': dict.fromkeys(('preparation', 'provider', 'verification', 'total'), 0),
        'review': None}


def _row(row, task, spec):
    _object(row, _ROW_FIELDS, 'comparison_result_fields')
    _require(row['status'] in STATUSES and row['candidate_origin'] in
        {'none', 'model', 'human', 'simulation', 'provider_unattested'} and row['verification_origin'] in
        {'none', 'simulation', 'unavailable', 'isolated_execution'} and
        row['verdict'] in {None, 'defect', 'healthy', 'unknown'}, 'comparison_result_invalid')
    _require(row['initial_context_sha256'] == task['initial_context_sha256'],
             'comparison_initial_input_mismatch')
    _require(row['model_configuration_sha256'] == spec['model']['configuration_sha256']
        and row['output_contract_sha256'] == spec['model']['output_contract_sha256'],
        'comparison_model_or_contract_mismatch')
    _require(row['model_identity_sha256'] is None or _hash(row['model_identity_sha256']),
             'comparison_identity_invalid')
    _require(all(row[key] is None or _hash(row[key]) for key in
        ('final_output_sha256', 'candidate_sha256', 'verification_receipt_sha256')),
        'comparison_artifact_digest_invalid')
    limits = spec['limits']
    budget_failure = row['status'] == 'budget_exceeded'
    for key in ('requests', 'input_tokens', 'output_tokens'):
        limit = limits['max_requests' if key == 'requests' else key]
        _require(_integer(row[key]) and (budget_failure or row[key] <= limit),
                 'comparison_actual_budget_exceeded')
    known, reserved = _amount(row['known_usd']), _amount(row['unknown_reserved_usd'])
    _require(budget_failure or known + reserved <= _amount(limits['max_usd']),
             'comparison_actual_budget_exceeded')
    # A settled overrun is a failed observation, never permission to dispatch
    # beyond the plan or an answer eligible for success credit. Keep raw actuals.
    if budget_failure:
        _require(row['verdict'] is row['final_output_sha256'] is row['candidate_sha256'] is None,
                 'comparison_budget_failure_has_answer')
    _require((reserved > 0) == (row['status'] == 'unknown'), 'comparison_unknown_reserve_invalid')
    _require(_integer(row['feedback_validation_attempts'], 0, 32),
             'comparison_feedback_count_invalid')
    timing = row['timing_ms']
    _object(timing, {'preparation', 'provider', 'verification', 'total'}, 'comparison_timing_fields')
    _require(all(_integer(v) for v in timing.values()) and
        sum(timing[k] for k in ('preparation', 'provider', 'verification')) <= timing['total'],
        'comparison_timing_invalid')
    pre_dispatch_blocked = (row['status'] == 'verification_unavailable' and row['requests'] == 0)
    pre_dispatch_timeout = (row['status'] == 'verification_timeout' and row['requests'] == 0)
    if row['status'] in {'not_attempted', 'skipped'} or pre_dispatch_blocked or pre_dispatch_timeout:
        expected_origins = ({'simulation','unavailable'} if pre_dispatch_timeout
                            else {'unavailable'} if pre_dispatch_blocked else {'none'})
        _require(row['requests'] == row['input_tokens'] == row['output_tokens'] == known == reserved == 0
            and row['candidate_origin'] == 'none'
            and row['verification_origin'] in expected_origins
            and row['verdict'] is row['review'] is row['model_identity_sha256'] is None
            and row['final_output_sha256'] is row['candidate_sha256'] is None
            and (pre_dispatch_timeout or row['verification_receipt_sha256'] is None)
            and row['feedback_validation_attempts'] <= (1 if pre_dispatch_timeout else 0),
            'comparison_unrun_has_result')
    else:
        _require(row['requests'] > 0, 'comparison_attempt_missing')
    if row['status'] != 'answer_recorded':
        _require(row['review'] is None, 'comparison_review_nonanswer')
    review = row['review']
    if review is not None:
        _object(review, {'status', 'source', 'rubric_sha256', 'hidden_tests_sha256',
            'result_sha256', 'outcome'}, 'comparison_review_fields')
        _require(row['candidate_origin'] == 'model' and
            row['verification_origin'] == 'isolated_execution' and
            _hash(row['model_identity_sha256']) and _hash(row['final_output_sha256'])
            and _hash(row['verification_receipt_sha256']), 'comparison_review_provenance')
        _require(review['status'] == 'locked' and review['source'] == 'independent_custodian'
            and review['rubric_sha256'] == spec['scoring']['rubric_sha256']
            and review['hidden_tests_sha256'] == spec['scoring']['hidden_tests_sha256']
            and review['result_sha256'] == result_digest(dict(row, review=None))
            and review['outcome'] in OUTCOMES, 'comparison_review_binding')
        allowed = {'full_repair': {'defect'}, 'healthy_correct': {'healthy'},
            'necessary_abstention': {'unknown'}, 'unnecessary_abstention': {'unknown'},
            'false_positive': {'defect'}, 'false_release': {'healthy'},
            'partial': {'defect', 'healthy', 'unknown'}}
        _require(row['verdict'] in allowed[review['outcome']], 'comparison_review_verdict_conflict')
        _require(review['outcome'] != 'full_repair' or _hash(row['candidate_sha256']),
                 'comparison_repair_candidate_missing')
    return known, reserved


def score_packet(plan: dict, cell_id: str, row: dict, *, original_files: dict,
                 editable_paths: list[str], protected_paths: list[str], edits=None,
                 final_output=None, verification_receipt=None, proposal_chain=None) -> dict:
    """Prepare a source-bound, unscored handoff; never execute tests or import gold.

    ``candidate_sha256`` means the applied source inventory's ``source_id``.
    Output/verification digests use ``result_digest`` over the supplied JSON value.
    An answered output must be the parsed repair proposal; no provider-envelope
    guessing is performed. Single proposals apply against the frozen original.
    Multi-round callers pass the entire previously verified parsed proposal chain,
    not just the last incremental edit. Local chain consistency still cannot
    authenticate who produced those outputs or whether verification ran.
    The recipient supplies a locked independent review later through build_report;
    this function cannot attest provenance or turn a claimed pass into success.
    """
    from macr.investigation.repair_io import bounded_json
    from macr.investigation.repair_edits import apply_edits, validate_files
    from macr.investigation.repair_loop import source_id, output_contract, digest

    plan = validate_comparison(plan)
    cell = next((c for c in plan['cells'] if c['cell_id'] == cell_id), None)
    _require(cell is not None, 'comparison_score_cell_missing')
    task = next(t for t in plan['spec']['tasks'] if t['task_id'] == cell['task_id'])
    finalize_allowed = (plan['spec']['model']['output_contract_sha256'] ==
                        digest(output_contract('v2')))
    _require(type(row) is dict and row.get('review') is None, 'comparison_score_review_must_be_separate')
    _row(row, task, plan['spec'])
    validate_files(original_files)
    _require(type(protected_paths) is list and 1 <= len(protected_paths) <= 8
        and all(type(p) is str and p in original_files for p in protected_paths)
        and len(set(protected_paths)) == len(protected_paths), 'comparison_score_protected_paths')
    _require(type(editable_paths) is list and 1 <= len(editable_paths) <= len(original_files)
        and all(type(p) is str and p in original_files for p in editable_paths)
        and len(set(editable_paths)) == len(editable_paths)
        and not set(editable_paths) & set(protected_paths), 'comparison_score_edit_authority')
    _require(source_id(original_files) == task['snapshot_sha256']
        and source_id({p:original_files[p] for p in protected_paths}) == task['visible_tests_sha256'],
        'comparison_score_source_binding')
    changed = None; last_applied = original_files; has_candidate = False
    if proposal_chain is not None:
        bounded_json(proposal_chain, max_bytes=65536, max_depth=16, utf8_bytes=True)
        _require(edits is None and type(proposal_chain) is list
            and 1 <= len(proposal_chain) <= min(3, row['requests']),
                 'comparison_score_chain_contract')
        for index, proposal in enumerate(proposal_chain):
            _require(type(proposal) is dict and proposal.get('base_content_id') == source_id(last_applied)
                and proposal.get('status') in ('candidate', 'no_candidate', 'insufficient_evidence',
                                               'finalize_current')
                and type(proposal.get('edits')) is list, 'comparison_score_chain_binding')
            if proposal['status'] == 'candidate':
                last_applied = apply_edits(last_applied, proposal['edits'], editable_paths=editable_paths)
                _require(all(last_applied[p] == original_files[p] for p in protected_paths),
                         'comparison_score_chain_protected')
                has_candidate = True
            elif proposal['status'] == 'finalize_current':
                _require(finalize_allowed and has_candidate and index == len(proposal_chain)-1
                    and proposal['edits'] == [], 'comparison_score_finalize_contract')
            else:
                _require(index == len(proposal_chain)-1 and proposal['edits'] == [],
                         'comparison_score_chain_after_abstention')
        _require(final_output == proposal_chain[-1], 'comparison_score_chain_final_output')
        if proposal_chain[-1]['status'] in ('candidate', 'finalize_current'):
            changed = last_applied
        expected_candidate = source_id(changed) if changed is not None else None
        _require(row['candidate_sha256'] == expected_candidate, 'comparison_score_candidate_binding')
    elif row['candidate_sha256'] is None:
        _require(edits is None or edits == [], 'comparison_score_candidate_unexpected')
    else:
        changed = apply_edits(original_files, edits, editable_paths=editable_paths)
        last_applied = changed
        _require(source_id(changed) == row['candidate_sha256']
            and all(changed[p] == original_files[p] for p in protected_paths),
            'comparison_score_candidate_binding')
    for value, key in ((final_output, 'final_output_sha256'),
                       (verification_receipt, 'verification_receipt_sha256')):
        bounded_json(value, max_bytes=65536, max_depth=16, utf8_bytes=key=='final_output_sha256')
        _require((value is None and row[key] is None)
            or (value is not None and result_digest(value) == row[key]),
            'comparison_score_artifact_binding')
    if row['status'] == 'answer_recorded':
        _require(type(final_output) is dict and final_output.get('status') in
            ('candidate', 'no_candidate', 'insufficient_evidence', 'finalize_current')
            and type(final_output.get('edits')) is list, 'comparison_score_proposal_contract')
        finalized = final_output['status'] == 'finalize_current'
        _require(not finalized or (finalize_allowed and proposal_chain is not None),
                 'comparison_score_finalize_chain_required')
        if changed is not None:
            expected_edits = proposal_chain[-1]['edits'] if proposal_chain is not None else edits
            _require((final_output['status'] == 'candidate' or finalized)
                and final_output['edits'] == expected_edits,
                     'comparison_score_proposal_candidate_mismatch')
        else:
            _require(final_output['status'] != 'candidate' and final_output['edits'] == [],
                     'comparison_score_proposal_candidate_missing')
    packet = {'schema': 'macr.repair-comparison.score-packet.v1',
        'comparison_sha256': plan['comparison_sha256'], 'cell': deepcopy(cell),
        'result': deepcopy(row), 'original_source_id': source_id(original_files),
        'protected_paths': sorted(protected_paths),
        'candidate_files': {p:b.decode('utf-8') for p,b in sorted(changed.items())} if changed else None,
        'proposal_chain': deepcopy(proposal_chain),
        'proposal_chain_sha256': result_digest(proposal_chain) if proposal_chain is not None else None,
        'last_applied_source_id': source_id(last_applied),
        'final_output': deepcopy(final_output), 'verification_receipt': deepcopy(verification_receipt),
        'scoring_commitments': deepcopy(plan['spec']['scoring']),
        'semantic_truth_validated': False, 'provenance_authenticated': False}
    packet['packet_sha256'] = result_digest(packet)
    return packet


def build_report(plan: dict, results: dict) -> dict:
    """Retain every planned cell. Binding validation is not independent attestation."""
    plan = validate_comparison(plan)
    _require(type(results) is dict and set(results) <= {c['cell_id'] for c in plan['cells']},
             'comparison_result_cell_unknown')
    tasks = {t['task_id']: t for t in plan['spec']['tasks']}
    arms = {arm: {'planned': len(plan['cells']) // 2, 'scored': 0, 'unscored': 0,
        'identity_invalid': 0, 'excluded_locked_reviews': 0,
        'statuses': dict.fromkeys(STATUSES, 0), 'outcomes': dict.fromkeys(OUTCOMES, 0),
        'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'known_usd': 0,
        'unknown_reserved_usd': 0, 'feedback_validation_attempts': 0,
        'timing_ms': dict.fromkeys(('preparation', 'provider', 'verification', 'total'), 0)}
        for arm in ARMS}
    rows, first_identity, must_stop = {}, None, False
    for cell in plan['cells']:
        row = deepcopy(results.get(cell['cell_id'], _empty(tasks[cell['task_id']], plan['spec'])))
        known, reserved = _row(row, tasks[cell['task_id']], plan['spec'])
        # A baseline check that timed out before any model dispatch supplied no
        # feedback to the direct strategy; preserve its single failed attempt.
        _require(cell['arm'] != 'direct_api' or row['feedback_validation_attempts'] == 0
            or (row['status'] == 'verification_timeout' and row['requests'] == 0),
                 'comparison_baseline_received_extra_feedback')
        _require(not must_stop or row['requests'] == 0, 'comparison_dispatch_after_stop')
        identity_valid = False
        if row['requests']:
            identity = row['model_identity_sha256']
            if identity is None or (first_identity is not None and identity != first_identity):
                must_stop = True
            else:
                identity_valid = True
                if first_identity is None:
                    first_identity = identity
        if row['status'] in {'unknown', 'budget_exceeded'}:
            must_stop = True
        stats = arms[cell['arm']]
        stats['identity_invalid'] += int(row['requests'] > 0 and not identity_valid)
        stats['statuses'][row['status']] += 1
        for field in ('requests', 'input_tokens', 'output_tokens', 'feedback_validation_attempts'):
            stats[field] += row[field]
        stats['known_usd'] += known; stats['unknown_reserved_usd'] += reserved
        for field in stats['timing_ms']:
            stats['timing_ms'][field] += row['timing_ms'][field]
        if row['review'] is not None and identity_valid:
            stats['scored'] += 1
            stats['outcomes'][row['review']['outcome']] += 1
        else:
            stats['unscored'] += 1
            stats['excluded_locked_reviews'] += int(row['review'] is not None)
        rows[(cell['task_id'], cell['repeat'], cell['arm'])] = row, identity_valid
    pairs = dict.fromkeys(('direct_api_win', 'api_debugger_win', 'tie', 'not_comparable'), 0)
    for task in tasks:
        for repeat in range(plan['spec']['repeats']):
            (left, left_valid), (right, right_valid) = (rows[(task, repeat, arm)] for arm in ARMS)
            if (not left_valid or not right_valid or left['review'] is None or right['review'] is None or
                    left['model_identity_sha256'] != right['model_identity_sha256']):
                pairs['not_comparable'] += 1
                continue
            scores = [r['review']['outcome'] in SUCCESS for r in (left, right)]
            key = 'tie' if scores[0] == scores[1] else ARMS[scores.index(True)] + '_win'
            pairs[key] += 1
    known_total = sum(a['known_usd'] for a in arms.values())
    reserved_total = sum(a['unknown_reserved_usd'] for a in arms.values())
    for stats in arms.values():
        for field in ('known_usd', 'unknown_reserved_usd'):
            stats[field] = _money(stats[field])
    return {'schema': 'macr.repair-comparison.report.v1',
        'comparison_sha256': plan['comparison_sha256'], 'planned_cells': plan['planned_cells'],
        'independent_tasks': len(tasks), 'planned_pairs': len(plan['cells']) // 2,
        'arms': arms, 'pairs': pairs, 'actual_known_usd': _money(known_total),
        'unknown_reserved_usd': _money(reserved_total), 'must_stop': must_stop,
        'automatic_retry_allowed': False, 'remote_enabled': False,
        'execution_authorized': False, 'semantic_truth_validated': False,
        'provenance_authenticated': False, 'hidden_test_exclusion_authenticated': False,
        'equal_ceilings_prove_equal_consumption': False,
        'orchestration_only_gain_established': False,
        'contrast': plan['spec']['contrast'],
        'tie_semantics': 'equal_effective_completion_only_not_equal_correctness',
        'limitations': ['Local bindings cannot authenticate reviews, metering or isolation.',
            'Failures, unavailable verification and pending reviews remain in the planned denominator.',
            'Repeated runs are not additional independent tasks.',
            'Extra validation/computed information confounds orchestration-only attribution.']}
