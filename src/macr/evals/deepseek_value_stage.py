"""One default-off developer gate and selected same-input two-arm stage."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import os
from pathlib import Path
import stat

from macr.evals import deepseek_tool_name_probe as probe
from macr.evals.deepseek_policy_manifest import _read, _root, PolicyManifestError
from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_policy_spend import _parse_usd, _usd, _usage_evidence
from macr.evals.deepseek_study_batch import _run_study_core
from macr.evals.deepseek_study_generation import VALUE_PROFILE
from macr.evals.deepseek_study_live import (
    MODEL, ENDPOINT, LOG_POLICY, _StudyGate, _ScriptedWire, _BoundWire)
from macr.evals.deepseek_study_profile import (
    StudyError, digest, implementation, load_neutral_pack, prepare_study,
    load_study, read_json, require)
from macr.evals.deepseek_study_runner import _run_study_cell_core
from macr.investigation.scope import open_directory, validate_workspace_path
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport

SCHEMA = 'macr.deepseek.value-stage.v1'
STRATEGIES = ('direct_initial_3', 'adaptive_read_3')
BUDGETS = {'S0': {'max_requests': 6, 'max_usd': '0.500000'},
           'S1': {'max_requests': 54, 'max_usd': '1.500000'}}
_FILES = ('src/macr/evals/deepseek_value_stage.py',
          'src/macr/evals/deepseek_value_report.py', 'scripts/run_deepseek_value_stage.py')


def _code(project):
    return implementation(project) | {
        name: sha256(_read(project, name, 262144)).hexdigest() for name in _FILES}


def _neutral(pack_root):
    pack = load_neutral_pack(pack_root)
    tasks = pack['tasks']
    projects = Counter(task['project_id'] for task in tasks)
    require(len(tasks) == 9 and len(projects) == 3 and set(projects.values()) == {3},
            'value_grid_invalid')
    for task in tasks:
        names = task['authorized_files']
        require(2 <= len(names) <= 4 and all(name.endswith('.py') for name in names)
            and set(task['initial_evidence_files']) == set(names), 'value_input_parity_invalid')
        require(sum(len(_read(pack_root, task['snapshot_root'] + '/' + name, 4097))
                    for name in names) <= 4096, 'value_initial_source_limit')
    return pack


def _payload(root, project):
    s0 = probe.load_probe(root / 'S0')
    study = load_study(root / 'S1')
    require(s0.get('prompt_variant') == probe.GAP_PROMPT_VARIANT
        and s0['project_root'] == str(project), 'value_s0_scope_invalid')
    require(study.get('execution_profile') == VALUE_PROFILE
        and study['project_root'] == str(project)
        and study['budget'] == BUDGETS['S1'], 'value_s1_scope_invalid')
    _neutral(Path(study['pack_root']))
    cells = [cell for cell in study['cells'] if cell['strategy'] in STRATEGIES]
    require(len(cells) == 18 and len({(cell['task_id'], cell['strategy']) for cell in cells}) == 18,
            'value_selected_grid_invalid')
    sources = [{'path': task['snapshot_root'] + '/' + name, **row}
        for task in study['tasks'] for name, row in sorted(task['manifest']['snapshot']['files'].items())]
    return {'schema': SCHEMA, 'control_root': str(root), 'project_root': str(project),
        'implementation': _code(project),
        'S0': {'control_root': str(root / 'S0'), 'plan_sha256': s0['plan_sha256']},
        'S1': {'control_root': str(root / 'S1'), 'profile_sha256': study['profile_sha256'],
               'cells': cells}, 'pack_root': study['pack_root'], 'pack_sha256': study['pack_sha256'],
        'custodian': study['custodian'], 'sources': sources, 'order_seed': study['order_seed'],
        'budgets': {name: dict(value) for name, value in BUDGETS.items()},
        'max_requests': 60, 'max_usd': '2.000000', 'model': dict(MODEL), 'endpoint': ENDPOINT,
        'price_snapshot': dict(probe.PRICE), 'remote_enabled': False,
        'log_policy': dict(LOG_POLICY), 'automatic_retries': 0, 'resume': False,
        'purpose': 'same complete source; no initial AST; engineering decision, not causal proof'}


def prepare_value_stage(pack_root, *, custodian, out, project_root, seed=20261030):
    pack_root, project = _root(pack_root), _root(project_root)
    _neutral(pack_root)
    _code(project)
    root = Path(out).absolute()
    validate_workspace_path(root, output=True)
    require(not root.exists() and not root.is_relative_to(pack_root), 'value_output_invalid')
    root.mkdir(mode=0o700)
    probe.prepare_probe(project / 'examples/deepseek-tool-name-dev', out=root / 'S0',
                        project_root=project, prompt_variant=probe.GAP_PROMPT_VARIANT)
    prepare_study(pack_root, custodian=custodian, out=root / 'S1', project_root=project,
                  budget=dict(BUDGETS['S1']), seed=seed, execution_profile=VALUE_PROFILE)
    plan = _payload(root, project)
    plan['plan_sha256'] = digest(plan)
    _write_exclusive(root / 'value-stage.json', plan, 131072)
    return plan


def load_value_stage(control):
    root = _root(control)
    value = read_json(root, 'value-stage.json', 131072)
    require(type(value) is dict and value.get('control_root') == str(root), 'value_path_mismatch')
    require(type(value.get('project_root')) is str and value.get('plan_sha256') ==
        digest({key: row for key, row in value.items() if key != 'plan_sha256'}), 'value_plan_invalid')
    expected = _payload(root, _root(value['project_root']))
    require(value == expected | {'plan_sha256': digest(expected)}, 'value_plan_drift')
    return value


def propose_value_approval(control, *, credential_source=None):
    plan = load_value_stage(control)
    return {'schema': 'macr.deepseek.value-stage.approval.v1', 'authorized': False,
        'confirmation_ref': '', 'control_root': plan['control_root'],
        'plan_sha256': plan['plan_sha256'], 'S0': plan['S0'], 'S1': plan['S1'],
        'pack_sha256': plan['pack_sha256'], 'custodian': plan['custodian'], 'sources': plan['sources'],
        'budgets': plan['budgets'], 'max_requests': 60, 'max_usd': '2.000000',
        'model': dict(MODEL), 'endpoint': ENDPOINT, 'price_snapshot': dict(probe.PRICE),
        'credential_source': probe._credential_source(credential_source),
        'credential_use': 'official DeepSeek authentication only, memory-only',
        'log_root': plan['control_root'], 'log_policy': dict(LOG_POLICY),
        'concurrent_requests': 1, 'automatic_retries': 0, 'resume': False, 'ledger_reset': False}


def _fresh(plan):
    for path in (Path(plan['control_root']), Path(plan['S0']['control_root']),
                 Path(plan['S1']['control_root'])):
        require(not any((path / name).exists() for name in
            ('execution.claim.json', 'retention.json', 'cells', 'ledger', 'summary.json')),
            'value_execution_already_claimed')


def _preflight(control, approval, *, simulated=False):
    plan = load_value_stage(control)
    require(type(approval) is dict and approval.get('authorized') is True
        and type(approval.get('confirmation_ref')) is str
        and 0 < len(approval['confirmation_ref'].strip()) <= 128, 'value_approval_missing')
    expected = propose_value_approval(control, credential_source=approval.get('credential_source'))
    require(approval | {'authorized': False, 'confirmation_ref': ''} == expected,
            'value_approval_scope_mismatch')
    root = Path(plan['control_root'])
    for folder in (root, root / 'S0', root / 'S1'):
        with open_directory(folder) as descriptor:
            require(stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o700,
                    'value_log_root_not_private')
    require(simulated or not root.is_relative_to(Path(plan['project_root'])),
            'value_live_log_inside_checkout')
    _fresh(plan)
    return plan


def preflight_value_stage(control, approval):
    return _preflight(control, approval)


def _s0_gate(plan, summary, provenance):
    root = Path(plan['S0']['control_root'])
    require(read_json(root, 'summary.json', 65536) == summary, 'value_s0_receipt_invalid')
    require(read_json(root, 'execution.claim.json', 4096) == {
        'plan_sha256': plan['S0']['plan_sha256'], 'provenance': provenance,
        'state': 'claimed_before_credentials_or_dispatch'}, 'value_s0_receipt_invalid')
    if (summary['batch_halted'] or summary['accounting']['state'] != 'open'
            or summary['accounting']['unknown_held_usd'] != '0.000000'
            or len(summary['rows']) != 2):
        return {'eligible': False, 'reason': 'developer_protocol_or_accounting_failure'}, None
    fingerprints, requests = set(), {}
    cases = probe._cases(root / 'data')
    for case in cases:
        result = read_json(root / 'cells', case['id'] + '.accounted.json', 1048576)
        observation = probe._observation(result, case['terminal'])
        if (result['status'] not in {'semantic_pending', 'ask_user_terminated'}
                or result.get('error_code') or not observation['flow_followed']):
            return {'eligible': False, 'reason': 'developer_protocol_failure'}, None
        for request in result['requests']:
            response = request['response']
            require(response.get('model') == MODEL['model_id']
                and type(response.get('system_fingerprint')) is str
                and bool(response['system_fingerprint'].strip()), 'value_s0_identity_invalid')
            fingerprints.add(response['system_fingerprint'])
            require(request['request_sha256'] not in requests, 'value_s0_request_duplicate')
            requests[request['request_sha256']] = (case['id'], response)
    require(len(requests) == 6 and len(fingerprints) == 1
        and summary['accounting']['request_count'] == 6, 'value_s0_receipt_invalid')
    spent = 0
    for number in range(1, 7):
        reservation = read_json(root / 'ledger', f'{number:03d}.reservation.json', 4096)
        outcome = read_json(root / 'ledger', f'{number:03d}.outcome.json', 4096)
        require(outcome['number'] == number and outcome['state'] == 'settled'
            and all(outcome[key] == reservation[key] for key in
                ('number', 'cell_id', 'request_sha256', 'reserved_micro_usd')),
            'value_s0_receipt_invalid')
        require(outcome['request_sha256'] in requests, 'value_s0_receipt_invalid')
        case_id, response = requests.pop(outcome['request_sha256'])
        measured = _usage_evidence(response['raw_usage'], 8192)
        require(measured is not None and measured ==
            (outcome['charged_micro_usd'], outcome['usage']) and case_id == outcome['cell_id']
            and outcome['usage']['prompt_tokens'] <= 8000, 'value_s0_receipt_invalid')
        spent += outcome['charged_micro_usd']
    require(not requests and _usd(spent) == summary['accounting']['known_accrued_usd'],
            'value_s0_receipt_invalid')
    fingerprint = next(iter(fingerprints))
    return {'eligible': True, 'reason': 'two_prescribed_protocol_flows_only_not_semantic_quality',
        'fingerprint_verified': True, 'fingerprint_sha256': sha256(fingerprint.encode()).hexdigest()}, fingerprint


class _ValueGate(_StudyGate):
    def settle(self, request_sha256, **outcome):
        super().settle(request_sha256, **outcome)
        # Settlement/identity evidence is durable before a content failure can
        # turn an input overrun into a mere known-contract continuation.
        row = self.rows[-1]
        if row['state'] == 'settled' and row['usage']['prompt_tokens'] > 8000:
            raise PolicyRunError('measured_budget_overrun')


def run_value_dryrun(control, *, scripted_by_cell):
    plan = load_value_stage(control)
    _fresh(plan)
    return _execute(plan, scripted_by_cell=scripted_by_cell, key_reader=None,
                    provenance='scripted_offline_fixture')


def execute_value_stage(control, approval, *, enabled=False, key_reader=None, scripted_by_cell=None):
    require(enabled is True, 'value_live_disabled')
    simulated = scripted_by_cell is not None
    plan = _preflight(control, approval, simulated=simulated)
    require(simulated or callable(key_reader), 'value_key_reader_invalid')
    return _execute(plan, scripted_by_cell=scripted_by_cell, key_reader=key_reader,
        provenance='approved_live_fake' if simulated else 'approved_live')


def _execute(plan, *, scripted_by_cell, key_reader, provenance):
    from macr.evals.deepseek_value_report import build_value_report, export_value_packets
    root = Path(plan['control_root'])
    simulated = provenance != 'approved_live'
    scripts = None
    if simulated:
        require(type(scripted_by_cell) is dict and set(scripted_by_cell) == {'S0', 'S1'}
            and all(type(value) is dict for value in scripted_by_cell.values()), 'value_script_invalid')
        require(set(scripted_by_cell['S0']) == set(probe.CASE_IDS)
            and set(scripted_by_cell['S1']) in (set(), {cell['cell_id'] for cell in plan['S1']['cells']}),
            'value_script_scope_invalid')
        scripts = {phase: {key: _ScriptedWire(value) for key, value in entries.items()}
                   for phase, entries in scripted_by_cell.items()}
    try:
        _write_exclusive(root / 'execution.claim.json', {'plan_sha256': plan['plan_sha256'],
            'provenance': provenance, 'state': 'claimed_before_S0_and_credentials'}, 4096)
    except FileExistsError:
        raise StudyError('value_execution_already_claimed') from None
    _write_exclusive(root / 'retention.json', {'plan_sha256': plan['plan_sha256'],
        'contents': ['source', 'requests', 'outputs', 'reasoning', 'usage', 'receipts'], **LOG_POLICY}, 4096)
    key, transports, s1 = None, [], None

    def stage_key():
        nonlocal key
        if key is None:
            key = key_reader()
        return key

    try:
        s0 = probe._execute(probe.load_probe(root / 'S0'),
            scripted_by_cell=None if not simulated else scripted_by_cell['S0'],
            key_reader=None if simulated else stage_key, provenance=provenance)
        gate_result, fingerprint = _s0_gate(plan, s0, provenance)
        if gate_result['eligible']:
            study = load_study(root / 'S1')
            require(study['profile_sha256'] == plan['S1']['profile_sha256'], 'value_s1_scope_drift')
            if simulated:
                require(set(scripts['S1']) == {cell['cell_id'] for cell in plan['S1']['cells']},
                        'value_script_scope_invalid')
            _write_exclusive(root / 'S1' / 'retention.json', {
                'plan_sha256': plan['plan_sha256'], **LOG_POLICY}, 4096)
            gate = None
            captured = {}

            def gate_factory(path, frozen, *, provenance):
                nonlocal gate
                require(frozen['profile_sha256'] == plan['S1']['profile_sha256'],
                        'value_s1_scope_drift')
                gate = _ValueGate(path, frozen, provenance)
                gate.fingerprint = fingerprint
                return gate

            def provider_factory(cell):
                inner = scripts['S1'][cell['cell_id']] if simulated else _ApprovedTransport(stage_key())
                transports.append(inner)
                wire = _BoundWire(inner, simulated=simulated)
                gate.wire = wire
                return DeepSeekProvider(model=MODEL['model_id'], transport=wire, allow_remote=False)

            def cell_runner(*args, **kwargs):
                return _run_study_cell_core(*args, **kwargs, provenance=provenance)

            def report_builder(frozen, results):
                captured.update(results)
                return build_value_report(frozen, results)

            s1 = _run_study_core(root / 'S1', provider_factory=provider_factory,
                cell_runner=cell_runner, provenance=provenance, gate_factory=gate_factory,
                execution_cells=plan['S1']['cells'], report_builder=report_builder)
            packets = export_value_packets(study, captured)
            _write_exclusive(root / 'S1' / 'blind-review-packets.json', {
                'packets': packets['packets'], 'blinding_limit': packets['blinding_limit']}, 1048576)
            _write_exclusive(root / 'S1' / 'review-keeper-mapping.json', packets['keeper_mapping'], 65536)
        accounting = [s0['accounting']] + ([] if s1 is None else [s1['accounting']])
        summary = {'schema': SCHEMA, 'plan_sha256': plan['plan_sha256'], 'provenance': provenance,
            'S0': s0, 'S0_gate': gate_result, 'S1': s1,
            'S1_not_attempted_cells': 18 if s1 is None else
                sum(row['status'] == 'not_attempted' for row in s1['rows']),
            'stage_halted': not gate_result['eligible'] or bool(s1 and s1['batch_halted']),
            'model_calls_live': sum(row['model_calls_live'] for row in accounting),
            'actual_requests': sum(row['request_count'] for row in accounting),
            'known_accrued_usd': _usd(sum(_parse_usd(row['known_accrued_usd'], code='value_receipt_invalid')
                                        for row in accounting)),
            'new_unknown_held_usd': _usd(sum(_parse_usd(row['unknown_held_usd'], code='value_receipt_invalid')
                                           for row in accounting)),
            'interpretation': 'pending independent semantic review; direct remains the operational default'}
        _write_exclusive(root / 'summary.json', summary, 262144)
        return summary
    finally:
        key = None
        for inner in transports:
            if type(inner) is _ApprovedTransport:
                inner._api_key = ''
