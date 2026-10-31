"""One-shot, default-off static diagnostic stage with frozen real preparation.

Local approval records and physical-path bindings prevent accidental reuse; they
are not OS isolation or a cryptographic signature from the operator.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import os
from pathlib import Path
import random
import re
import stat
from time import monotonic

from macr.evals.deepseek_policy_manifest import _read, _root, PolicyManifestError
from macr.evals.deepseek_policy_runner import PolicyRunError, _response_record, _write_exclusive
from macr.evals.deepseek_policy_spend import _parse_usd, _usd
from macr.evals.deepseek_study_generation import VALUE_PROFILE
from macr.evals.deepseek_study_live import (
    ENDPOINT, LOG_POLICY, MODEL, _BoundWire, _ScriptedWire, _StudyGate)
from macr.evals.deepseek_study_profile import (
    StudyError, _custodian, digest, implementation, read_json, require)
from macr.evals.deepseek_tool_name_probe import _credential_source
from macr.evals.diagnostic_compact import (
    VERSION, candidate_quality_warnings, parse_candidate, restore_scope)
from macr.evals.static_diagnostic_pack import load_pack
from macr.investigation.diagnostic_packet import PROFILE, prepare_pair
from macr.investigation.index import RepositoryIndex
from macr.investigation.records import ScopeProfile
from macr.investigation.scope import RepositoryScope, open_directory, validate_workspace_path
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.source_text import SourceText
from macr.investigation.validation import ContractError
from macr.providers.base import ConversationRequest, ModelBudget, ProviderError
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport

SCHEMA = 'macr.static-diagnostic.stage.v1'
_BUDGETS = {'S0': {'max_requests': 6, 'max_usd': '0.500000'},
            'S1': {'max_requests': 18, 'max_usd': '1.000000'}}
_TOTAL = {'max_requests': 24, 'max_usd': '1.500000'}
_LIMITS = {'max_input_tokens': 8000, 'max_output_tokens': 8192, 'timeout_seconds': 60.0,
           'max_wire_bytes': 65536, 'max_answer_bytes': 8192}
_CODE_FILES = (
    'src/macr/evals/static_diagnostic_stage.py', 'src/macr/evals/static_diagnostic_pack.py',
    'src/macr/evals/static_diagnostic_report.py', 'scripts/run_static_diagnostic_stage.py',
    'src/macr/evals/diagnostic_compact.py', 'src/macr/evals/diagnostic_verification.py',
    'src/macr/investigation/diagnostic_packet.py', 'src/macr/investigation/static_facts.py',
    'src/macr/investigation/context.py', 'src/macr/investigation/source_text.py',
    'src/macr/evals/deepseek_tool_name_probe.py',
    'scripts/run_deepseek_value_stage.py', 'scripts/run_deepseek_tool_name_probe.py')
PRICE = {'source': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'checked_date': '2026-10-31', 'status': 'planning_snapshot_recheck_before_paid_activation',
    'input_miss_per_million': '0.30', 'input_hit_per_million': '0.006',
    'output_per_million': '1.20', 'reservation_micro_usd': 330000,
    'context_input_envelope': 1048576, 'supplier_hard_limit': False}


def _code(project):
    return implementation(project) | {name: sha256(_read(project, name, 262144)).hexdigest()
        for name in _CODE_FILES}


def _mkdir(path):
    with open_directory(Path(path).parent) as parent:
        os.mkdir(Path(path).name, 0o700, dir_fd=parent)


def _raw_write(path, data):
    with open_directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.fsync(parent)


def _request(prepared, arm):
    return ConversationRequest(**prepared['requests'][arm],
        thinking={'type': 'enabled'}, reasoning_effort='high')


def _wire(prepared, arm):
    request = _request(prepared, arm)
    require(request.max_input_tokens == 8000 and request.max_output_tokens == 8192
        and request.timeout_seconds == 60 and request.tools == [], 'static_stage_request_contract_drift')
    body = DeepSeekProvider(model=MODEL['model_id'], allow_remote=False).encode_conversation_request(request)
    require(len(body) <= 65536, 'static_stage_wire_limit')
    return request, sha256(body).hexdigest(), len(body)


def _binding(root, pack, frozen):
    names = ['pack.json'] + [task['snapshot_root'] + '/' + name
        for task in pack['tasks'] for name in task['authorized_files']]
    return {'original_root': str(root), 'frozen_root': frozen, 'pack_sha256': digest(pack),
        'files': {name: {'sha256': sha256(raw).hexdigest(), 'size_bytes': len(raw)}
            for name in sorted(names) for raw in [_read(root, name, 65536)]}}


def _copy_pack(root, pack, destination):
    _mkdir(destination)
    binding = _binding(root, pack, '')
    for name in binding['files']:
        folder = destination / Path(name).parent
        with open_directory(folder, create=True):
            pass
        _raw_write(destination / name, _read(root, name, 65536))


def _cells(stage_id, phase, tasks, root, seed):
    ids = sorted(task['id'] for task in tasks)
    random.Random(seed + (1 if phase == 'S1' else 0)).shuffle(ids)
    by_id = {task['id']: task for task in tasks}
    cells = []
    for index, task_id in enumerate(ids):
        arms = ('direct', 'evidence') if index % 2 == 0 else ('evidence', 'direct')
        task = by_id[task_id]
        prepared = read_json(root, task['prepared_path'], 262144)
        for arm in arms:
            _, request_hash, size = _wire(prepared, arm)
            cells.append({'cell_id': digest([stage_id, phase, task_id, arm])[:32],
                'task_id': task_id, 'project_id': task['project_id'], 'phase': phase, 'arm': arm,
                'prepared_path': task['prepared_path'], 'request_sha256': request_hash,
                'wire_bytes': size, 'order_index': len(cells)})
    return cells


def prepare_stage(development_root, qualification_root, *, custodian, out,
                  project_root, stage_id, seed=20261031):
    """Freeze source-only data and actual computed capsules, without credentials."""
    require(type(stage_id) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', stage_id),
            'static_stage_id_invalid')
    require(type(seed) is int and 0 <= seed < 2**32, 'static_stage_seed_invalid')
    roots = {'S0': _root(development_root), 'S1': _root(qualification_root)}
    project = _root(project_root)
    code = _code(project)
    commitment = _custodian(custodian)
    packs = {phase: load_pack(path, development=phase == 'S0') for phase, path in roots.items()}
    require(roots['S0'] != roots['S1'] and not (roots['S0'].is_relative_to(roots['S1'])
        or roots['S1'].is_relative_to(roots['S0'])), 'static_stage_pack_roots_overlap')
    root = Path(out).absolute()
    validate_workspace_path(root, output=True)
    require(_root(root.parent) == root.parent and root == root.resolve() and not root.exists()
        and all(not root.is_relative_to(path) and not path.is_relative_to(root)
                for path in roots.values()), 'static_stage_output_invalid')
    _mkdir(root); _mkdir(root / 'inputs')
    phases, bindings = {}, {}
    budget = ModelBudget(8000, 8192, 60.0)
    for phase in ('S0', 'S1'):
        phase_root = root / 'inputs' / phase; _mkdir(phase_root)
        data = phase_root / 'data'
        _copy_pack(roots[phase], packs[phase], data)
        relative_data = data.relative_to(root).as_posix()
        bindings[phase] = _binding(roots[phase], packs[phase], relative_data)
        tasks = []
        for task in packs[phase]['tasks']:
            task_root = phase_root / task['id']; _mkdir(task_root)
            repo = data / task['snapshot_root']
            capture_started = monotonic()
            scope = RepositoryScope().inventory(repo, ScopeProfile(max_python_files=4,
                max_text_bytes=4096, max_file_bytes=4096))
            require({row.path for row in scope.eligible} == set(task['authorized_files'])
                and not scope.errors, 'static_stage_capture_scope_invalid')
            store = SnapshotStore(task_root / 'snapshots', allowed_root=task_root)
            bundle = store.capture(repo, scope)
            capture_seconds = monotonic() - capture_started
            index = RepositoryIndex(store, task_root / 'index', allowed_root=task_root)
            prepared = prepare_pair(bundle, index, entries=task['entries'], question=task['question'],
                scenario=task['scenario'], assumptions=task['assumptions'], budget=budget)
            require(not prepared['packet']['selection_gaps'], 'static_stage_entry_not_resolved')
            path = task_root / 'prepared.json'
            _write_exclusive(path, prepared, 262144)
            timing = prepared['timing_seconds']
            tasks.append(task | {'prepared_path': path.relative_to(root).as_posix(),
                'prepared_sha256': sha256(_read(root, path.relative_to(root).as_posix(), 262144)).hexdigest(),
                'preparation': {'capture_common_seconds': capture_seconds,
                    'common_seconds': capture_seconds + timing['source_scope'],
                    'evidence_seconds': timing['index'] + timing['collect'] + timing['normalize'],
                    'packet_bytes': len(__import__('json').dumps(prepared['packet'], ensure_ascii=False,
                        sort_keys=True, separators=(',', ':')).encode('utf-8')),
                    'operations': prepared['operations']}})
        phases[phase] = {'budget': dict(_BUDGETS[phase]), 'tasks': tasks,
            'cells': _cells(stage_id, phase, tasks, root, seed)}
    plan = {'schema': SCHEMA, 'profile': PROFILE, 'stage_id': stage_id,
        'control_root': str(root), 'project_root': str(project), 'model': dict(MODEL),
        'endpoint': ENDPOINT, 'budget': dict(_TOTAL), 'seed': seed, 'limits': dict(_LIMITS),
        'implementation': code, 'sourcebindings': bindings, 'custodian': commitment,
        'phases': phases, 'price_snapshot': dict(PRICE), 'log_policy': dict(LOG_POLICY),
        'remote_enabled': False, 'automatic_retries': 0, 'resume': False,
        'ledger_reset': False, 'planned_cells': 24, 'planned_request_ceiling': 24}
    plan['plan_sha256'] = digest(plan)
    _write_exclusive(root / 'stage.json', plan, 262144)
    return load_stage(root)


def load_stage(control):
    """Read only; revalidate original and copied inputs, implementation and wires."""
    root = _root(control)
    require(root == root.resolve(), 'static_stage_path_mismatch')
    plan = read_json(root, 'stage.json', 262144)
    require(type(plan) is dict and plan.get('schema') == SCHEMA
        and plan.get('control_root') == str(root), 'static_stage_path_mismatch')
    expected_hash = plan.get('plan_sha256')
    require(expected_hash == digest({k: v for k, v in plan.items() if k != 'plan_sha256'}),
            'static_stage_plan_drift')
    require(plan['profile'] == PROFILE and plan['model'] == MODEL and plan['endpoint'] == ENDPOINT
        and plan['limits'] == _LIMITS and plan['budget'] == _TOTAL and plan['remote_enabled'] is False,
        'static_stage_contract_drift')
    require(_code(_root(plan['project_root'])) == plan['implementation'], 'static_stage_code_drift')
    _custodian(plan['custodian'])
    require(set(plan['phases']) == {'S0', 'S1'} and set(plan['sourcebindings']) == {'S0', 'S1'},
            'static_stage_phase_invalid')
    for phase in ('S0', 'S1'):
        binding, phase_plan = plan['sourcebindings'][phase], plan['phases'][phase]
        original = _root(binding['original_root'])
        original_pack = load_pack(original, development=phase == 'S0')
        require(_binding(original, original_pack, binding['frozen_root']) == binding,
            'static_stage_source_drift')
        copied = _root(root / binding['frozen_root'])
        require(copied.is_relative_to(root) and load_pack(copied, development=phase == 'S0') == original_pack,
                'static_stage_source_drift')
        for name, frozen in binding['files'].items():
            raw = _read(copied, name, 65536)
            require({'size_bytes': len(raw), 'sha256': sha256(raw).hexdigest()} == frozen,
                'static_stage_source_drift')
        require(phase_plan['budget'] == _BUDGETS[phase], 'static_stage_budget_drift')
        require(len(phase_plan['tasks']) == len(original_pack['tasks']), 'static_stage_task_drift')
        for task, raw_task in zip(phase_plan['tasks'], original_pack['tasks']):
            require({key: task[key] for key in raw_task} == raw_task, 'static_stage_task_drift')
            path = task['prepared_path']
            require(path == f'inputs/{phase}/{task["id"]}/prepared.json', 'static_stage_prepared_path_invalid')
            raw = _read(root, path, 262144)
            require(sha256(raw).hexdigest() == task['prepared_sha256'], 'static_stage_prepared_drift')
            prepared = read_json(root, path, 262144)
            require(prepared['profile'] == PROFILE and not prepared['packet']['selection_gaps']
                and digest(prepared['packet']) == prepared['packet_sha256'], 'static_stage_prepared_drift')
            source = {row['file']: row['body'] for row in prepared['scope']['source']}
            require(set(source) == set(task['authorized_files']) and all(
                source[name] == SourceText.from_bytes(
                    _read(copied, task['snapshot_root'] + '/' + name, 4096), name).text
                for name in source), 'static_stage_prepared_source_drift')
            restore_scope(prepared['scope'])
        require(_cells(plan['stage_id'], phase, phase_plan['tasks'], root, plan['seed']) == phase_plan['cells'],
                'static_stage_cell_drift')
    return plan


def propose_approval(control, *, credential_source=None):
    plan = load_stage(control)
    return {'schema': 'macr.static-diagnostic.approval.v1', 'authorized': False, 'confirmation_ref': '',
        'plan_sha256': plan['plan_sha256'], 'control_root': plan['control_root'], 'profile': PROFILE,
        'model': dict(MODEL), 'endpoint': ENDPOINT, 'budget': dict(_TOTAL),
        'phases': {phase: {'budget': dict(plan['phases'][phase]['budget']),
            'cell_ids': [c['cell_id'] for c in plan['phases'][phase]['cells']]}
            for phase in ('S0', 'S1')},
        'price_snapshot': dict(PRICE), 'credential_source': _credential_source(credential_source),
        'credential_use': 'official DeepSeek authentication only, memory-only',
        'log_root': plan['control_root'], 'log_policy': dict(LOG_POLICY),
        'concurrent_requests': 1, 'automatic_retries': 0, 'resume': False, 'ledger_reset': False,
        's1_requires_s0_delivery_qualification': True}


def preflight_stage(control, approval):
    plan = load_stage(control)
    require(type(approval) is dict and approval.get('authorized') is True
        and type(approval.get('confirmation_ref')) is str
        and 0 < len(approval['confirmation_ref'].strip()) <= 128, 'static_stage_approval_missing')
    proposal = propose_approval(control, credential_source=approval.get('credential_source'))
    require(approval | {'authorized': False, 'confirmation_ref': ''} == proposal,
        'static_stage_approval_scope_mismatch')
    return plan


def _scripts(plan, scripted_by_cell):
    ids = {cell['cell_id'] for phase in plan['phases'].values() for cell in phase['cells']}
    require(type(scripted_by_cell) is dict and set(scripted_by_cell) == ids,
        'static_stage_script_invalid')
    return {key: _ScriptedWire((reply,)) for key, reply in scripted_by_cell.items()}


def run_dryrun(control, *, scripted_by_cell):
    plan = load_stage(control)
    scripts = _scripts(plan, scripted_by_cell)
    return _run(plan, scripts=scripts, provenance='scripted_offline_fixture')


def execute_stage(control, approval, *, enabled=False, key_reader=None, scripted_by_cell=None):
    """Explicit exact approval, then exclusive claim, then memory-only authentication."""
    require(enabled is True, 'static_stage_live_disabled')
    plan = preflight_stage(control, approval)
    simulated = scripted_by_cell is not None
    scripts = _scripts(plan, scripted_by_cell) if simulated else None
    if not simulated:
        require(callable(key_reader), 'static_stage_key_reader_invalid')
        root, project = Path(plan['control_root']), Path(plan['project_root'])
        require(not root.is_relative_to(project) and not project.is_relative_to(root),
            'static_stage_live_log_root_invalid')
        with open_directory(root) as descriptor:
            require(stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o700,
                'static_stage_live_log_root_not_private')
    return _run(plan, scripts=scripts,
        provenance='approved_live_fake' if simulated else 'approved_live', key_reader=key_reader,
        confirmation_ref=approval['confirmation_ref'])


def _base_result(plan, task, cell, prepared, provenance):
    request, _, _ = _wire(prepared, cell['arm'])
    prep = task['preparation'] | {'wire_bytes': cell['wire_bytes']}
    return cell | {'schema': 'macr.static-diagnostic.cell.v1', 'profile': PROFILE,
        'provenance': provenance, 'status': 'not_attempted', 'error_code': None,
        'review_status': 'not_ready', 'answer': None, 'quality_warnings': [],
        'question': task['question'], 'scenario': task['scenario'], 'assumptions': task['assumptions'],
        'source': prepared['scope']['source'], 'scope': prepared['scope'],
        'evidence_packet': prepared['packet'] if cell['arm'] == 'evidence' else None,
        'packet_sha256': prepared['packet_sha256'] if cell['arm'] == 'evidence' else None,
        'initial_source_sha256': prepared['initial_source_sha256'], 'request': asdict(request),
        'response': _response_record(None), 'accounting': None, 'model_calls_live': 0,
        'fixture_model_calls': 0, 'elapsed_seconds': 0.0, 'preparation': prep,
        'standalone_elapsed_seconds': prep['common_seconds'] + (
            prep['evidence_seconds'] if cell['arm'] == 'evidence' else 0.0)}


def _attempt(plan, task, cell, prepared, gate, wire, provenance):
    row = _base_result(plan, task, cell, prepared, provenance)
    started = monotonic()
    gate.cell_id, gate.wire = cell['cell_id'], wire
    response, failure = None, None
    try:
        # Revalidate every binding before reserving/dispatching, without re-preparing.
        load_stage(plan['control_root'])
        request, body_hash, size = _wire(prepared, cell['arm'])
        require(body_hash == cell['request_sha256'] and size == cell['wire_bytes'],
            'static_stage_wire_drift')
        gate.admit(body_hash)
        try:
            response = DeepSeekProvider(model=MODEL['model_id'], transport=wire,
                allow_remote=False).converse(request)
        except ProviderError as error:
            response, failure = error.response, error
        finally:
            # The bound wire consumes its hash only when POST is reached. Capture
            # that event before settlement clears the binding on every outcome.
            row['model_calls_live'] = wire.live_attempts
            row['fixture_model_calls'] = int(wire.simulated and wire.expected_hash is None)
        row['response'] = _response_record(response)
        if failure is not None:
            for name in ('diagnostic', 'billing_evidence', 'identity_evidence'):
                value = getattr(failure, name)
                if value is not None:
                    row['response'][name] = value
        # Billing is independent of content validity, and must survive rejection.
        gate.settle(body_hash, response=response if failure is None else None, error=failure)
        usage = gate.rows[-1]['usage']
        require(usage['prompt_tokens'] <= 8000, 'static_stage_measured_input_overrun')
        if failure is not None:
            # A trustworthy settled non-transport response is a delivery failure.
            if failure.code in {'model_response_invalid', 'model_output_incomplete'}:
                row.update(status='contract_failed', error_code=failure.code)
            else:
                row.update(status='failed', error_code=failure.code)
        else:
            try:
                require(not response.tool_calls and response.finish_reason == 'stop',
                    'static_stage_unexpected_model_action')
                answer = parse_candidate(response.content, restore_scope(prepared['scope']), version=VERSION)
                row.update(status='semantic_pending', review_status='pending', answer=answer,
                    quality_warnings=candidate_quality_warnings(answer))
            except (ProviderError, ContractError, StudyError) as error:
                row.update(status='contract_failed', error_code=error.code)
    except (StudyError, PolicyRunError, PolicyManifestError, ContractError, ProviderError) as error:
        row.update(status='unknown' if gate.halted or gate.pending else 'failed', error_code=error.code)
    except KeyboardInterrupt:
        row.update(status='unknown' if gate.pending else 'failed', error_code='static_stage_interrupted')
    except Exception:
        # Do not expose arbitrary provider/local exception bodies or lose claim/reserve.
        row.update(status='unknown' if gate.pending else 'failed', error_code='static_stage_infrastructure_failure')
    row['accounting'] = gate.state(cell['cell_id']) | {'model_calls_live': row['model_calls_live']}
    row['elapsed_seconds'] = monotonic() - started
    row['standalone_elapsed_seconds'] += row['elapsed_seconds']
    return row


def _run(plan, *, scripts, provenance, key_reader=None, confirmation_ref=''):
    root = Path(plan['control_root'])
    # Cache only frozen bytes, so later drift cannot erase the untouched rows.
    prepared_by_path = {task['prepared_path']: read_json(root, task['prepared_path'], 262144)
        for phase in plan['phases'].values() for task in phase['tasks']}
    try:
        _write_exclusive(root / 'execution.claim.json', {'schema': 'macr.static-diagnostic.claim.v1',
            'plan_sha256': plan['plan_sha256'], 'control_root': str(root),
            'provenance': provenance, 'state': 'claimed_before_credentials_or_dispatch'}, 4096)
    except FileExistsError:
        raise StudyError('static_stage_execution_already_claimed') from None
    _mkdir(root / 'cells'); _mkdir(root / 'phases')
    _write_exclusive(root / 'retention.json', {'schema': 'macr.static-diagnostic.log.v1',
        'plan_sha256': plan['plan_sha256'], 'confirmation_ref': confirmation_ref,
        'contents': ['source', 'model_output', 'reasoning_content', 'usage', 'receipts'], **LOG_POLICY}, 4096)
    gates, results, rows, transports = {}, {}, [], []
    key, halted, qualified, write_failure = None, False, False, None
    live_calls, fixture_calls = 0, 0
    try:
        for phase in ('S0', 'S1'):
            phase_plan = plan['phases'][phase]
            folder = root / 'phases' / phase; _mkdir(folder); _mkdir(folder / 'ledger')
            gate = _StudyGate(folder, {'execution_profile': VALUE_PROFILE,
                'budget': dict(phase_plan['budget'])}, provenance)
            gates[phase] = gate
            if phase == 'S1':
                gate.fingerprint = gates['S0'].fingerprint
            failures = 0
            tasks = {task['id']: task for task in phase_plan['tasks']}
            for cell in phase_plan['cells']:
                task = tasks[cell['task_id']]
                prepared = prepared_by_path[task['prepared_path']]
                row = _base_result(plan, task, cell, prepared, provenance)
                if not halted and (phase == 'S0' or qualified):
                    try:
                        if scripts is not None:
                            inner = scripts[cell['cell_id']]
                        else:
                            if key is None:
                                # No callback, including a fake callback, is invoked in dry runs.
                                try:
                                    key = key_reader()
                                except Exception:
                                    raise StudyError('static_stage_key_input_failed') from None
                                require(type(key) is str and 1 <= len(key) <= 4096 and key == key.strip()
                                    and all(32 < ord(char) < 127 for char in key), 'static_stage_key_invalid')
                            inner = _ApprovedTransport(key); transports.append(inner)
                        wire = _BoundWire(inner, simulated=scripts is not None)
                        row = _attempt(plan, task, cell, prepared, gate, wire, provenance)
                    except (StudyError, PolicyRunError, ContractError) as error:
                        row.update(status='failed', error_code=error.code)
                    except KeyboardInterrupt:
                        row.update(status='failed', error_code='static_stage_interrupted')
                    except OSError:
                        row.update(status='failed', error_code='static_stage_infrastructure_failure')
                    live_calls += row['model_calls_live']; fixture_calls += row['fixture_model_calls']
                    if row['status'] != 'semantic_pending':
                        failures += 1
                        halted = (phase == 'S0' or row['status'] != 'contract_failed' or failures >= 2)
                row['accounting'] = gate.state(cell['cell_id']) | {'model_calls_live': row['model_calls_live']}
                results[cell['cell_id']] = row
                try:
                    _write_exclusive(root / 'cells' / (cell['cell_id'] + '.json'), row, 1048576)
                except (OSError, PolicyRunError) as error:
                    # Keep claim/ledger and try a full-denominator summary. Missing
                    # original artifacts are reported as an infrastructure failure.
                    write_failure = error
                    halted = True
                rows.append(cell | {'status': row['status'], 'error_code': row['error_code'],
                    'result_sha256': digest(row)})
            if phase == 'S0':
                qualified = (not halted and len(gate.rows) == 6 and gate.fingerprint is not None
                    and all(r['state'] == 'settled' for r in gate.rows)
                    and all(results[c['cell_id']]['status'] == 'semantic_pending' for c in phase_plan['cells']))
                halted = not qualified
        accounting = {phase: gate.state() for phase, gate in gates.items()}
        summary = {'schema': 'macr.static-diagnostic.execution.v1', 'profile': PROFILE,
            'plan_sha256': plan['plan_sha256'], 'provenance': provenance, 'planned_cells': 24,
            's0_qualified': qualified, 'stage_halted': halted, 'rows': rows,
            'phases': {phase: {'accounting': accounting[phase], 'planned_cells': len(plan['phases'][phase]['cells']),
                'processed_cells': len(gates[phase].rows), 'fingerprint': gates[phase].fingerprint,
                'qualified': qualified if phase == 'S0' else None} for phase in ('S0', 'S1')},
            'actual_requests': sum(a['request_count'] for a in accounting.values()),
            'model_calls_live': live_calls, 'fixture_model_calls': fixture_calls,
            'known_accrued_usd': _usd(sum(_parse_usd(a['known_accrued_usd'], code='static_stage_accounting_invalid')
                for a in accounting.values())),
            'new_unknown_held_usd': _usd(sum(_parse_usd(a['unknown_held_usd'], code='static_stage_accounting_invalid')
                for a in accounting.values())), 'semantic_scoring': 'pending_independent_review'}
        _write_exclusive(root / 'summary.json', summary, 262144)
        if write_failure is not None:
            raise StudyError('static_stage_result_write_failed') from None
        from macr.evals.static_diagnostic_report import export_packets, build_report, sanitize_report
        exported = export_packets(plan, summary, results)
        _write_exclusive(root / 'anonymous-packets.json', {key: value for key, value in exported.items()
            if key != 'keeper_mapping'}, 1048576)
        _write_exclusive(root / 'keeper-mapping.json', exported['keeper_mapping'], 65536)
        _write_exclusive(root / 'report.json', sanitize_report(build_report(plan, summary, results)), 262144)
        return summary
    finally:
        key = None
        for transport in transports:
            transport._api_key = ''
