"""One-time continuation of the frozen neutral study; activation stays explicit."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re

from macr.evals.deepseek_policy_manifest import _read, _root
from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_policy_spend import _normalized_charge, _parse_usd, _usd
from macr.evals.deepseek_study_batch import _run_study_core
from macr.evals.deepseek_study_live import (ENDPOINT, MODEL, PRICE, LOG_POLICY,
    _BoundWire, _ScriptedWire, _StudyGate, _proposal as _validate_grid)
from macr.evals.deepseek_study_profile import (SCHEMA as STUDY_SCHEMA, StudyError,
    digest, load_study, prepare_study, read_json, require)
from macr.evals.deepseek_study_report import build_study_report
from macr.evals.deepseek_study_runner import _run_study_cell_core
from macr.investigation.scope import open_directory
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport

SOURCE_PROFILE_SHA = '3b44c573dcd478c686ba142f926ae431350299bfba08a44ea37f5e108ddd4354'
SOURCE_FILES_SHA = '5dbf600125df7c6f319a2cb073d837fd08a23ffe6328ffa91838b5af15cfc9bb'
SCHEMA = 'macr.deepseek.study.continuation.v1'
OPENING = {'request_count':7,'known_accrued_usd':'0.008200','unknown_held_usd':'0.000000'}
_HASH = re.compile('[0-9a-f]{64}\\Z')


def _decode(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'continuation_json_invalid')
            value[key] = item
        return value
    try:
        return json.loads(data, object_pairs_hook=unique,
            parse_constant=lambda _: require(False,'continuation_json_invalid'))
    except (ValueError, UnicodeError, RecursionError):
        raise StudyError('continuation_json_invalid') from None


def _source(source_control):
    """Read only the pinned terminal whitelist; never inventory its siblings."""
    root = _root(source_control)
    freeze = _decode(_read(root,'terminal-artifact-freeze.json',65536))
    require(type(freeze) is dict and set(freeze)=={'schema','profile_sha256','files',
        'files_sha256','state'} and freeze['schema']=='macr.deepseek.study.terminal-freeze.v1'
        and freeze['state']=='halted_identity_evidence_unavailable_no_retry'
        and freeze['profile_sha256']==SOURCE_PROFILE_SHA
        and type(freeze['files']) is list and len(freeze['files'])==72
        and freeze['files_sha256']==SOURCE_FILES_SHA==digest(freeze['files']),
        'continuation_source_freeze_invalid')
    frozen, total = {}, 0
    for row in freeze['files']:
        require(type(row) is dict and set(row)=={'path','bytes','sha256'},
            'continuation_source_file_invalid')
        name = row['path']
        require(type(name) is str and 0<len(name)<=512 and not name.startswith('/')
            and '\\' not in name and all(part not in ('','.','..') for part in name.split('/'))
            and name not in frozen and name!='terminal-artifact-freeze.json'
            and type(row['bytes']) is int and 0<=row['bytes']<=1048576
            and type(row['sha256']) is str and _HASH.fullmatch(row['sha256']),
            'continuation_source_file_invalid')
        total += row['bytes']
        require(total<=16*1048576,'continuation_source_size_limit')
        data = _read(root,name,1048576)
        require(len(data)==row['bytes'] and sha256(data).hexdigest()==row['sha256'],
            'continuation_source_file_drift')
        frozen[name] = data

    def document(name):
        require(name in frozen,'continuation_source_file_missing')
        return _decode(frozen[name])

    study, summary = document('study.json'), document('summary.json')
    require(type(study) is dict and study.get('schema')==STUDY_SCHEMA
        and study.get('remote_enabled') is False and study.get('control_root')==str(root)
        and study.get('profile_sha256')==SOURCE_PROFILE_SHA==digest(
            {key:value for key,value in study.items() if key!='profile_sha256'}),
        'continuation_source_profile_invalid')
    _validate_grid(study)
    require(type(summary) is dict and summary.get('schema')=='macr.deepseek.study.execution.v1'
        and summary.get('profile_sha256')==SOURCE_PROFILE_SHA
        and summary.get('provenance')=='approved_live' and summary.get('planned_cells')==36
        and summary.get('planned_request_ceiling')==108 and summary.get('batch_halted') is True
        and summary.get('model_calls_live')==7
        and summary.get('semantic_scoring')=='pending_independent_review',
        'continuation_source_summary_invalid')
    accounting = summary.get('accounting')
    require(type(accounting) is dict and all(accounting.get(key)==value for key,value in OPENING.items())
        and accounting.get('state')=='open' and accounting.get('model_calls_live')==7
        and accounting.get('full_miss_sensitivity_usd')=='0.010307'
        and accounting.get('basis')=='provider_usage_peak_local_accrual_not_supplier_bill',
        'continuation_source_accounting_invalid')
    rows = summary.get('rows')
    require(type(rows) is list and len(rows)==36,'continuation_source_rows_invalid')
    results, requests = {}, []
    expected = (('failed','model_output_incomplete',1), ('semantic_pending',None,3),
                ('failed','system_fingerprint_unknown',3))
    for index, (cell,row) in enumerate(zip(study['cells'],rows)):
        require(type(row) is dict and all(row.get(k)==v for k,v in cell.items()),
            'continuation_source_rows_invalid')
        if index>=3:
            require(row==cell|{'status':'not_attempted'},'continuation_source_remaining_invalid')
            continue
        status, error, count = expected[index]
        require(row.get('status')==status and row.get('error_code')==error,
            'continuation_source_terminal_invalid')
        result = document(f"cells/{cell['cell_id']}.accounted.json")
        require(type(result) is dict and digest(result)==row.get('result_sha256')
            and result.get('schema')=='macr.deepseek.study.cell.v1'
            and result.get('cell_id')==cell['cell_id'] and result.get('task_id')==cell['task_id']
            and result.get('strategy')==cell['strategy'] and result.get('status')==status
            and result.get('error_code')==error and result.get('requests_attempted')==count
            and type(result.get('requests')) is list and len(result['requests'])==count,
            'continuation_source_result_invalid')
        for decision, request in enumerate(result['requests'],1):
            require(type(request) is dict and request.get('decision')==decision
                and type(request.get('request_sha256')) is str
                and _HASH.fullmatch(request['request_sha256']), 'continuation_source_request_invalid')
            requests.append((cell['cell_id'],request))
        results[cell['cell_id']] = result
    charged, fingerprints = 0, []
    for number, (cell_id, request) in enumerate(requests,1):
        reservation = document(f'ledger/{number:03d}.reservation.json')
        base = {'number':number,'cell_id':cell_id,'request_sha256':request['request_sha256'],
                'reserved_micro_usd':330000,'state':'reserved_unknown','usage':None}
        require(reservation==base,'continuation_source_reservation_invalid')
        outcome = document(f'ledger/{number:03d}.outcome.json')
        require(type(outcome) is dict,'continuation_source_outcome_invalid')
        cost = _normalized_charge(outcome.get('usage'),2000)
        require(cost is not None and outcome==base|{'state':'settled','usage':outcome['usage'],
            'charged_micro_usd':cost},'continuation_source_outcome_invalid')
        charged += cost
        response = request.get('response')
        require(type(response) is dict,'continuation_source_identity_invalid')
        if number==7:
            require(response.get('state')=='unknown' and response.get('model') is None
                and response.get('system_fingerprint') is None
                and response.get('identity_evidence') is None,
                'continuation_historical_gap_changed')
        else:
            fingerprint = response.get('system_fingerprint')
            require(response.get('state')=='received' and response.get('model')=='deepseek-flash'
                and type(fingerprint) is str and 0<len(fingerprint.strip())<=256
                and all(ord(char)>=32 and ord(char)!=127 for char in fingerprint),
                'continuation_source_identity_invalid')
            fingerprints.append(fingerprint)
    require(len(requests)==7 and charged==8200 and len(fingerprints)==6
        and len(set(fingerprints))==1,'continuation_source_pin_or_spend_invalid')
    return {'study':study,'results':results,'fingerprint':fingerprints[0],
            'files_sha256':freeze['files_sha256']}


def _plan(source, study):
    old = source['study']
    require(study['pack_root']==old['pack_root'] and study['pack_sha256']==old['pack_sha256']
        and study['custodian']==old['custodian'] and study['budget']==old['budget']
        and study['order_seed']==old['order_seed'] and study['prompt_version']==old['prompt_version'],
        'continuation_input_scope_changed')
    require(len(study['tasks'])==len(old['tasks']),'continuation_source_task_drift')
    for previous, task in zip(old['tasks'],study['tasks']):
        require({key:value for key,value in task.items() if key!='fixed_plan'}==
                {key:value for key,value in previous.items() if key!='fixed_plan'}
            and {key:value for key,value in task['fixed_plan'].items() if key!='elapsed_seconds'}==
                {key:value for key,value in previous['fixed_plan'].items() if key!='elapsed_seconds'},
            'continuation_source_task_drift')
    by_pair = {(cell['task_id'],cell['strategy']):cell for cell in study['cells']}
    require(len(by_pair)==36,'continuation_grid_invalid')
    mapping, remaining = [], []
    for index, cell in enumerate(old['cells']):
        new = by_pair.get((cell['task_id'],cell['strategy']))
        require(new is not None and new['project_id']==cell['project_id'],
            'continuation_grid_invalid')
        mapping.append({'logical_cell_id':cell['cell_id'],'code_cell_id':new['cell_id'],
            'task_id':cell['task_id'],'strategy':cell['strategy']})
        if index>=3:
            remaining.append(new)
    require(remaining==[cell for cell in study['cells'] if cell in remaining],
        'continuation_remaining_order_changed')
    value = {'schema':SCHEMA,'control_root':study['control_root'],
        'profile_sha256':study['profile_sha256'],'source_control_root':old['control_root'],
        'source_profile_sha256':old['profile_sha256'],'source_files_sha256':source['files_sha256'],
        'source_code_sha256':digest(old['implementation']),
        'code_sha256':digest(study['implementation']), 'cell_mapping':mapping,
        'remaining_cells':remaining,'remaining_cells_sha256':digest(remaining),
        'opening':dict(OPENING),'max_new_requests':99,'remaining_usd':'1.991800',
        'historical_pin':{'system_fingerprint':source['fingerprint'],'validated_requests':6,
            'unverifiable_request':7,'continuity':'historical_gap_accepted'}}
    value['plan_sha256'] = digest(value)
    return value


def prepare_continuation(source_control, *, out, project_root):
    """Freeze all nine neutral tasks, selecting only the 33 untouched cells."""
    source = _source(source_control)
    old = source['study']
    output = Path(out).absolute()
    require('..' not in output.parts and not output.is_relative_to(Path(old['control_root'])),
        'continuation_output_scope_invalid')
    _root(output.parent)
    study = prepare_study(old['pack_root'],custodian=old['custodian'],out=out,
        project_root=project_root,budget=old['budget'],seed=old['order_seed'])
    plan = _plan(source,study)
    _write_exclusive(Path(study['control_root'])/'continuation.json',plan,65536)
    return plan


def _load(control_root):
    study = load_study(control_root)
    plan = read_json(Path(study['control_root']),'continuation.json',65536)
    require(type(plan) is dict and type(plan.get('source_control_root')) is str,
        'continuation_plan_invalid')
    source = _source(plan['source_control_root'])
    require(plan==_plan(source,study),'continuation_plan_drift')
    return study, plan, source


def _approval(study, plan):
    return {'schema':'macr.deepseek.study.continuation-approval.v1',
        'authorized':False,'confirmation_ref':'','authorization_scope':'one-time-neutral-study-continuation',
        'control_root':study['control_root'],'profile_sha256':study['profile_sha256'],
        'plan_sha256':plan['plan_sha256'],'source_control_root':plan['source_control_root'],
        'source_profile_sha256':plan['source_profile_sha256'],
        'source_files_sha256':plan['source_files_sha256'],
        'remaining_cells':plan['remaining_cells'],'remaining_cells_sha256':plan['remaining_cells_sha256'],
        'cell_mapping_sha256':digest(plan['cell_mapping']),'opening':plan['opening'],
        'historical_pin':plan['historical_pin'],'historical_fingerprint_gap_accepted':True,
        'model':dict(MODEL),'endpoint':ENDPOINT,'max_requests':108,'max_usd':'2.000000',
        'max_new_requests':99,'remaining_usd':'1.991800','price_snapshot':dict(PRICE),
        'log_root':study['control_root'],'log_policy':dict(LOG_POLICY),'concurrent_requests':1,
        'automatic_retries':0,'resume':False,'ledger_reset':False}


def propose_continuation_approval(control_root):
    """Return the exact continuation scope without authorizing any request."""
    study, plan, _ = _load(control_root)
    return _approval(study,plan)


class _ContinuationGate(_StudyGate):
    def __init__(self, root, study, provenance, fingerprint):
        super().__init__(root,study,provenance)
        self.fingerprint = fingerprint  # The old six canonical values are the future pin.

    def admit(self, request_sha256):
        if len(self.rows)>=99 or len(self.rows)+7>=108:
            raise PolicyRunError('study_request_budget_exhausted')
        occupied = 8200+sum(row.get('charged_micro_usd',330000) for row in self.rows)
        if occupied+330000>2_000_000:
            raise PolicyRunError('study_usd_budget_exhausted')
        super().admit(request_sha256)

    def state(self, cell_id=None):
        segment = super().state(cell_id)
        if cell_id is not None:
            return segment
        cumulative = segment | {'request_count':segment['request_count']+7,
            'known_accrued_usd':_usd(_parse_usd(segment['known_accrued_usd'],code='study_budget_invalid')+8200),
            'full_miss_sensitivity_usd':_usd(_parse_usd(segment['full_miss_sensitivity_usd'],
                code='study_budget_invalid')+10307)}
        return cumulative | {'opening':dict(OPENING),'segment':segment,'cumulative':cumulative}


def _report(study, results, *, source, plan):
    merged = dict(source['results'])
    by_code = {row['code_cell_id']:row['logical_cell_id'] for row in plan['cell_mapping']}
    for cell_id,result in results.items():
        logical = by_code[cell_id]
        require(logical not in merged,'continuation_old_cell_replay')
        merged[logical] = result
    report = build_study_report(source['study'],merged)
    report['continuation'] = {'plan_sha256':plan['plan_sha256'],
        'source_files_sha256':plan['source_files_sha256'],'cell_mapping':plan['cell_mapping'],
        'historical_pin':plan['historical_pin'],'historical_fingerprint_gap_accepted':True,
        'all_request_fingerprint_continuity_verified':False,
        'segments':[{'profile_sha256':plan['source_profile_sha256'],
            'code_sha256':plan['source_code_sha256'],'processed_cells':3,'requests':7},
            {'profile_sha256':study['profile_sha256'],'code_sha256':plan['code_sha256'],
             'planned_cells':33,'processed_cells':len(results)}]}
    return report


def _claim_successor(plan):
    root = Path(plan['source_control_root']).parent/'continuation-claims'
    with open_directory(root,create=True):
        pass
    try:
        _write_exclusive(root/f"{plan['source_profile_sha256']}.json",{
            'schema':'macr.deepseek.study.continuation-claim.v1',
            'source_profile_sha256':plan['source_profile_sha256'],
            'source_files_sha256':plan['source_files_sha256'],'control_root':plan['control_root'],
            'profile_sha256':plan['profile_sha256'],'plan_sha256':plan['plan_sha256'],
            'state':'claimed_before_key_or_dispatch'},4096)
    except FileExistsError:
        raise StudyError('continuation_already_claimed') from None


def execute_continuation(control_root, approval, *, enabled=False, key_reader=None,
                         scripted_by_cell=None):
    """Consume the source's sole successor claim before any credential ingress."""
    require(enabled is True,'continuation_disabled')
    study, plan, source = _load(control_root)
    require(type(approval) is dict and approval.get('authorized') is True
        and type(approval.get('confirmation_ref')) is str
        and 0<len(approval['confirmation_ref'].strip())<=128,'continuation_approval_missing')
    require(approval|{'authorized':False,'confirmation_ref':''}==_approval(study,plan),
        'continuation_approval_scope_mismatch')
    require(callable(key_reader),'study_live_key_reader_invalid')
    simulated = scripted_by_cell is not None
    scripts = {}
    if simulated:
        require(type(scripted_by_cell) is dict and set(scripted_by_cell)==
            {cell['cell_id'] for cell in plan['remaining_cells']},'study_live_script_invalid')
        scripts = {cell:_ScriptedWire(replies) for cell,replies in scripted_by_cell.items()}
    root = Path(study['control_root'])
    retention = {'schema':'macr.deepseek.study.log.v1','profile_sha256':study['profile_sha256'],
        'confirmation_ref':approval['confirmation_ref'],
        'contents':['source','model_output','reasoning_content','usage','receipts'],**LOG_POLICY}
    try:
        _write_exclusive(root/'retention.json',retention,4096)
    except FileExistsError:
        require(read_json(root,'retention.json',4096)==retention,'study_live_log_policy_mismatch')
    _claim_successor(plan)
    key, gate, transports = None, None, []
    provenance = 'approved_live_fake' if simulated else 'approved_live'

    def gate_factory(root, frozen, *, provenance):
        nonlocal gate
        require(frozen['profile_sha256']==study['profile_sha256'],'study_live_profile_changed')
        _, current, _ = _load(root)
        require(current==plan,'continuation_plan_drift')
        gate = _ContinuationGate(root,frozen,provenance,source['fingerprint'])
        return gate

    def provider_factory(cell):
        nonlocal key
        if key is None:
            try:
                key = key_reader()
            except Exception:
                raise StudyError('study_live_key_input_failed') from None
            require(type(key) is str and 1<=len(key)<=4096 and key==key.strip()
                and all(32<ord(char)<127 for char in key),'study_live_key_invalid')
        inner = scripts[cell['cell_id']] if simulated else _ApprovedTransport(key)
        transports.append(inner)
        wire = _BoundWire(inner,simulated=simulated)
        gate.wire = wire
        return DeepSeekProvider(model=MODEL['model_id'],transport=wire,allow_remote=False)

    def cell_runner(*args, **kwargs):
        return _run_study_cell_core(*args,**kwargs,provenance=provenance)

    def report_builder(frozen, results):
        return _report(frozen,results,source=source,plan=plan)

    try:
        return _run_study_core(root,provider_factory=provider_factory,cell_runner=cell_runner,
            provenance=provenance,gate_factory=gate_factory,execution_cells=plan['remaining_cells'],
            report_builder=report_builder)
    finally:
        key = None
        for transport in transports:
            if type(transport) is _ApprovedTransport:
                transport._api_key = ''
