"""Evaluator-only final observations. No dispatch, provenance upgrade or credit.

A final receipt is a bounded producer claim, not authenticated execution. This
module deliberately leaves the generation result and pending review unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import stat

from macr.investigation import repair_loop as loop
from macr.investigation.repair_io import bounded_json, encode_bounded
from macr.investigation.repair_test_plan import validate_plan_shape
from macr.investigation.scope import open_directory, read_safe
from .repair_comparison import build_report, result_digest, validate_comparison
from .repair_scoring_stage import JSON_LIMIT, _inventory, prepare_scoring

_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_STATES = {'completed', 'timeout', 'resource_limit', 'error', 'blocked', 'cancelled', 'outcome_unknown'}
_COUNTS = {'passed', 'failed', 'errors', 'collection_errors', 'skipped', 'xfailed', 'xpassed', 'executed', 'collected'}


def _need(ok, code):
    if not ok:
        raise ValueError('comparison_final_' + code)


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _integer(value, low=0, high=10**12):
    return type(value) is int and low <= value <= high


def validate_final_policy(policy, custodian, plan):
    """Check pre-generation commitments; an identity commitment is not trust."""
    plan = validate_comparison(plan)
    bounded_json(policy, max_bytes=65536, max_depth=16)
    _need(type(policy) is dict and set(policy) == {'schema', 'tasks', 'runner_sha256',
        'environment_sha256', 'dependency_lock_sha256', 'calibration_sha256', 'limits'}
        and policy['schema'] == 'macr.repair.final-policy.v1', 'policy')
    for field in ('runner_sha256', 'environment_sha256', 'dependency_lock_sha256', 'calibration_sha256'):
        _need(_hash(policy[field]), 'environment_commitment')
    limits = policy['limits']
    _need(type(limits) is dict and set(limits) == {'cpu_seconds', 'memory_bytes', 'pids',
        'wall_seconds', 'output_bytes'} and all(_integer(value, 1) for value in limits.values())
        and limits['cpu_seconds'] <= 3600 and limits['wall_seconds'] <= 3600
        and limits['memory_bytes'] <= 64 * 1024**3 and limits['pids'] <= 4096
        and limits['output_bytes'] <= 16 * 1024**2, 'limits')
    tasks = policy['tasks']
    _need(type(tasks) is list and 1 <= len(tasks) <= 32, 'tasks')
    ids = []
    for task in tasks:
        _need(type(task) is dict and set(task) == {'task_id', 'kind', 'hidden_test_plan', 'criteria_sha256'}
            and type(task['task_id']) is str and _ID.fullmatch(task['task_id'])
            and task['kind'] in ('defective', 'healthy', 'insufficient_evidence')
            and _hash(task['criteria_sha256']), 'task')
        validate_plan_shape(task['hidden_test_plan'])
        ids.append(task['task_id'])
    _need(ids == sorted(ids) and len(set(ids)) == len(ids)
        and set(ids) == {task['task_id'] for task in plan['spec']['tasks']}, 'task_scope')
    _need(result_digest(policy) == plan['spec']['scoring']['rubric_sha256'], 'policy_commitment')
    bounded_json(custodian, max_bytes=4096, max_depth=4)
    _need(type(custodian) is dict and set(custodian) == {'schema', 'custodian_id', 'protocol_sha256'}
        and custodian['schema'] == 'macr.repair.final-custodian.v1'
        and type(custodian['custodian_id']) is str and _ID.fullmatch(custodian['custodian_id'])
        and _hash(custodian['protocol_sha256'])
        and result_digest(custodian) == plan['spec']['scoring']['custodian_commitment_sha256'], 'custodian_commitment')
    return deepcopy(policy)


def _verify_exact_tree(root, expected):
    """No ignored files: extra config/helper files, links and aliases all fail."""
    root = Path(root).absolute()
    nodes = {}
    for path in expected:
        node = nodes
        for part in path.split('/'):
            node = node.setdefault(part, {})
    anchors = {}
    def walk(fd, names, prefix=''):
        info = os.fstat(fd)
        _need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'stage_permissions')
        _need(set(os.listdir(fd)) == set(names), 'stage_inventory')
        for name, children in names.items():
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if children:
                _need(stat.S_ISDIR(info.st_mode), 'stage_entry')
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    held = os.fstat(child)
                    _need((held.st_dev, held.st_ino) == (info.st_dev, info.st_ino), 'stage_changed')
                    walk(child, children, path + '/')
                finally:
                    os.close(child)
            else:
                _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600, 'stage_entry')
                _need(read_safe(root, path, len(expected[path])) == expected[path], 'stage_bytes')
        _need(set(os.listdir(fd)) == set(names), 'stage_changed')
    with open_directory(root, observed=anchors) as directory:
        walk(directory, nodes)
        with open_directory(root, identities=anchors):
            pass


def prepare_final(stage, record, plan, cell_id, manifest, overlay_root, policy, custodian):
    """Rebuild the source handoff and verify every saved byte before requesting final tests.

    Public checks use unchanged generation tests. Hidden checks use the overlay.
    No public result is reused, even when the overlay replaced a public file.
    """
    plan = validate_comparison(plan)
    validate_final_policy(policy, custodian, plan)
    metadata, original, candidate, hidden_original, hidden_candidate = prepare_scoring(
        record, plan, cell_id, manifest, overlay_root)
    expected = {name: encode_bounded(value, JSON_LIMIT) for name, value in metadata.items()}
    for variant, files in (('original', hidden_original), ('candidate', hidden_candidate)):
        if files is not None:
            expected.update({variant + '/' + path: body for path, body in files.items()})
    _verify_exact_tree(stage, expected)
    packet = metadata['packet.json']; staging = metadata['staging.json']
    task = next(task for task in policy['tasks'] if task['task_id'] == packet['cell']['task_id'])
    hidden_names = sorted(task['hidden_test_plan']['nodeids'])
    overlay_paths = {item['path'] for item in manifest['tasks']
        if item['task_id'] == packet['cell']['task_id'] for item in item['files']}
    _need(all(name.split('::')[0] in overlay_paths for name in hidden_names), 'hidden_selector_scope')
    public_names = loop.test_manifest(original, packet['protected_paths'], test_plan=packet.get('test_plan'),
        full_originals=packet.get('source_windows') is not None)
    trees = {'public_original': original, 'hidden_original': hidden_original}
    if candidate is not None:
        trees.update(public_candidate=candidate, hidden_candidate=hidden_candidate)
    checks = []
    for name, files in trees.items():
        suite, variant = name.split('_')
        selectors = public_names if suite == 'public' else hidden_names
        check = {'suite': suite, 'variant': variant, 'tree_sha256': result_digest(_inventory(files)),
            'framework': ('pytest' if packet.get('test_plan') else 'unittest') if suite == 'public' else 'pytest',
            'selectors': selectors}
        check['check_id'] = result_digest({'comparison_sha256': plan['comparison_sha256'],
            'cell': packet['cell'], 'staging_sha256': staging['staging_sha256'],
            'policy_sha256': result_digest(policy), **check})
        checks.append(check)
    request = {'schema': 'macr.repair.final-request.v1', 'phase': 'final',
        'comparison_sha256': plan['comparison_sha256'], 'cell': packet['cell'],
        'packet_sha256': packet['packet_sha256'], 'staging_sha256': staging['staging_sha256'],
        'overlay_sha256': result_digest(manifest), 'policy': deepcopy(policy),
        'custodian': deepcopy(custodian), 'generation_result': deepcopy(packet['result']),
        'generation_receipt_sha256': packet['result']['verification_receipt_sha256'],
        'candidate_source_id': packet['result']['candidate_sha256'], 'task_kind': task['kind'],
        'checks': checks, 'execution_authorized': False, 'scoring_credit_awarded': False,
        'provenance_authenticated': False}
    request['request_sha256'] = result_digest(request)
    return request


def _check_observation(raw, check, limits):
    _need(type(raw) is dict and set(raw) == {'check_id', 'execution_status', 'exit_code', 'test_counts',
        'outcomes', 'failure_kinds', 'limits_observed', 'output_truncated', 'cleanup_status', 'elapsed_ms'}
        and raw['check_id'] == check['check_id'] and raw['execution_status'] in _STATES
        and (raw['exit_code'] is None or _integer(raw['exit_code'], -255, 255))
        and type(raw['output_truncated']) is bool and raw['cleanup_status'] in ('closed', 'outcome_unknown')
        and _integer(raw['elapsed_ms']) and type(raw['outcomes']) is dict, 'observation')
    measured = raw['limits_observed']
    _need(measured is None or type(measured) is dict and set(measured) ==
        {'cpu_ms', 'memory_bytes', 'pids', 'output_bytes'}
        and all(_integer(value) for value in measured.values()), 'limits_observed')
    within_limits = (measured is not None and 0 < measured['pids'] <= limits['pids']
        and 0 < measured['memory_bytes'] <= limits['memory_bytes']
        and measured['cpu_ms'] <= limits['cpu_seconds'] * 1000
        and measured['output_bytes'] <= limits['output_bytes']
        and raw['elapsed_ms'] <= limits['wall_seconds'] * 1000)
    counts = raw['test_counts']
    _need(counts is None or type(counts) is dict and set(counts) <= _COUNTS
        and all(_integer(value, 0, 65536) for value in counts.values()), 'counts')
    outcomes = raw['outcomes']
    _need(type(raw['failure_kinds']) is dict and set(raw['failure_kinds']) ==
        {name for name, value in outcomes.items() if value == 'failed'}
        and all(type(value) is str and value in ('assertion', 'exception')
                for value in raw['failure_kinds'].values()), 'failure_kinds')
    _need(set(outcomes) <= set(check['selectors']) and all(type(value) is str and value in
        ('passed', 'failed', 'error', 'skipped', 'xfailed', 'xpassed') for value in outcomes.values()), 'outcomes')
    if raw['execution_status'] != 'completed':
        _need(outcomes == {} and counts is None and raw['exit_code'] is None, 'incomplete_observation')
        return 'not_established'
    _need(type(counts) is dict, 'completed_counts')
    # Exact collected identities and category totals are necessary, not enough
    # to prove truthful runtime collection. Collection errors may omit IDs.
    category = {'passed': 'passed', 'failed': 'failed', 'error': 'errors', 'skipped': 'skipped',
                'xfailed': 'xfailed', 'xpassed': 'xpassed'}
    wanted = {name: sum(value == outcome for value in outcomes.values()) for outcome, name in category.items()}
    _need(all(counts.get(name, 0) == total for name, total in wanted.items()), 'count_contradiction')
    actual = sum(wanted[name] for name in ('passed', 'failed', 'errors', 'xfailed', 'xpassed'))
    _need(counts.get('executed', actual) == actual
        and counts.get('collected', len(outcomes)) == len(outcomes), 'count_contradiction')
    usable = (within_limits and set(outcomes) == set(check['selectors']) and not raw['output_truncated']
        and raw['cleanup_status'] == 'closed' and not counts.get('collection_errors', 0)
        and all(value in ('passed', 'failed') for value in outcomes.values()))
    if not usable:
        return 'not_established'
    passed = all(value == 'passed' for value in outcomes.values())
    _need(raw['exit_code'] == (0 if passed else 1), 'exit_contradiction')
    return ('passed' if passed else 'assertion_failed'
        if all(kind == 'assertion' for kind in raw['failure_kinds'].values()) else 'test_failed')


def _validate_request_checks(request):
    """Protect direct API consumers too; CLI additionally rebuilds from bytes."""
    task = next(task for task in request['policy']['tasks'] if task['task_id'] == request['cell']['task_id'])
    _need(request['task_kind'] == task['kind']
        and all(_hash(request[field]) for field in
            ('packet_sha256', 'staging_sha256', 'overlay_sha256')), 'request_metadata')
    pairs = [('public', 'original'), ('hidden', 'original')]
    if request['candidate_source_id'] is not None:
        _need(_hash(request['candidate_source_id']), 'candidate_identity')
        pairs += [('public', 'candidate'), ('hidden', 'candidate')]
    checks = request['checks']
    _need(type(checks) is list and len(checks) == len(pairs), 'request_checks')
    public = None
    for check, (suite, variant) in zip(checks, pairs):
        _need(type(check) is dict and set(check) == {'suite', 'variant', 'tree_sha256',
            'framework', 'selectors', 'check_id'} and check['suite'] == suite
            and check['variant'] == variant and _hash(check['tree_sha256'])
            and check['framework'] in ('unittest', 'pytest'), 'request_check')
        names = check['selectors']
        _need(type(names) is list and 1 <= len(names) <= 32
            and all(type(name) is str and 0 < len(name.encode('utf-8')) <= 2048
                and not any(ord(char) < 32 or ord(char) == 127 for char in name) for name in names)
            and names == sorted(set(names)), 'request_selectors')
        if suite == 'hidden':
            _need(check['framework'] == 'pytest'
                and names == sorted(task['hidden_test_plan']['nodeids']), 'request_hidden_selectors')
        elif public is None:
            public = check['framework'], names
        else:
            _need((check['framework'], names) == public, 'request_public_selectors')
        expected = result_digest({'comparison_sha256':request['comparison_sha256'],
            'cell':request['cell'], 'staging_sha256':request['staging_sha256'],
            'policy_sha256':result_digest(request['policy']),
            **{key:value for key,value in check.items() if key != 'check_id'}})
        _need(check['check_id'] == expected, 'request_check_identity')


def import_final(request, receipt, *, plan):
    """Import an UNAUTHENTICATED final observation, preserving zero scoring credit.

    Call prepare_final against the current input record/stage first. No data
    field, signature-looking string or producer label can upgrade model origin.
    """
    plan = validate_comparison(plan)
    _need(type(request) is dict and set(request) == {'schema', 'phase', 'comparison_sha256',
        'cell', 'packet_sha256', 'staging_sha256', 'overlay_sha256', 'policy', 'custodian',
        'generation_result', 'generation_receipt_sha256', 'candidate_source_id', 'task_kind', 'checks',
        'execution_authorized', 'scoring_credit_awarded', 'provenance_authenticated', 'request_sha256'}
        and request['execution_authorized'] is request['scoring_credit_awarded'] is request['provenance_authenticated'] is False
        and request.get('schema') == 'macr.repair.final-request.v1'
        and request.get('phase') == 'final'
        and request.get('comparison_sha256') == plan['comparison_sha256']
        and request.get('request_sha256') == result_digest({key: value for key, value in request.items()
                                                          if key != 'request_sha256'}), 'request_binding')
    validate_final_policy(request['policy'], request['custodian'], plan)
    cell = next((cell for cell in plan['cells'] if cell['cell_id'] == request['cell']['cell_id']), None)
    _need(cell == request['cell'], 'cell_binding')
    _need(request['generation_result'].get('review') is None
        and request['candidate_source_id'] == request['generation_result'].get('candidate_sha256')
        and request['generation_receipt_sha256'] == request['generation_result'].get('verification_receipt_sha256'),
        'generation_binding')
    _need(request['overlay_sha256'] == plan['spec']['scoring']['hidden_tests_sha256'], 'overlay_commitment')
    _validate_request_checks(request)
    bounded_json(receipt, max_bytes=65536, max_depth=16)
    _need(type(receipt) is dict and set(receipt) == {'schema', 'phase', 'request_sha256',
        'origin', 'execution', 'checks', 'preparation_ms', 'total_ms'}
        and receipt['schema'] == 'macr.repair.final-receipt.v1' and receipt['phase'] == 'final'
        and receipt['request_sha256'] == request['request_sha256']
        and receipt['origin'] == 'independent_custodian_reported'
        and type(receipt['checks']) is list and len(receipt['checks']) == len(request['checks'])
        and _integer(receipt['preparation_ms']) and _integer(receipt['total_ms']), 'receipt_binding')
    execution = {field:request['policy'][field] for field in
        ('runner_sha256', 'environment_sha256', 'dependency_lock_sha256', 'calibration_sha256')}
    _need(receipt['execution'] == execution, 'execution_binding')
    observations = {}
    for raw, check in zip(receipt['checks'], request['checks']):
        observations[check['suite'] + '_' + check['variant']] = _check_observation(raw, check, request['policy']['limits'])
    _need(receipt['total_ms'] >= receipt['preparation_ms'] + sum(raw['elapsed_ms'] for raw in receipt['checks']),
        'timing_contradiction')
    # The unchanged generation row keeps historical public receipt/cost/timing
    # separate. Final latency is retained independently, never counted twice.
    row = deepcopy(request['generation_result'])
    report = build_report(plan, {cell['cell_id']: row})
    _need(row['review'] is None, 'generation_review')
    unknown = any(raw['execution_status'] == 'outcome_unknown' or raw['cleanup_status'] != 'closed'
                  for raw in receipt['checks'])
    timeout = any(raw['execution_status'] == 'timeout' for raw in receipt['checks'])
    complete = all(value != 'not_established' for value in observations.values())
    must_stop = report['must_stop'] or unknown
    status = ('final_verification_unknown' if unknown else 'final_verification_timeout' if timeout else
              'pending_independent_review' if complete else 'final_verification_not_established')
    final = {'schema': 'macr.repair.final-observation.v1', 'phase': 'final',
        'comparison_sha256': plan['comparison_sha256'], 'cell': deepcopy(cell),
        'request_sha256': request['request_sha256'], 'receipt_sha256': result_digest(receipt),
        'request': deepcopy(request), 'receipt': deepcopy(receipt), 'status': status,
        'test_observations': observations,
        'original_hidden_assertion_failure': observations['hidden_original'] == 'assertion_failed',
        'candidate_all_required_tests_passed': all(observations.get(name) == 'passed'
            for name in ('public_candidate', 'hidden_candidate')),
        'generation_result': row, 'must_stop': must_stop, 'final_verification_must_stop': unknown, 'automatic_retry_allowed': False,
        'scoring_credit_awarded': False, 'semantic_truth_validated': False,
        'provenance_authenticated': False, 'execution_authorized': False,
        'reported_target_execution_authenticated': False}
    final['observation_sha256'] = result_digest(final)
    report['final_scoring'] = {cell['cell_id']: {'status': status, 'must_stop': must_stop, 'final_verification_must_stop': unknown,
        'receipt_sha256': final['receipt_sha256'], 'scoring_credit_awarded': False,
        'preparation_ms': receipt['preparation_ms'], 'verification_ms': sum(raw['elapsed_ms'] for raw in receipt['checks']),
        'total_ms': receipt['total_ms']}}
    report['must_stop'] = must_stop
    return final, {cell['cell_id']: row}, report
