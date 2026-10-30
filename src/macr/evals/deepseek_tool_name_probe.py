"""Two fixed developer protocol checks; no benchmark, custody or resume shortcut."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat

from macr.evals.deepseek_policy_manifest import (
    _read, _root, DEFAULT_BUDGET, PolicyManifestError,
    prepare_policy_manifest, load_policy_manifest,
    STUDY_EVIDENCE_PROMPT_VERSION, STUDY_GAP_PROMPT_VERSION)
from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_study_generation import EVIDENCE_PROFILE, get_generation
from macr.evals.deepseek_study_profile import (
    StudyError, digest, implementation, read_json, require, _inventory)
from macr.evals.deepseek_study_live import (
    MODEL, ENDPOINT, LOG_POLICY, _StudyGate, _ScriptedWire, _BoundWire)
from macr.evals.deepseek_study_runner import _run_study_cell_core
from macr.investigation.scope import open_directory, validate_workspace_path
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport

SCHEMA = 'macr.deepseek.tool-name.probe.v1'
CASES_SHA256 = 'a443dfc734d53a19bc67bbb5c3f8810bb32f71e39459ae15fa45855b86582a74'
SOURCES = {
    'entry.py': (59, '60954e8388bddbec9036d46b89ff96cc6456606a2ee66942ebce70c2be27c990'),
    'helper.py': (32, '0adcb531f78a384cb8bbd8939cd48524aab0dcfd4aa17deca3d3eef5598e809e')}
CASE_IDS = ('dev-finish', 'dev-ask')
GAP_PROMPT_VARIANT = 'concise-gap-v1'
PROMPT_VARIANTS = ('baseline', GAP_PROMPT_VARIANT)
_PROMPT_VERSIONS = {'baseline': STUDY_EVIDENCE_PROMPT_VERSION,
                    GAP_PROMPT_VARIANT: STUDY_GAP_PROMPT_VERSION}
BUDGET = {'max_requests': 6, 'max_usd': '0.500000'}
PRICE = {'checked_date': '2026-10-30', 'source': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'input_hit_per_million': '0.006', 'input_miss_per_million': '0.30',
    'output_per_million': '1.20', 'reservation_micro_usd': 330000,
    'supplier_hard_limit': False}
_OWNER_FILES = ('src/macr/evals/deepseek_tool_name_probe.py',
                'scripts/run_deepseek_tool_name_probe.py')


def _code(project_root):
    return implementation(project_root) | {name: sha256(_read(
        project_root, name, 262144)).hexdigest() for name in _OWNER_FILES}


def _cases(root):
    raw = _read(root, 'cases.json', 4096)
    require(len(raw) == 1170 and sha256(raw).hexdigest() == CASES_SHA256, 'probe_cases_drift')
    cases = read_json(root, 'cases.json', 4096)
    require(type(cases) is list and tuple(row.get('id') for row in cases) == CASE_IDS,
            'probe_cases_invalid')
    return cases


def _policy_task(case):
    return {key: case[key] for key in ('id', 'question', 'scenario', 'assumptions')} | {
        'snapshot_root': 'snapshots/developer', 'authorized_files': sorted(SOURCES),
        'initial_evidence_files': ['entry.py'], 'stratum': 'retrieval_required',
        'evidence_parity': 'unequal_by_design', 'entrypoint': {'file': 'entry.py', 'symbol': 'run'}}


def _raw_write(path, raw):
    with open_directory(path.parent) as descriptor:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=descriptor)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def _prompt_version(variant):
    require(type(variant) is str and variant in PROMPT_VARIANTS, 'probe_prompt_variant_invalid')
    return _PROMPT_VERSIONS[variant]


def _payload(root, project_root, prompt_variant='baseline'):
    prompt_version = _prompt_version(prompt_variant)
    data = _root(root / 'data')
    _inventory(data, {'cases.json'} | {'snapshots/developer/' + name for name in SOURCES})
    cases = _cases(data)
    generation = get_generation(EVIDENCE_PROFILE)
    budget = DEFAULT_BUDGET | {'max_total_output_tokens': generation['max_total_output_tokens']}
    cells = []
    for case in cases:
        manifest = load_policy_manifest(root / 'manifests' / case['id'], dataset_root=data)
        require(manifest['task'] == _policy_task(case) and manifest['model'] == MODEL
            and manifest['budget'] == budget and manifest['repeat'] == 1
            and manifest['prompt_version'] == prompt_version, 'probe_manifest_drift')
        for name, (size, expected) in SOURCES.items():
            require(manifest['snapshot']['files'][name] == {'size_bytes': size, 'sha256': expected},
                    'probe_source_drift')
        cells.append({'case_id': case['id'], 'manifest_sha256': manifest['manifest_sha256']})
    value = {'schema': SCHEMA, 'control_root': str(root), 'data_root': str(data),
        'project_root': str(project_root), 'implementation': _code(project_root),
        'cases_sha256': CASES_SHA256, 'source_bytes': 91, 'cases_bytes': 1170,
        'execution_profile': EVIDENCE_PROFILE, 'model': dict(MODEL), 'endpoint': ENDPOINT,
        'budget': dict(BUDGET), 'price_snapshot': dict(PRICE), 'planned_cells': 2,
        'cells': cells, 'remote_enabled': False, 'stop_on_first_failure': True,
        'purpose': 'author-known developer protocol only; no semantic quality or historical-name recovery'}
    if prompt_variant != 'baseline':
        value['prompt_variant'] = prompt_variant
    return value


def prepare_probe(inputs, *, out, project_root, prompt_variant='baseline'):
    """Copy only three exact public developer inputs; no gold or credential input."""
    prompt_version = _prompt_version(prompt_variant)
    inputs, project_root = _root(inputs), _root(project_root)
    _cases(inputs)
    raw_sources = {name: _read(inputs, 'sources/' + name, 1024) for name in SOURCES}
    require(all(len(raw_sources[name]) == size and sha256(raw_sources[name]).hexdigest() == expected
        for name, (size, expected) in SOURCES.items()), 'probe_source_drift')
    _code(project_root)
    root = Path(out).absolute()
    validate_workspace_path(root, output=True)
    require(not root.exists() and not root.is_relative_to(inputs), 'probe_output_invalid')
    root.mkdir(mode=0o700)
    for folder in ('data', 'data/snapshots', 'data/snapshots/developer', 'manifests'):
        (root / folder).mkdir(mode=0o700)
    _raw_write(root / 'data/cases.json', _read(inputs, 'cases.json', 4096))
    for name, raw in raw_sources.items():
        _raw_write(root / 'data/snapshots/developer' / name, raw)
    generation = get_generation(EVIDENCE_PROFILE)
    for case in _cases(root / 'data'):
        prepare_policy_manifest(_policy_task(case), dataset_root=root / 'data',
            out=root / 'manifests' / case['id'], model=dict(MODEL),
            prompt_version=prompt_version,
            budget=DEFAULT_BUDGET | {'max_total_output_tokens': generation['max_total_output_tokens']})
    plan = _payload(root, project_root, prompt_variant)
    plan['plan_sha256'] = digest(plan)
    _write_exclusive(root / 'probe.json', plan, 65536)
    return plan


def load_probe(control):
    root = _root(control)
    value = read_json(root, 'probe.json', 65536)
    require(type(value) is dict and value.get('control_root') == str(root), 'probe_path_mismatch')
    require(type(value.get('project_root')) is str, 'probe_plan_invalid')
    require(value.get('plan_sha256') == digest({k: v for k, v in value.items() if k != 'plan_sha256'}),
            'probe_plan_invalid')
    expected = _payload(root, _root(value['project_root']), value.get('prompt_variant', 'baseline'))
    require(value == expected | {'plan_sha256': digest(expected)}, 'probe_plan_drift')
    return value


def _credential_source(value):
    value = {'kind': 'terminal'} if value is None else value
    require(type(value) is dict and value.get('kind') in {'terminal', 'project_env'},
            'probe_credential_source_invalid')
    if value['kind'] == 'terminal':
        require(value == {'kind': 'terminal'}, 'probe_credential_source_invalid')
    else:
        require(set(value) == {'kind', 'path'} and type(value['path']) is str
            and 0 < len(value['path']) <= 1024 and '\x00' not in value['path'],
            'probe_credential_source_invalid')
        path = Path(value['path'])
        require(path.is_absolute() and path.name == '.env' and
            path.parent == _root(path.parent), 'probe_credential_source_invalid')
    return dict(value)


def propose_probe_approval(control, *, credential_source=None):
    plan = load_probe(control)
    require('prompt_variant' not in plan, 'probe_prompt_variant_live_disabled')
    return {'schema': 'macr.deepseek.tool-name.approval.v1', 'authorized': False,
        'confirmation_ref': '', 'plan_sha256': plan['plan_sha256'],
        'control_root': plan['control_root'], 'cases_sha256': CASES_SHA256,
        'cell_ids': list(CASE_IDS), 'model': dict(MODEL), 'endpoint': ENDPOINT,
        'max_requests': 6, 'max_usd': '0.500000', 'price_snapshot': dict(PRICE),
        'credential_source': _credential_source(credential_source),
        'credential_use': 'official DeepSeek authentication only, memory-only',
        'log_root': plan['control_root'], 'log_policy': dict(LOG_POLICY),
        'concurrent_requests': 1, 'automatic_retries': 0, 'resume': False, 'ledger_reset': False}


def _preflight(control, approval, *, simulated=False):
    plan = load_probe(control)
    require('prompt_variant' not in plan, 'probe_prompt_variant_live_disabled')
    require(type(approval) is dict and approval.get('authorized') is True and
        type(approval.get('confirmation_ref')) is str and
        0 < len(approval['confirmation_ref'].strip()) <= 128, 'probe_approval_missing')
    expected = propose_probe_approval(control, credential_source=approval.get('credential_source'))
    require(approval | {'authorized': False, 'confirmation_ref': ''} == expected,
            'probe_approval_scope_mismatch')
    root = Path(plan['control_root'])
    with open_directory(root) as descriptor:
        require(stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o700, 'probe_log_root_not_private')
    require(simulated or not root.is_relative_to(Path(plan['project_root'])), 'probe_live_log_inside_checkout')
    require(not any((root / name).exists() for name in
        ('execution.claim.json', 'retention.json', 'cells', 'ledger', 'summary.json')),
        'probe_execution_already_claimed')
    return plan


def preflight_probe(control, approval):
    return _preflight(control, approval)


def _observation(result, expected):
    requests = result.get('requests', [])
    response = requests[-1].get('response') if requests else None
    diagnostic = response.get('failure_diagnostic') if type(response) is dict else None
    diagnostic = diagnostic if type(diagnostic) is dict else {}
    reason = diagnostic.get('tool_name_reason')
    terminals = [event for event in result.get('tool_events', []) if event.get('terminal')]
    observed = terminals[-1]['name'] if terminals else None
    classification = ('valid_' + observed if observed in {'finish', 'ask_user'}
                      else result.get('error_code') or 'unobserved_terminal')
    if reason in {'type', 'format'}:
        classification = 'tool_name_' + reason
    elif reason == 'not_declared':
        classification = ('terminal_round_read_attempt' if diagnostic.get('policy_decision') == 3
            and diagnostic.get('declared_tool_set') == 'terminal_only'
            and diagnostic.get('tool_name_family') == 'static_read' else 'undeclared_tool_name')
    events = result.get('tool_events', [])
    flow = (len(events) == 3 and events[0].get('decision') == 1 and events[0].get('name') == 'search'
        and events[0].get('arguments') == {'query': 'double'}
        and events[1].get('decision') == 2 and events[1].get('name') == 'read_symbol'
        and events[1].get('arguments') == {'file': 'helper.py', 'symbol': 'double'}
        and len(terminals) == 1 and terminals[0].get('decision') == 3 and observed == expected)
    return {'classification': classification, 'third_decision_reached': len(requests) == 3,
        'expected_terminal': expected, 'observed_terminal': observed, 'flow_followed': bool(flow)}


def run_probe_dryrun(control, *, scripted_by_cell):
    plan = load_probe(control)
    return _execute(plan, scripted_by_cell=scripted_by_cell, key_reader=None,
                    provenance='scripted_offline_fixture')


def execute_probe(control, approval, *, enabled=False, key_reader=None, scripted_by_cell=None):
    require(enabled is True, 'probe_live_disabled')
    simulated = scripted_by_cell is not None
    plan = _preflight(control, approval, simulated=simulated)
    require(simulated or callable(key_reader), 'probe_key_reader_invalid')
    return _execute(plan, scripted_by_cell=scripted_by_cell, key_reader=key_reader,
        provenance='approved_live_fake' if simulated else 'approved_live')


def _execute(plan, *, scripted_by_cell, key_reader, provenance):
    simulated = provenance != 'approved_live'
    scripts = {}
    if simulated:
        require(type(scripted_by_cell) is dict and set(scripted_by_cell) == set(CASE_IDS),
                'probe_script_scope_invalid')
        scripts = {key: _ScriptedWire(value) for key, value in scripted_by_cell.items()}
    root = Path(plan['control_root'])
    try:
        _write_exclusive(root / 'execution.claim.json', {'plan_sha256': plan['plan_sha256'],
            'provenance': provenance, 'state': 'claimed_before_credentials_or_dispatch'}, 4096)
    except FileExistsError:
        raise StudyError('probe_execution_already_claimed') from None
    _write_exclusive(root / 'retention.json', {'plan_sha256': plan['plan_sha256'],
        'contents': ['source', 'requests', 'outputs', 'reasoning', 'usage', 'receipts'], **LOG_POLICY}, 4096)
    (root / 'cells').mkdir(mode=0o700)
    (root / 'ledger').mkdir(mode=0o700)
    gate = _StudyGate(root, {'execution_profile': EVIDENCE_PROFILE, 'budget': dict(BUDGET)}, provenance)
    key, transports, rows, halted, live_calls = None, [], [], False, 0
    try:
        for case in _cases(root / 'data'):
            case_id = case['id']
            if halted:
                rows.append({'case_id': case_id, 'status': 'not_attempted', 'error_code': None})
                continue
            gate.cell_id = case_id
            wire = None
            try:
                if not simulated and key is None:
                    try:
                        key = key_reader()
                    except Exception:
                        raise StudyError('probe_key_input_failed') from None
                    require(type(key) is str and 1 <= len(key) <= 4096 and key == key.strip()
                        and all(32 < ord(char) < 127 for char in key), 'probe_key_invalid')
                inner = scripts[case_id] if simulated else _ApprovedTransport(key)
                transports.append(inner)
                wire = _BoundWire(inner, simulated=simulated)
                gate.wire = wire
                result = _run_study_cell_core(root / 'manifests' / case_id,
                    dataset_root=root / 'data', strategy='adaptive_read_3',
                    provider=DeepSeekProvider(model=MODEL['model_id'], transport=wire, allow_remote=False),
                    out=root / 'cells' / case_id, fixed_plan={}, admit=gate.admit,
                    settle=gate.settle, provenance=provenance)
            except (PolicyRunError, PolicyManifestError, StudyError, OSError) as error:
                result = {'task_id': case_id, 'strategy': 'adaptive_read_3', 'status': 'failed',
                    'error_code': getattr(error, 'code', 'probe_io_error'), 'requests': [],
                    'review_status': 'not_ready', 'answer': None}
            if wire is not None:
                live_calls += wire.live_attempts
            observation = _observation(result, case['terminal'])
            result.update(case_id=case_id, observation=observation, accounting=gate.state(case_id))
            _write_exclusive(root / 'cells' / f'{case_id}.accounted.json', result, 1048576)
            rows.append({'case_id': case_id, 'status': result['status'],
                'error_code': result.get('error_code'), 'observation': observation})
            halted = (gate.halted or gate.pending is not None or result['status'] not in
                {'semantic_pending', 'ask_user_terminated'} or not observation['flow_followed'])
        summary = {'schema': SCHEMA, 'plan_sha256': plan['plan_sha256'], 'planned_cells': 2,
            'rows': rows, 'accounting': gate.state() | {'model_calls_live': live_calls},
            'model_calls_live': live_calls, 'provenance': provenance, 'batch_halted': bool(halted),
            'interpretation': 'developer protocol observations; no semantic success or historical-name inference'}
        _write_exclusive(root / 'summary.json', summary, 65536)
        return summary
    finally:
        key = None
        for inner in transports:
            if type(inner) is _ApprovedTransport:
                inner._api_key = ''
