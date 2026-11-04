"""Pure, proposal-only contract for a paired repair comparison.

Both strategies receive the full original source, issue and visible tests and
may spend the same cumulative request/token/USD allowance on revision. Debugger
additionally supplies computed evidence and available validation feedback. That
contrast measures the whole strategy, not orchestration alone. A same-evidence
auxiliary needs its own frozen plan; it cannot be reconstructed after success.
``verification_origin`` describes final independent outcome verification, common
to both arms. In-generation checks belong to ``feedback_validation_attempts``.

Explicit spec.v2 adds model-controlled public-test feedback between self-review
and Debugger's public-pass early stop. D-C identifies only that stopping policy;
planned missing/pending/unknown outcomes are interval-valued, not failed answers.

This module reads no files and dispatches nothing. Hashes bind local records;
they do not authenticate a provider, isolation, hidden-test exclusion or review.
The development example uses placeholder commitments, not a runnable experiment.
"""
from __future__ import annotations

from copy import deepcopy
import random
import re
import math

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

SCHEMA = 'macr.repair-comparison.spec.v1'
ARMS = ('direct_api', 'api_debugger')
SCHEMA_V2 = 'macr.repair-comparison.spec.v2'
ARMS_V2 = ('direct_api', 'api_test_feedback', 'api_debugger')
CONTRAST_V2 = 'feedback_and_public_pass_stop_policy'
_CONTRASTS_V2 = {
    'api_test_feedback_vs_direct_api': {'treatment':'api_test_feedback',
        'control':'direct_api','mechanism':'public_test_feedback'},
    'api_debugger_vs_api_test_feedback': {'treatment':'api_debugger',
        'control':'api_test_feedback','mechanism':'public_pass_early_stop_policy'},
    'api_debugger_vs_direct_api': {'treatment':'api_debugger',
        'control':'direct_api','mechanism':'feedback_and_stopping_whole_policy'},
}
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
    _require(spec['schema'] in (SCHEMA, SCHEMA_V2) and type(spec['comparison_id']) is str
        and _ID.fullmatch(spec['comparison_id']) and spec['purpose'] in
        {'development_contract_only', 'preregistered_proposal'}, 'comparison_spec_invalid')
    three = spec['schema'] == SCHEMA_V2
    _require(spec['initial_evidence'] == 'identical_full_source_issue_visible_tests'
        and spec['contrast'] == (CONTRAST_V2 if three else
            'whole_strategy_with_extra_computed_and_validation_information')
        and spec['stop_policy'] == 'unknown_identity_or_cost_halts_no_retry',
        'comparison_evidence_or_stop_policy')
    tasks = spec['tasks']
    _require(type(tasks) is list and 1 <= len(tasks) <= 32, 'comparison_tasks_invalid')
    seen = set(); contents = set()
    for task in tasks:
        _object(task, {'task_id', 'snapshot_sha256', 'issue_sha256',
            'visible_tests_sha256', 'initial_context_sha256'}, 'comparison_task_fields')
        _require(type(task['task_id']) is str and _ID.fullmatch(task['task_id'])
            and task['task_id'] not in seen and all(_hash(v) for k, v in task.items()
            if k != 'task_id'), 'comparison_task_invalid')
        seen.add(task['task_id'])
        if three:
            content = tuple(task[k] for k in ('snapshot_sha256','issue_sha256',
                'visible_tests_sha256','initial_context_sha256'))
            _require(content not in contents, 'comparison_task_content_alias')
            contents.add(content)
    _object(spec['model'], {'configuration_sha256', 'output_contract_sha256',
        'identity_policy'}, 'comparison_model_fields')
    _require(spec['model']['identity_policy'] == 'strict_run_fingerprint' and
        all(_hash(spec['model'][k]) for k in ('configuration_sha256', 'output_contract_sha256')),
        'comparison_model_invalid')
    if three:
        from macr.investigation.repair_loop import output_contract, digest
        _require(any(spec['model']['output_contract_sha256'] == digest(output_contract(version))
                     for version in ('v3','v4','v5','v6')),
                 'comparison_three_arm_output_contract')
    limits = spec['limits']
    _object(limits, {'max_requests', 'input_tokens', 'output_tokens', 'max_usd'},
            'comparison_limit_fields')
    _require(_integer(limits['max_requests'], 1, 3 if three else 8) and
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
    rng = random.Random(spec['order_seed'])
    rng.shuffle(pairs)
    if three:
        base = list(ARMS_V2); rng.shuffle(base)
        rotations = [tuple(base[n:]+base[:n]) for n in range(3)]
        orders = rotations + [tuple(reversed(order)) for order in rotations]
    cells = []
    for index, (task_id, repeat) in enumerate(pairs):
        order = orders[index % 6] if three else ARMS if index % 2 == 0 else ARMS[::-1]
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
    if three:
        plan.update(schema='macr.repair-comparison.plan.v2',
            arms={'direct_api':'full_source_model_controlled_self_review',
                  'api_test_feedback':'public_test_feedback_model_controlled_finalize',
                  'api_debugger':'same_public_test_feedback_stop_on_public_pass'},
            contrasts=deepcopy(_CONTRASTS_V2),
            order_policy='seeded_task_repeat_shuffle_six_orders_balanced_positions',
            current_spend_capacity={'schema':'macr.repair.spend-plan.v1',
                'max_cells':128,'max_requests':384,
                'fits':len(cells)<=128 and len(cells)*limits['max_requests']<=384,
                'meaning':'Size compatibility only; not an executable spend plan or authority.'})
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
    return {'schema': 'macr.repair-comparison.generation.v2' if plan['spec']['schema']==SCHEMA_V2
            else 'macr.repair-comparison.generation.v1', 'task': deepcopy(task),
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
                 final_output=None, verification_receipt=None, proposal_chain=None, test_plan=None,
                 source_scope=None, source_windows=None) -> dict:
    """Prepare a source-bound, unscored handoff; never execute tests or import gold.

    ``candidate_sha256`` means the applied source inventory's ``source_id``.
    Output/verification digests use ``result_digest`` over the supplied JSON value.
    An answered output must be the parsed repair proposal; no provider-envelope
    guessing is performed. Single proposals apply against the frozen original.
    Multi-round callers pass the entire previously verified parsed proposal chain,
    not just the last incremental edit. Local chain consistency still cannot
    authenticate who produced those outputs or whether verification ran.
    Windowed mode commits to complete originals and an exact frozen model view;
    the exported candidate always contains complete source files.
    The recipient supplies a locked independent review later through build_report;
    this function cannot attest provenance or turn a claimed pass into success.
    """
    from macr.investigation.repair_io import bounded_json
    from macr.investigation.repair_edits import apply_edits, validate_files
    from macr.investigation.repair_loop import source_id, output_contract, digest

    full_originals = source_windows is not None
    plan = validate_comparison(plan)
    cell = next((c for c in plan['cells'] if c['cell_id'] == cell_id), None)
    _require(cell is not None, 'comparison_score_cell_missing')
    task = next(t for t in plan['spec']['tasks'] if t['task_id'] == cell['task_id'])
    finalize_versions = ('v3','v4','v5','v6') if plan['spec']['schema']==SCHEMA_V2 else ('v2',)
    finalize_allowed = any(plan['spec']['model']['output_contract_sha256'] ==
                           digest(output_contract(version)) for version in finalize_versions)
    selection_allowed = (plan['spec']['schema']==SCHEMA_V2 and
        plan['spec']['model']['output_contract_sha256']==digest(output_contract('v6')))
    if type(final_output) is dict:
        if final_output.get('status') == 'select_candidate':
            _require(selection_allowed and proposal_chain is not None,
                     'comparison_score_selection_chain_required')
        if selection_allowed:
            _require('selected_content_id' in final_output and
                (final_output.get('status') == 'select_candidate' or final_output['selected_content_id'] is None),
                'comparison_score_selection_field')
    _require(type(row) is dict and row.get('review') is None, 'comparison_score_review_must_be_separate')
    _row(row, task, plan['spec'])
    validate_files(original_files, full_originals=full_originals)
    _require(type(protected_paths) is list and 1 <= len(protected_paths) <= 8
        and all(type(p) is str and p in original_files for p in protected_paths)
        and len(set(protected_paths)) == len(protected_paths), 'comparison_score_protected_paths')
    _require(type(editable_paths) is list and 1 <= len(editable_paths) <= len(original_files)
        and all(type(p) is str and p in original_files for p in editable_paths)
        and len(set(editable_paths)) == len(editable_paths)
        and not set(editable_paths) & set(protected_paths), 'comparison_score_edit_authority')
    _require(source_id(original_files, full_originals=full_originals) == task['snapshot_sha256']
        and source_id({p:original_files[p] for p in protected_paths},
                      full_originals=full_originals) == task['visible_tests_sha256'],
        'comparison_score_source_binding')
    if test_plan is not None:
        from macr.investigation.repair_test_plan import validate_test_plan
        validate_test_plan(original_files,protected_paths,test_plan)
    if full_originals:
        from macr.investigation.repair_windows import project_source_windows, windowed_context_id
        _require(source_scope == 'declared_files', 'comparison_score_windowed_source_scope')
        project_source_windows(original_files, source_windows, protected_paths=protected_paths)
        _require(windowed_context_id(original_files, task['issue_sha256'], source_windows,
            source_scope=source_scope, test_plan=test_plan) == task['initial_context_sha256'],
            'comparison_score_windowed_initial_context')
    changed = None; last_applied = original_files; has_candidate = False; accepted = {}
    if proposal_chain is not None:
        bounded_json(proposal_chain, max_bytes=65536, max_depth=16, utf8_bytes=True)
        _require(edits is None and type(proposal_chain) is list
            and 1 <= len(proposal_chain) <= min(3, row['requests']),
                 'comparison_score_chain_contract')
        for index, proposal in enumerate(proposal_chain):
            _require(type(proposal) is dict and proposal.get('base_content_id') ==
                     source_id(last_applied, full_originals=full_originals)
                and proposal.get('status') in (('candidate', 'no_candidate', 'insufficient_evidence',
                                               'finalize_current', 'select_candidate') if selection_allowed else
                                              ('candidate', 'no_candidate', 'insufficient_evidence','finalize_current'))
                and type(proposal.get('edits')) is list, 'comparison_score_chain_binding')
            if selection_allowed:
                _require('selected_content_id' in proposal and (proposal['status'] == 'select_candidate'
                    or proposal['selected_content_id'] is None), 'comparison_score_selection_field')
            if proposal['status'] == 'candidate':
                last_applied = apply_edits(last_applied, proposal['edits'], editable_paths=editable_paths,
                    full_originals=full_originals, source_windows=source_windows)
                if full_originals:
                    project_source_windows(last_applied, source_windows, protected_paths=protected_paths)
                _require(all(last_applied[p] == original_files[p] for p in protected_paths),
                         'comparison_score_chain_protected')
                has_candidate = True
                accepted[source_id(last_applied, full_originals=full_originals)] = last_applied
            elif proposal['status'] == 'select_candidate':
                selected = proposal['selected_content_id']
                _require(index == len(proposal_chain)-1 and proposal['edits'] == []
                    and _hash(selected) and selected in accepted, 'comparison_score_selection_contract')
                changed = accepted[selected]
            elif proposal['status'] == 'finalize_current':
                _require(finalize_allowed and has_candidate and index == len(proposal_chain)-1
                    and proposal['edits'] == [], 'comparison_score_finalize_contract')
            else:
                _require(index == len(proposal_chain)-1 and proposal['edits'] == [],
                         'comparison_score_chain_after_abstention')
        _require(final_output == proposal_chain[-1], 'comparison_score_chain_final_output')
        if proposal_chain[-1]['status'] in ('candidate', 'finalize_current'):
            changed = last_applied
        expected_candidate = source_id(changed, full_originals=full_originals) if changed is not None else None
        _require(row['candidate_sha256'] == expected_candidate, 'comparison_score_candidate_binding')
    elif row['candidate_sha256'] is None:
        _require(edits is None or edits == [], 'comparison_score_candidate_unexpected')
    else:
        changed = apply_edits(original_files, edits, editable_paths=editable_paths,
            full_originals=full_originals, source_windows=source_windows)
        if full_originals:
            project_source_windows(changed, source_windows, protected_paths=protected_paths)
        last_applied = changed
        _require(source_id(changed, full_originals=full_originals) == row['candidate_sha256']
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
            (('candidate', 'no_candidate', 'insufficient_evidence', 'finalize_current','select_candidate')
             if selection_allowed else ('candidate', 'no_candidate', 'insufficient_evidence','finalize_current'))
            and type(final_output.get('edits')) is list, 'comparison_score_proposal_contract')
        finalized = final_output['status'] == 'finalize_current'
        selected = final_output['status'] == 'select_candidate'
        _require(not finalized or (finalize_allowed and proposal_chain is not None),
                 'comparison_score_finalize_chain_required')
        if changed is not None:
            expected_edits = proposal_chain[-1]['edits'] if proposal_chain is not None else edits
            _require((final_output['status'] == 'candidate' or finalized or selected)
                and final_output['edits'] == expected_edits,
                     'comparison_score_proposal_candidate_mismatch')
        else:
            _require(final_output['status'] != 'candidate' and final_output['edits'] == [],
                     'comparison_score_proposal_candidate_missing')
    packet = {'schema': 'macr.repair-comparison.score-packet.v2' if plan['spec']['schema']==SCHEMA_V2
              else 'macr.repair-comparison.score-packet.v1',
        'comparison_sha256': plan['comparison_sha256'], 'cell': deepcopy(cell),
        'result': deepcopy(row), 'original_source_id': source_id(original_files, full_originals=full_originals),
        'protected_paths': sorted(protected_paths),
        'candidate_files': {p:b.decode('utf-8') for p,b in sorted(changed.items())} if changed else None,
        'proposal_chain': deepcopy(proposal_chain),
        'proposal_chain_sha256': result_digest(proposal_chain) if proposal_chain is not None else None,
        'last_applied_source_id': source_id(last_applied, full_originals=full_originals),
        'final_output': deepcopy(final_output), 'verification_receipt': deepcopy(verification_receipt),
        'scoring_commitments': deepcopy(plan['spec']['scoring']),
        'semantic_truth_validated': False, 'provenance_authenticated': False}
    if test_plan is not None: packet['test_plan']=deepcopy(test_plan)
    if full_originals: packet['source_windows']=deepcopy(source_windows)
    if source_scope is not None:
        from macr.investigation.repair_sources import source_view
        packet['source_view']=source_view(source_scope)
    packet['packet_sha256'] = result_digest(packet)
    return packet


def build_report(plan: dict, results: dict) -> dict:
    """Retain every planned cell. Binding validation is not independent attestation."""
    plan = validate_comparison(plan)
    _require(type(results) is dict and set(results) <= {c['cell_id'] for c in plan['cells']},
             'comparison_result_cell_unknown')
    tasks = {t['task_id']: t for t in plan['spec']['tasks']}
    three = plan['spec']['schema'] == SCHEMA_V2
    active_arms = ARMS_V2 if three else ARMS
    arms = {arm: {'planned': len(plan['cells']) // len(active_arms), 'scored': 0, 'unscored': 0,
        'identity_invalid': 0, 'excluded_locked_reviews': 0,
        'statuses': dict.fromkeys(STATUSES, 0), 'outcomes': dict.fromkeys(OUTCOMES, 0),
        'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'known_usd': 0,
        'unknown_reserved_usd': 0, 'feedback_validation_attempts': 0,
        'timing_ms': dict.fromkeys(('preparation', 'provider', 'verification', 'total'), 0)}
        for arm in active_arms}
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
    if three:
        return _three_arm_report(plan, arms, rows, must_stop)
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


def _completion(row, identity_valid):
    """An interval-valued planned outcome, not missing-answer imputation.

    A terminal pipeline failure is observed noncompletion. An answer with no
    usable locked review, an unrun cell or unknown outcome remains unresolved.
    """
    if row['review'] is not None and identity_valid:
        return int(row['review']['outcome'] in SUCCESS)
    if row['status'] in {'contract_failed','transport_failed','verification_unavailable',
                         'verification_timeout','budget_exceeded'}:
        return 0
    return None


def _three_arm_report(plan, arms, rows, must_stop):
    """Descriptive contrasts with bounded, explicitly assumed task independence.

    Repeat-level differences are averaged inside each task first. For X in
    [-1,1], Hoeffding gives radius sqrt(2 log(2/alpha)/n). Bonferroni across
    three frozen contrasts uses alpha=.05/3; unknown outcomes widen the mean
    bounds before that radius is added. This is not a semantic attestation or
    a claim that tasks from the same project are actually independent.
    """
    spec=plan['spec']; tasks=spec['tasks']; repeats=spec['repeats']
    states={key:_completion(*value) for key,value in rows.items()}
    for arm,stats in arms.items():
        outcomes=[states[(task['task_id'],repeat,arm)] for task in tasks for repeat in range(repeats)]
        success=sum(value==1 for value in outcomes)
        unresolved=sum(value is None for value in outcomes)
        stats['completion_bounds']={'observed_successes':success,
            'definite_noncompletions':len(outcomes)-success-unresolved,'unresolved':unresolved,
            'rate_bounds':[success/len(outcomes),(success+unresolved)/len(outcomes)]}
    contrasts={}
    radius=math.sqrt(2*math.log(2*len(_CONTRASTS_V2)/0.05)/len(tasks))
    for name,definition in _CONTRASTS_V2.items():
        treatment,control=definition['treatment'],definition['control']
        quality=dict.fromkeys(('treatment_win','control_win','tie','not_comparable',
                               'both_success','both_noncompletion'),0)
        e2e=dict.fromkeys(('treatment_only_completed','control_only_completed',
                          'both_completed','both_noncompletion','unresolved'),0)
        task_bounds=[]
        for task in tasks:
            lower=upper=0.0
            for repeat in range(repeats):
                tkey=(task['task_id'],repeat,treatment);ckey=(task['task_id'],repeat,control)
                t,c=states[tkey],states[ckey]
                lower+=(0 if t is None else t)-(1 if c is None else c)
                upper+=(1 if t is None else t)-(0 if c is None else c)
                if t is None or c is None:e2e['unresolved']+=1
                else:
                    e2e['both_completed' if t==c==1 else 'both_noncompletion' if t==c==0
                        else 'treatment_only_completed' if t else 'control_only_completed']+=1
                (tr,tvalid),(cr,cvalid)=rows[tkey],rows[ckey]
                if (not tvalid or not cvalid or tr['review'] is None or cr['review'] is None
                        or tr['model_identity_sha256']!=cr['model_identity_sha256']):
                    quality['not_comparable']+=1
                elif t==c:
                    quality['tie']+=1
                    quality['both_success' if t else 'both_noncompletion']+=1
                else:quality['treatment_win' if t else 'control_win']+=1
            task_bounds.append((lower/repeats,upper/repeats))
        low=sum(bound[0] for bound in task_bounds)/len(tasks)
        high=sum(bound[1] for bound in task_bounds)/len(tasks)
        contrasts[name]={**deepcopy(definition),'planned_pairs':len(tasks)*repeats,
            'conditional_quality':quality,'e2e_pair_states':e2e,
            'task_cluster':{'independent_tasks_assumed':len(tasks),'repeats_per_task':repeats,
                'fully_ascertained_tasks':sum(a==b for a,b in task_bounds),
                'difference_bounds':[low,high],'point_difference':low if low==high else None},
            'uncertainty':{'method':'hoeffding_task_means_bonferroni',
                'simultaneous_confidence':0.95,'contrasts':len(_CONTRASTS_V2),
                'radius':radius,'interval':[max(-1.0,low-radius),min(1.0,high+radius)],
                'assumptions':['Independent bounded task outcomes; project/family dependence can invalidate coverage.',
                    'Repeats are averaged within task, never counted as independent samples.',
                    'Terminal failures are noncompletion; missing, pending and unknown outcomes remain interval-valued.',
                    'Local review/identity bindings are not semantic or producer attestation.']}}
    known=sum(stats['known_usd'] for stats in arms.values())
    reserved=sum(stats['unknown_reserved_usd'] for stats in arms.values())
    for stats in arms.values():
        for key in ('known_usd','unknown_reserved_usd'):stats[key]=_money(stats[key])
    return {'schema':'macr.repair-comparison.report.v2','comparison_sha256':plan['comparison_sha256'],
        'planned_cells':plan['planned_cells'],'task_count':len(tasks),'repeats':repeats,
        'arms':arms,'contrasts':contrasts,'actual_known_usd':_money(known),
        'unknown_reserved_usd':_money(reserved),'must_stop':must_stop,
        'endpoint':'effective_completion','full_repair_rate_established':False,
        'endpoint_limitation':'No task strata are declared; full_repair and necessary_abstention counts remain separate.',
        'automatic_retry_allowed':False,'remote_enabled':False,'execution_authorized':False,
        'semantic_truth_validated':False,'provenance_authenticated':False,
        'hidden_test_exclusion_authenticated':False,'equal_ceilings_prove_equal_consumption':False,
        'orchestration_only_gain_established':False,'contrast':spec['contrast'],
        'current_spend_capacity':deepcopy(plan['current_spend_capacity']),
        'tie_semantics':'equal_reviewed_completion_not_equal_correctness; unresolved pairs are not ties',
        'limitations':['Eight-task pilots are exploratory; this report establishes no gain, power or general superiority.',
            'D-C is only the public-pass early-stop strategy, C-B feedback, D-B their whole policy.',
            'Failures and missing cells stay in the planned denominator; unresolved outcomes are not set to zero.',
            'Exact task-content aliases are rejected, but independence across related mechanisms/projects is unverified.']}
