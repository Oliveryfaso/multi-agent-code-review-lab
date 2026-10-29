"""Offline-only, gold-free four-strategy adapter for the paired study.

Shares the provider, diagnosis contract, bounded static tools and receipt format
with the historical experiment. It cannot dispatch a remote transport.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic

from macr.evals.deepseek_policy_manifest import PROMPT_VERSION, load_policy_manifest
from macr.evals.deepseek_study_generation import V2_PROFILE, V2_PROMPT_VERSION, get_generation
from macr.evals.deepseek_policy_runner import (
    ASK_USER_GAP_SCHEMA, DIAGNOSIS_CRITERIA, DIRECT_INSTRUCTIONS, OUTPUT_SCHEMA,
    READ_TOOLS, TERMINAL_TOOLS, TOOL_INSTRUCTIONS, TOOL_SCHEMAS, PolicyRunError,
    _ReservationLedger, _append_source, _diagnosis, _initial_static_evidence,
    _json_bytes, _object, _response_record, _tool_call, _write_exclusive,
)
from macr.evals.deepseek_policy_tools import StaticEvidenceTools
from macr.investigation.scope import open_directory, validate_workspace_path
from macr.investigation.validation import ContractError
from macr.providers.base import ConversationRequest, ProviderError
from macr.providers.deepseek import DeepSeekProvider
from macr.providers.failure_evidence import validate_failure_diagnostic

SCHEMA = 'macr.deepseek.study.cell.v1'
STRATEGIES = ('direct_initial_3', 'fixed_read_3', 'adaptive_read_3', 'direct_full_3')
CHECK_INSTRUCTIONS = ('Check the preceding diagnosis against the allowed input domain, including '
    'counterexamples. Use only the supplied evidence. Return the diagnosis JSON again.')
FINAL_INSTRUCTIONS = ('Give the final diagnosis for the full allowed input domain using only the '
    'supplied evidence. Return the diagnosis JSON; do not select between answers after scoring.')
EXTERNAL_EVIDENCE_CRITERIA = (
    'Distinguish what the supplied code establishes from decisive facts about the current external situation. '
    'If compatible external states change the conclusion, do not select an unsupported current outcome. '
    'Name the missing decisive fact, the smallest evidence needed to distinguish the outcomes, and its causal role. '
    'Missing authorized source is not automatically a genuine external gap; describe that access limitation accurately. '
    'A possible failure scenario is not proof of the current outcome; avoid demands for unrelated data. '
    'Use existing unknowns or gap fields and citations within their schema limits; do not add fields or headings.')


def initial_evidence(manifest: dict, tools: StaticEvidenceTools) -> dict:
    source = tools.source(manifest['task']['initial_evidence_files'])
    task = manifest['task']
    return {'question': task['question'], 'scenario': task['scenario'],
        'assumptions': task['assumptions'], 'source': source,
        'static_evidence': _initial_static_evidence(source),
        'authorized_files': sorted(task['authorized_files'])}


def _fixed_plan(manifest: dict, dataset_root: Path, supplied: dict) -> dict:
    from macr.evals.deepseek_study_plan import build_fixed_plan
    planned = build_fixed_plan(manifest, dataset_root)
    if (type(supplied) is not dict or supplied.get('sha256') != planned['sha256']
            or {k:v for k,v in supplied.items() if k != 'elapsed_seconds'} !=
               {k:v for k,v in planned.items() if k != 'elapsed_seconds'}):
        raise PolicyRunError('fixed_plan_drift')
    return planned


def run_study_cell(manifest_dir: Path, *, dataset_root: Path, strategy: str,
                   provider: DeepSeekProvider, out: Path, fixed_plan: dict,
                   admit=None, settle=None) -> dict:
    """Execute one exclusive fixture cell; callbacks implement batch accounting.

    admit(exact_wire_sha256) runs before provider dispatch. settle(hash,
    response=response,error=None) or settle(hash,response=None,error=error) runs
    once after the provider returns, before parsing policy output. Unknown usage
    terminates this cell; the batch must retain its reservation and halt.
    """
    if (not isinstance(provider, DeepSeekProvider) or provider.allow_remote
            or provider.transport is None
            or getattr(provider.transport, 'offline_fixture', False) is not True):
        raise PolicyRunError('live_dispatch_disabled')
    return _run_study_cell_core(manifest_dir, dataset_root=dataset_root, strategy=strategy,
        provider=provider, out=out, fixed_plan=fixed_plan, admit=admit, settle=settle,
        provenance='scripted_offline_fixture')


def _run_study_cell_core(manifest_dir: Path, *, dataset_root: Path, strategy: str,
                        provider: DeepSeekProvider, out: Path, fixed_plan: dict,
                        admit=None, settle=None, provenance: str) -> dict:
    """Shared policy body; the separate explicit live entry owns authorization."""
    started = monotonic()
    if provenance not in {'scripted_offline_fixture', 'approved_live_fake', 'approved_live'}:
        raise PolicyRunError('study_provenance_invalid')
    if not isinstance(provider, DeepSeekProvider) or provider.transport is None:
        raise PolicyRunError('provider_invalid')
    if strategy not in STRATEGIES:
        raise PolicyRunError('study_strategy_invalid')
    manifest = load_policy_manifest(manifest_dir, dataset_root=dataset_root)
    generation = get_generation(V2_PROFILE if manifest['prompt_version']==V2_PROMPT_VERSION else None)
    if manifest['prompt_version'] != generation['prompt_version']:
        raise PolicyRunError('policy_prompt_version_invalid')
    if provider.model != manifest['model']['model_id']:
        raise PolicyRunError('model_identity_drift')
    if (manifest['model'].get('model_id') != 'deepseek-flash'
            or manifest['model'].get('provider') != 'deepseek'
            or manifest['model'].get('thinking') is not True
            or manifest['model'].get('reasoning_effort') != 'high'
            or provider.base_url != 'https://api.deepseek.com'):
        raise PolicyRunError('study_model_contract_invalid')
    budget = manifest['budget']
    if (budget['max_total_input_tokens'] != 24000
            or budget['max_total_output_tokens'] != generation['max_total_output_tokens']
            or budget['max_wall_seconds'] != 180 or budget['max_tool_reads'] != 4):
        raise PolicyRunError('study_cell_budget_invalid')
    if (sum(row['size_bytes'] for row in manifest['snapshot']['files'].values()) > 12 * 1024
            or sum(manifest['snapshot']['files'][name]['size_bytes']
                   for name in manifest['task']['initial_evidence_files']) > 4 * 1024):
        raise PolicyRunError('study_source_limit')
    root = Path(out).absolute()
    try:
        validate_workspace_path(root, output=True)
        if root.is_relative_to(Path(dataset_root).absolute() / 'snapshots'):
            raise PolicyRunError('run_output_in_snapshot')
    except ContractError:
        raise PolicyRunError('run_output_not_exclusive') from None
    tools = StaticEvidenceTools(manifest, dataset_root)
    e0 = initial_evidence(manifest, tools)
    visible = list(e0['source'])
    plan = _fixed_plan(manifest, dataset_root, fixed_plan) if strategy == 'fixed_read_3' else None
    ledger = _ReservationLedger(budget, 'D')  # Three requests; the frozen budget derives each cap.
    timing = {'preparation': 0.0, 'provider': 0.0, 'tool': 0.0}
    result = {'schema': SCHEMA, 'task_id': manifest['task']['id'], 'repeat': manifest['repeat'],
        'manifest_sha256': manifest['manifest_sha256'], 'strategy': strategy,
        'initial_evidence_sha256': sha256(_json_bytes(e0)).hexdigest(),
        'status': 'running', 'error_code': None, 'review_status': 'not_ready',
        'answer': None, 'first_answer': None, 'tool_reads': 0, 'tool_events': [],
        'requests': [], 'reservations': ledger.rows, 'model_calls_live': 0,
        'semantic_proof': False, 'provenance': provenance,
        'fixed_plan': plan, 'timing_seconds': timing}
    messages = [{'role':'system', 'content': DIRECT_INSTRUCTIONS if strategy != 'adaptive_read_3'
        else DIAGNOSIS_CRITERIA + ' ' + TOOL_INSTRUCTIONS + ' This task permits 4 read tools in total.'},
        {'role':'user', 'content':_json_bytes(e0).decode('utf-8')}]
    if generation['name'] is not None:
        messages[0]['content'] += ' ' + EXTERNAL_EVIDENCE_CRITERIA
    if strategy == 'direct_full_3':
        additional = tools.source([name for name in sorted(manifest['task']['authorized_files'])
                                   if name not in manifest['task']['initial_evidence_files']])
        _append_source(visible, additional)
        messages.append({'role':'user', 'content':_json_bytes({'additional_source':additional}).decode('utf-8')})
    try:
        with open_directory(root.parent) as parent_fd:
            os.mkdir(root.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except (OSError, ContractError):
        raise PolicyRunError('run_output_not_exclusive') from None
    _write_exclusive(root / 'claim.json', {'schema':SCHEMA,
        'manifest_sha256':manifest['manifest_sha256'], 'strategy':strategy,
        'state':'claimed_before_first_dispatch'}, 4096)
    timing['preparation'] = monotonic() - started
    pinned_fingerprint = None

    def record_response(decision, response, *, error=None, billing=None):
        evidence = _response_record(response)
        if billing is not None:
            evidence['billing_evidence'] = billing
        if error is not None:
            try:
                identity = error.identity_evidence
                evidence['identity_evidence_state'] = 'captured' if identity is not None else 'unverified'
                if identity is not None:
                    evidence['identity_evidence'] = identity
            except (AttributeError, ValueError):
                evidence['identity_evidence_rejected'] = True
                evidence['identity_evidence_state'] = 'rejected'
        if error is not None and error.diagnostic is not None:
            try:
                evidence['failure_diagnostic'] = validate_failure_diagnostic(error.diagnostic)
            except (ValueError, TypeError):
                evidence['failure_diagnostic_rejected'] = True
        _write_exclusive(root / f'decision-{decision:02d}.response.json', evidence,
                         budget['max_record_bytes'])
        return evidence

    def dispatch():
        nonlocal pinned_fingerprint
        remaining = budget['max_wall_seconds'] - (monotonic() - started)
        if remaining <= 0:
            raise PolicyRunError('wall_budget_exhausted')
        row = ledger.reserve()
        decision = row['decision']
        use_tools = strategy == 'adaptive_read_3'
        declared_tools = TOOL_SCHEMAS if use_tools else []
        if use_tools and generation['name'] is not None and decision==3:
            declared_tools = [tool for tool in TOOL_SCHEMAS if tool['function']['name'] in TERMINAL_TOOLS]
        request = ConversationRequest(messages=messages, tools=declared_tools,
            response_schema=None if use_tools else OUTPUT_SCHEMA,
            max_input_tokens=row['reserved_input_tokens'],
            max_output_tokens=row['reserved_output_tokens'],
            timeout_seconds=min(60.0, remaining), thinking={'type':'enabled'}, reasoning_effort='high',
            metadata={'prompt_version':manifest['prompt_version'], 'strategy':strategy,
                      'task_id':manifest['task']['id']})
        try:
            body = provider.encode_conversation_request(request)
        except ProviderError as error:
            ledger.rows.pop()
            raise PolicyRunError(error.code) from None
        if len(body) > budget['max_request_bytes']:
            ledger.rows.pop()
            raise PolicyRunError('request_bytes_limit')
        if len(_json_bytes(result)) + len(body) + 512 > budget['max_record_bytes']:
            ledger.rows.pop()
            raise PolicyRunError('run_record_preflight_limit')
        request_hash = sha256(body).hexdigest()
        # Admission can refuse without counting a provider attempt.
        if admit is not None:
            try:
                admit(request_hash)
            except Exception:
                ledger.rows.pop()
                raise
        _write_exclusive(root / f'decision-{decision:02d}.reservation.json',
            {'schema':SCHEMA, 'decision':decision, 'state':'reserved_unknown_outcome',
             'request_sha256':request_hash, 'reserved_input_tokens':row['reserved_input_tokens'],
             'reserved_output_tokens':row['reserved_output_tokens']}, 4096)
        wire = json.loads(body)
        _write_exclusive(root / f'decision-{decision:02d}.request.json', wire, budget['max_record_bytes'])
        entry = {'decision':decision, 'request':wire, 'request_sha256':request_hash,
                 'response':None, 'error_code':None}
        result['requests'].append(entry)
        provider_started = monotonic()
        response = None
        error = None
        try:
            response = provider.converse(request)
        except ProviderError as caught:
            error = caught
        finally:
            timing['provider'] += monotonic() - provider_started
        # The verified billing receipt is available to accounting even if the
        # assistant/policy contract fails; settlement is never an automatic retry.
        settlement_error = None
        if settle is not None:
            try:
                settle(request_hash, response=response, error=error)
            except Exception as refused:
                # A gate stop must prevent policy parsing/further dispatch, but
                # cannot erase the response or independently known billing that
                # caused it. Preserve these artifacts before propagating it.
                settlement_error = refused
        billing = None
        if error is not None:
            billing = ledger.fail(row, error, request_sha256=request_hash, model=provider.model)
            entry['error_code'] = error.code
            entry['response'] = record_response(decision, error.response, error=error, billing=billing)
            code = ('remote_outcome_unknown' if row['usage'] is None else error.code)
        else:
            entry['response'] = record_response(decision, response)
            try:
                ledger.complete(row, response)
                code = None
            except PolicyRunError as violation:
                code = violation.code
            if row['state'] == 'completed_usage_unknown_reservation_kept':
                code = 'remote_outcome_unknown'
        stop_code = getattr(settlement_error, 'code', 'settlement_failed') if settlement_error else code
        _write_exclusive(root / f'decision-{decision:02d}.outcome.json',
            {'schema':SCHEMA, 'decision':decision, 'request_sha256':request_hash,
             'state':row['state'], 'usage':row['usage'], 'error_code':stop_code,
             'response_sha256':sha256(_json_bytes(entry['response'])).hexdigest()}, 4096)
        if settlement_error is not None:
            raise settlement_error
        if code:
            raise PolicyRunError(code)
        if response.model != provider.model:
            raise PolicyRunError('model_identity_drift')
        fingerprint = response.system_fingerprint
        if type(fingerprint) is not str or not fingerprint.strip():
            raise PolicyRunError('system_fingerprint_unknown')
        if pinned_fingerprint is None:
            pinned_fingerprint = fingerprint
        elif fingerprint != pinned_fingerprint:
            raise PolicyRunError('system_fingerprint_drift')
        return response

    def read(name, args, decision, *, call_id=None):
        if result['tool_reads'] >= 4:
            raise PolicyRunError('read_tool_budget_exhausted')
        tool_started = monotonic()
        try:
            evidence = tools.execute(name, args)
        finally:
            timing['tool'] += monotonic() - tool_started
        if evidence.get('status') != 'ok':
            if evidence.get('error_code') in {'arguments_invalid','symbol_invalid','term_invalid','files_invalid'}:
                raise PolicyRunError('read_tool_arguments_invalid')
            raise PolicyRunError('read_tool_invalid')
        result['tool_reads'] += 1
        event = {'decision':decision, 'name':name, 'arguments':args, 'result':evidence}
        if call_id is not None:
            event['id'] = call_id
        result['tool_events'].append(event)
        _append_source(visible, evidence.get('snippets', []))
        return evidence

    try:
        if strategy != 'adaptive_read_3':
            for decision in range(1,4):
                if decision > 1:
                    if strategy == 'fixed_read_3':
                        group = [read(action['name'], action['arguments'], decision)
                                 for action in plan['groups'][decision - 2]]
                        messages.append({'role':'user','content':_json_bytes(
                            {'fixed_evidence':group, 'origin':'host_fixed_read_plan'}).decode('utf-8')})
                    messages.append({'role':'user','content':CHECK_INSTRUCTIONS if decision == 2 else FINAL_INSTRUCTIONS})
                response = dispatch()
                if response.tool_calls or response.finish_reason != 'stop':
                    raise PolicyRunError('unexpected_model_action')
                answer = _diagnosis(_object(response.content), visible)
                if decision == 1 and strategy in ('direct_initial_3','direct_full_3'):
                    result['first_answer'] = answer
                messages.append(response.assistant_message)
            result.update(answer=answer, status='semantic_pending', review_status='pending')
        else:
            seen_ids, seen_reads = set(), set()
            for decision in range(1,4):
                response = dispatch()
                if not response.tool_calls:
                    raise PolicyRunError('terminal_action_missing')
                parsed = [_tool_call(call) for call in response.tool_calls]
                ids = [item[0] for item in parsed]
                if len(ids) != len(set(ids)) or any(item in seen_ids for item in ids):
                    raise PolicyRunError('duplicate_tool_call')
                seen_ids.update(ids)
                names = [name for _,name,_ in parsed]
                if decision == 3 and (len(names)!=1 or names[0] not in TERMINAL_TOOLS):
                    raise PolicyRunError('third_decision_must_terminate')
                if any(name in TERMINAL_TOOLS for name in names):
                    if len(parsed)!=1 or names[0] not in TERMINAL_TOOLS:
                        raise PolicyRunError('mixed_terminal_action')
                    _,name,args = parsed[0]
                    if name == 'ask_user':
                        gap = args.get('gap')
                        if (set(args) != {'gap'} or type(gap) is not str or not
                                ASK_USER_GAP_SCHEMA['minLength'] <= len(gap.strip()) <=
                                ASK_USER_GAP_SCHEMA['maxLength']):
                            raise PolicyRunError('ask_user_contract_invalid')
                        result.update(status='ask_user_terminated',review_status='pending',gap=gap,
                            task_resolution='unresolved',abstention_review_status='pending')
                    else:
                        if set(args) != {'diagnosis'} or type(args['diagnosis']) is not dict:
                            raise PolicyRunError('finish_contract_invalid')
                        result.update(answer=_diagnosis(args['diagnosis'],visible),
                            status='semantic_pending',review_status='pending')
                    result['tool_events'].append({'decision':decision,'name':name,'arguments':args,'terminal':True})
                    break
                if len(parsed)>2 or not set(names)<=READ_TOOLS:
                    raise PolicyRunError('read_tool_limit_or_forbidden')
                if result['tool_reads'] + len(parsed) > 4:
                    raise PolicyRunError('read_tool_budget_exhausted')
                messages.append(response.assistant_message)
                for call_id,name,args in parsed:
                    key = (name,_json_bytes(args))
                    if key in seen_reads:
                        raise PolicyRunError('duplicate_read_tool')
                    seen_reads.add(key)
                    evidence = read(name,args,decision,call_id=call_id)
                    messages.append({'role':'tool','tool_call_id':call_id,'content':_json_bytes(evidence).decode('utf-8')})
    except PolicyRunError as error:
        result.update(status='remote_outcome_unknown' if error.code == 'remote_outcome_unknown'
            else 'failed', error_code=error.code, review_status='not_ready')
    result['source'] = visible
    for field in ('question', 'scenario', 'assumptions'):
        result[field] = manifest['task'][field]
    result['requests_attempted'] = len(result['requests'])
    if provenance == 'approved_live':
        count = getattr(provider.transport, 'live_attempts', None)
        if type(count) is not int or not 0 <= count <= len(result['requests']):
            raise PolicyRunError('study_live_attempts_unknown')
        result['model_calls_live'] = count
    result['elapsed_seconds'] = round(monotonic() - started,6)
    result['timing_seconds'] = {name:round(value,6) for name,value in timing.items()}
    try:
        _write_exclusive(root / 'result.json',result,budget['max_record_bytes'])
    except PolicyRunError as error:
        if error.code != 'run_record_limit':
            raise
        result = {key:result[key] for key in ('schema','task_id','repeat','manifest_sha256','strategy',
            'initial_evidence_sha256','requests_attempted','tool_reads','elapsed_seconds','timing_seconds','model_calls_live')}
        result.update(status='failed',error_code='run_record_limit',review_status='not_ready',artifact_complete=False)
        _write_exclusive(root/'result.json',result,budget['max_record_bytes'])
    return result
