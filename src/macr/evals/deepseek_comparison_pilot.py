"""Exact-scope R/U pilot; no credentials or dispatch during preparation.

Consent records are local operator records, not cryptographic signatures.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import os
import random
import re
import stat
from time import monotonic

from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_study_batch import KNOWN_CONTRACT_ERRORS, _FixtureGate
from macr.evals.deepseek_policy_spend import _usage_evidence
from macr.evals.deepseek_study_generation import V2_PROFILE, EVIDENCE_PROFILE
from macr.evals.deepseek_study_live import (
    ENDPOINT, MODEL, LOG_POLICY, _ScriptedWire, _BoundWire, _StudyGate)
from macr.evals.deepseek_study_profile import (
    STRATEGIES, StudyError, digest, load_neutral_pack, load_study,
    prepare_study, read_json, require, _custodian, _read)
from macr.evals.deepseek_study_runner import _run_study_cell_core
from macr.investigation.scope import open_directory, validate_workspace_path
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport

SCHEMA = 'macr.deepseek.comparison-pilot.v1'
BUDGETS = {'R':{'max_requests':21,'max_usd':'1.000000'},
           'U':{'max_requests':54,'max_usd':'1.500000'}}
PRICE = {'checked_date':'2026-10-30',
    'source':'https://api-docs.deepseek.com/quick_start/pricing/',
    'input_miss_per_million':'0.30','input_hit_per_million':'0.006',
    'output_per_million':'1.20','contract_input_envelope':1048576,
    'context_exact_provider_limit_verified':False,
    'max_output_per_request':8192,'minimum_reservation_micro_usd':324404,
    'reservation_micro_usd':330000}
_HASH = re.compile('[0-9a-f]{64}\\Z')


def _selection(value, tasks):
    require(type(value) is dict and set(value)=={
        'schema','source_sha256','selection_statement','rows'}
        and value['schema']=='macr.deepseek.pilot.regression.v1', 'pilot_selection_invalid')
    require(type(value['source_sha256']) is str and _HASH.fullmatch(value['source_sha256'])
        and type(value['selection_statement']) is str
        and 0<len(value['selection_statement'].strip())<=2000, 'pilot_selection_invalid')
    rows=value['rows'];seen=set()
    require(type(rows) is list and len(rows)==7, 'pilot_selection_invalid')
    for row in rows:
        require(type(row) is dict and set(row)=={
            'task_id','strategy','response_sha256','finish_reason'}, 'pilot_selection_invalid')
        require(type(row['task_id']) is str and row['task_id'] in tasks
            and row['strategy'] in STRATEGIES and row['finish_reason']=='length'
            and type(row['response_sha256']) is str and _HASH.fullmatch(row['response_sha256']),
            'pilot_selection_invalid')
        pair=(row['task_id'],row['strategy'])
        require(pair not in seen,'pilot_selection_invalid');seen.add(pair)
    return value


def _regression_binding(pack_root,pack,selection,custodian):
    _custodian(custodian,regression=True)
    sources=[]
    for task in pack['tasks']:
        for name in task['authorized_files']:
            path=task['snapshot_root']+'/'+name;raw=_read(pack_root,path,12289)
            sources.append({'path':path,'size_bytes':len(raw),'sha256':sha256(raw).hexdigest()})
    require(custodian['neutral_manifest_sha256']==selection['source_sha256']
        and custodian['selection_sha256']==digest(selection)
        and custodian['pack_sha256']==digest(pack)
        and custodian['source_inventory_sha256']==digest(sorted(sources,key=lambda r:r['path'])),
        'pilot_regression_commitment_drift')


def _regression_receipt(root):
    root=Path(root).absolute()
    raw=read_json(root,'pilot.json')
    require(type(raw) is dict and raw.get('phase')=='R','pilot_regression_required')
    plan=load_pilot(root)
    summary=read_json(root,'summary.json')
    require(type(summary) is dict,'pilot_regression_not_qualified')
    _verify_regression_summary(root,plan,summary)
    return {'control_root':str(root),'plan_sha256':plan['plan_sha256'],
        'summary_sha256':digest(summary),'provenance':summary.get('provenance'),
        'pack_sha256':plan['pack_sha256']}


def _verify_regression_summary(root,plan,summary):
    """Recompute promotion from bound outcomes, not an editable eligibility flag."""
    from macr.evals.deepseek_comparison_report import build_pilot_report
    provenance=summary.get('provenance')
    require(type(provenance) is str and provenance in {
        'approved_live','approved_live_fake','scripted_offline_fixture'},
        'pilot_regression_not_qualified')
    require(read_json(root,'execution.claim.json',4096)=={
        'plan_sha256':plan['plan_sha256'],'provenance':provenance,
        'state':'claimed_before_credentials_or_dispatch'},'pilot_regression_receipt_drift')
    results={};requests=[];live=0;failures=0
    for cell in plan['cells']:
        result=read_json(root/'cells',cell['cell_id']+'.accounted.json',1048576)
        require(type(result) is dict and result.get('cell_id')==cell['cell_id']
            and result.get('provenance')==provenance
            and type(result.get('requests')) is list and 1<=len(result['requests'])<=3
            and result.get('requests_attempted')==len(result['requests']),
            'pilot_regression_receipt_drift')
        attempts=result.get('model_calls_live')
        require(type(attempts) is int and attempts==(
            len(result['requests']) if provenance=='approved_live' else 0),
            'pilot_regression_receipt_drift')
        live+=attempts;results[cell['cell_id']]=result
        requests.extend((cell['cell_id'],request) for request in result['requests'])
        failures+=int(result.get('status') not in {'semantic_pending','ask_user_terminated'}
            and result.get('error_code') in KNOWN_CONTRACT_ERRORS)
    require(1<=len(requests)<=21,'pilot_regression_receipt_drift')
    gate=_FixtureGate(root,{'execution_profile':V2_PROFILE,'budget':BUDGETS['R']},
        provenance=provenance)
    for number,(cell_id,request) in enumerate(requests,1):
        require(type(request) is dict and type(request.get('request_sha256')) is str
            and _HASH.fullmatch(request['request_sha256']),'pilot_regression_receipt_drift')
        row=read_json(root/'ledger',f'{number:03d}.outcome.json',4096)
        reserve={'number':number,'cell_id':cell_id,'request_sha256':request['request_sha256'],
            'reserved_micro_usd':330000,'state':'reserved_unknown','usage':None}
        require(read_json(root/'ledger',f'{number:03d}.reservation.json',4096)==reserve
            and type(row) is dict and row.get('state')=='settled'
            and all(row.get(key)==reserve[key] for key in (
                'number','cell_id','request_sha256','reserved_micro_usd'))
            and type(row.get('usage')) is dict,'pilot_regression_receipt_drift')
        usage=row['usage'];raw=usage | {'completion_tokens_details':{
            'reasoning_tokens':usage.get('reasoning_tokens')}}
        evidence=_usage_evidence(raw,8192)
        require(evidence is not None and type(row.get('charged_micro_usd')) is int
            and row['charged_micro_usd']==evidence[0],'pilot_regression_receipt_drift')
        gate.rows.append(row)
    accounting=gate.state() | {'model_calls_live':live}
    report=build_pilot_report(plan,results,accounting,halted=False)
    expected=report | {'provenance':provenance,'model_calls_live':live,
        'promotion_gate':report['r_gate'],'known_contract_failures':failures}
    require(summary==expected,'pilot_regression_receipt_drift')
    require(report['r_gate']['eligible'] is True,'pilot_regression_not_qualified')


def _cells(studies, phase, selection, seed):
    by_profile={name:{(c['task_id'],c['strategy']):c for c in study['cells']}
                for name,study in studies.items()}
    pairs=[]
    if phase=='R':
        pairs=[(row['task_id'],row['strategy'],V2_PROFILE) for row in selection['rows']]
    else:
        ids=sorted(task['id'] for task in studies[V2_PROFILE]['tasks'])
        rng=random.Random(seed);rng.shuffle(ids)
        first=[V2_PROFILE]*5+[EVIDENCE_PROFILE]*4;rng.shuffle(first)
        for task,name in zip(ids,first):
            pairs.extend([(task,'adaptive_read_3',name),
                (task,'adaptive_read_3',EVIDENCE_PROFILE if name==V2_PROFILE else V2_PROFILE)])
    return [by_profile[name][(task,strategy)] | {
        'profile':name,'study_root':studies[name]['control_root']}
        for task,strategy,name in pairs]


def _plan(root, studies, phase, selection, prerequisite, seed):
    primary=studies[V2_PROFILE]
    selected=_cells(studies,phase,selection,seed)
    ids={cell['task_id'] for cell in selected}
    tasks=[{key:task[key] for key in ('id','project_id','question','scenario','assumptions')}
           for task in primary['tasks'] if task['id'] in ids]
    sources={}
    for task in primary['tasks']:
        if task['id'] not in ids:continue
        for name,row in task['manifest']['snapshot']['files'].items():
            path=task['snapshot_root']+'/'+name
            sources[path]={'path':path,'sha256':row['sha256'],'size_bytes':row['size_bytes']}
    value={'schema':SCHEMA,'phase':phase,'control_root':str(root),
        'pack_root':primary['pack_root'],'pack_sha256':primary['pack_sha256'],
        'project_root':primary['project_root'],'implementation':primary['implementation'],
        'custodian':primary['custodian'],'budget':dict(BUDGETS[phase]),
        'price_snapshot':dict(PRICE),'cell_limits':primary['cell_limits'],
        'model':dict(MODEL),'endpoint':ENDPOINT,'order_seed':seed,
        'regression_selection':selection,'regression_receipt':prerequisite,
        'profiles':{name:{'control_root':study['control_root'],
            'profile_sha256':study['profile_sha256']} for name,study in studies.items()},
        'tasks':tasks,'cells':selected,'sources':[sources[key] for key in sorted(sources)],
        'remote_enabled':False,'max_known_contract_failures':3,'log_policy':dict(LOG_POLICY)}
    return value | {'plan_sha256':digest(value)}


def _unseen_descriptor(root,studies):
    value=_plan(root,studies,'U',None,None,20261030)
    del value['plan_sha256'];del value['regression_receipt']
    value.update(schema='macr.deepseek.pilot.unseen-scope.v1',
        execution_prerequisite={'phase':'R','requires_real':True,'requires_eligible':True})
    return value | {'scope_sha256':digest(value)}


def _bind_unseen_scope(plan,scope):
    value={key:item for key,item in plan.items() if key!='plan_sha256'}
    value['unseen_scope_sha256']=scope['scope_sha256']
    return value | {'plan_sha256':digest(value)}


def freeze_unseen_inputs(pack_root, *, custodian, out, project_root, seed=20261030):
    """Seal U data/config/order now; neither create nor fabricate an R receipt."""
    require(type(seed) is int and seed==20261030,'pilot_seed_invalid')
    pack=load_neutral_pack(pack_root);_custodian(custodian)
    require(len(pack['tasks'])==9 and sorted(Counter(
        task['project_id'] for task in pack['tasks']).values())==[3,3,3], 'pilot_pack_grid_invalid')
    root=Path(out).absolute();validate_workspace_path(root,output=True)
    require(not root.exists() and not root.is_relative_to(Path(pack_root).absolute()),
        'pilot_output_scope_invalid')
    with open_directory(root.parent) as fd:
        os.mkdir(root.name,0o700,dir_fd=fd);os.fsync(fd)
    studies={name:prepare_study(pack_root,custodian=custodian,out=root/name,
        project_root=project_root,budget=BUDGETS['U'],seed=seed,execution_profile=name)
        for name in (V2_PROFILE,EVIDENCE_PROFILE)}
    scope=_unseen_descriptor(root,studies)
    _write_exclusive(root/'unseen-scope.json',scope,262144)
    return scope


def load_unseen_inputs(control_root):
    root=Path(control_root).absolute();scope=read_json(root,'unseen-scope.json')
    require(type(scope) is dict and scope.get('control_root')==str(root), 'pilot_unseen_scope_invalid')
    studies={name:load_study(root/name) for name in (V2_PROFILE,EVIDENCE_PROFILE)}
    first=studies[V2_PROFILE]
    require(len(first['tasks'])==9 and sorted(Counter(
        task['project_id'] for task in first['tasks']).values())==[3,3,3]
        and all(study.get('execution_profile')==name and not study.get('regression_custody',False)
            and study['pack_sha256']==first['pack_sha256']
            and study['implementation']==first['implementation']
            and study['custodian']==first['custodian'] and study['budget']==BUDGETS['U']
            and all(task['manifest']['model']==MODEL for task in study['tasks'])
            for name,study in studies.items()),'pilot_profile_mismatch')
    require(scope==_unseen_descriptor(root,studies),'pilot_unseen_scope_drift')
    return scope


def finalize_unseen_pilot(control_root, *, regression_control):
    """Bind the sealed U scope to a verified real R, without granting execution."""
    root=Path(control_root).absolute();scope=load_unseen_inputs(root)
    prior=Path(regression_control).absolute()
    require(not root.is_relative_to(prior) and not prior.is_relative_to(root),
        'pilot_output_scope_invalid')
    receipt=_regression_receipt(prior)
    require(receipt['provenance']=='approved_live','pilot_regression_live_required')
    require(receipt['pack_sha256']!=scope['pack_sha256'],'pilot_unseen_pack_reused')
    studies={name:load_study(root/name) for name in (V2_PROFILE,EVIDENCE_PROFILE)}
    plan=_bind_unseen_scope(_plan(root,studies,'U',None,receipt,20261030),scope)
    try:_write_exclusive(root/'pilot.json',plan,262144)
    except FileExistsError:raise StudyError('pilot_already_finalized') from None
    return plan


def prepare_pilot(pack_root, *, custodian, out, phase, project_root,
                  regression_selection=None, regression_control=None, seed=20261030):
    """Prepare fresh exact scopes; selection/qualification is supplied by custody."""
    require(type(phase) is str and phase in BUDGETS,'pilot_phase_invalid')
    require(type(seed) is int and seed==20261030,'pilot_seed_invalid')
    pack=load_neutral_pack(pack_root)
    root=Path(out).absolute();validate_workspace_path(root,output=True)
    require(not root.exists() and not root.is_relative_to(Path(pack_root).absolute()),
        'pilot_output_scope_invalid')
    prerequisite=None
    if phase=='R':
        require(regression_control is None,'pilot_phase_scope_invalid')
        _selection(regression_selection,{task['id'] for task in pack['tasks']})
        require({task['id'] for task in pack['tasks']}==
            {row['task_id'] for row in regression_selection['rows']},'pilot_pack_grid_invalid')
        _regression_binding(pack_root,pack,regression_selection,custodian)
    else:
        _custodian(custodian)
        require(len(pack['tasks'])==9 and sorted(Counter(
            task['project_id'] for task in pack['tasks']).values())==[3,3,3], 'pilot_pack_grid_invalid')
        require(regression_selection is None and regression_control is not None,
            'pilot_regression_required')
        prior=Path(regression_control).absolute()
        require(not root.is_relative_to(prior) and not prior.is_relative_to(root),
            'pilot_output_scope_invalid')
        prerequisite=_regression_receipt(prior)
        require(prerequisite['pack_sha256']!=digest(pack),'pilot_unseen_pack_reused')
    with open_directory(root.parent) as fd:
        os.mkdir(root.name,0o700,dir_fd=fd);os.fsync(fd)
    names=[V2_PROFILE] if phase=='R' else [V2_PROFILE,EVIDENCE_PROFILE]
    studies={name:prepare_study(pack_root,custodian=custodian,out=root/name,
        project_root=project_root,budget=BUDGETS[phase],seed=seed,execution_profile=name,
        regression_custody=phase=='R')
        for name in names}
    value=_plan(root,studies,phase,regression_selection,prerequisite,seed)
    _write_exclusive(root/'pilot.json',value,262144)
    return value


def load_pilot(control_root):
    root=Path(control_root).absolute();value=read_json(root,'pilot.json')
    require(type(value) is dict and value.get('schema')==SCHEMA and value.get('control_root')==str(root)
        and type(value.get('phase')) is str and value['phase'] in BUDGETS
        and type(value.get('order_seed')) is int and value['order_seed']==20261030,
        'pilot_plan_invalid')
    phase=value['phase'];names=[V2_PROFILE] if phase=='R' else [V2_PROFILE,EVIDENCE_PROFILE]
    studies={name:load_study(root/name) for name in names}
    first=studies[V2_PROFILE]
    require(all(study.get('execution_profile')==name and study.get('remote_enabled') is False
        and study.get('regression_custody',False)==(phase=='R')
        and all(task['manifest']['model']==MODEL for task in study['tasks'])
        and study['pack_sha256']==first['pack_sha256']
        and study['implementation']==first['implementation']
        and study['custodian']==first['custodian']
        and study['budget']==BUDGETS[phase] for name,study in studies.items()), 'pilot_profile_mismatch')
    selection=value.get('regression_selection');prerequisite=None
    if phase=='R':
        _selection(selection,{task['id'] for task in first['tasks']})
        require({task['id'] for task in first['tasks']}==
            {row['task_id'] for row in selection['rows']},'pilot_pack_grid_invalid')
        _regression_binding(first['pack_root'],load_neutral_pack(first['pack_root']),selection,first['custodian'])
    else:
        require(len(first['tasks'])==9 and sorted(Counter(
            task['project_id'] for task in first['tasks']).values())==[3,3,3], 'pilot_pack_grid_invalid')
        require(selection is None,'pilot_phase_scope_invalid')
        receipt=value.get('regression_receipt')
        require(type(receipt) is dict and type(receipt.get('control_root')) is str,
            'pilot_regression_required')
        prerequisite=_regression_receipt(receipt['control_root'])
    expected=_plan(root,studies,phase,selection,prerequisite,20261030)
    if phase=='U' and ((root/'unseen-scope.json').exists() or 'unseen_scope_sha256' in value):
        expected=_bind_unseen_scope(expected,load_unseen_inputs(root))
    require(value==expected,'pilot_plan_drift')
    return value


def propose_pilot_approval(control_root):
    plan=load_pilot(control_root)
    with open_directory(Path(control_root)) as fd:
        require(stat.S_IMODE(os.fstat(fd).st_mode)==0o700,'pilot_log_root_not_private')
    return {'schema':'macr.deepseek.pilot.approval.v1','authorized':False,'confirmation_ref':'',
        'authorization_scope':'one-fresh-phase-no-prior-funds-or-retries',
        'phase':plan['phase'],'control_root':plan['control_root'],'plan_sha256':plan['plan_sha256'],
        'pack_sha256':plan['pack_sha256'],'custodian':plan['custodian'],
        'profiles':plan['profiles'],'cells_sha256':digest(plan['cells']),
        'cell_ids':[cell['cell_id'] for cell in plan['cells']],'sources':plan['sources'],
        'endpoint':ENDPOINT,'model':dict(MODEL),**plan['budget'],
        'price_snapshot':dict(PRICE),'cell_limits':plan['cell_limits'],
        'regression_receipt':plan['regression_receipt'],'concurrent_requests':1,
        'unseen_scope_sha256':plan.get('unseen_scope_sha256'),
        'automatic_retries':0,'resume':False,'ledger_reset':False,
        'log_root':plan['control_root'],'log_policy':dict(LOG_POLICY)}


def _preflight(control_root, approval, *, simulated):
    plan=load_pilot(control_root);expected=propose_pilot_approval(control_root)
    require(type(approval) is dict and approval.get('authorized') is True
        and type(approval.get('confirmation_ref')) is str
        and 0<len(approval['confirmation_ref'].strip())<=128,'pilot_approval_missing')
    require(approval | {'authorized':False,'confirmation_ref':''}==expected,
        'pilot_approval_scope_mismatch')
    if plan['phase']=='U' and not simulated:
        require(plan['regression_receipt']['provenance']=='approved_live',
            'pilot_regression_live_required')
        require(type(plan.get('unseen_scope_sha256')) is str,'pilot_unseen_scope_required')
    if not simulated:
        # Guarded offline fixtures live in artifacts; real logs must be external.
        require(not Path(plan['control_root']).is_relative_to(Path(plan['project_root'])),
            'pilot_live_log_inside_checkout')
    return plan


def preflight_approved_pilot(control_root, approval):
    return _preflight(control_root,approval,simulated=False)


def run_pilot_dryrun(control_root, *, scripted_by_cell):
    """Literal offline replies only; no Key reader or live transport is created."""
    plan=load_pilot(control_root)
    return _execute(plan,scripted_by_cell=scripted_by_cell,key_reader=None,
        provenance='scripted_offline_fixture')


def execute_approved_pilot(control_root, approval, *, enabled=False, key_reader=None,
                           scripted_by_cell=None):
    require(enabled is True,'pilot_live_disabled')
    simulated=scripted_by_cell is not None
    plan=_preflight(control_root,approval,simulated=simulated)
    require(simulated or callable(key_reader),'pilot_key_reader_invalid')
    return _execute(plan,scripted_by_cell=scripted_by_cell,key_reader=key_reader,
        provenance='approved_live_fake' if simulated else 'approved_live')


def _execute(plan, *, scripted_by_cell, key_reader, provenance):
    from macr.evals.deepseek_comparison_report import build_pilot_report, export_pilot_review_packets
    root=Path(plan['control_root']);simulated=provenance!='approved_live'
    scripts={}
    if simulated:
        require(type(scripted_by_cell) is dict and set(scripted_by_cell)==
            {cell['cell_id'] for cell in plan['cells']},'pilot_script_scope_invalid')
        scripts={cell:_ScriptedWire(replies) for cell,replies in scripted_by_cell.items()}
    try:
        _write_exclusive(root/'execution.claim.json',{'plan_sha256':plan['plan_sha256'],
            'provenance':provenance,'state':'claimed_before_credentials_or_dispatch'},4096)
    except FileExistsError:
        raise StudyError('execution_already_claimed') from None
    _write_exclusive(root/'retention.json',{'plan_sha256':plan['plan_sha256'],
        'contents':['source','model_output','reasoning_content','usage','receipts'],**LOG_POLICY},4096)
    (root/'cells').mkdir(mode=0o700);(root/'ledger').mkdir(mode=0o700)
    # Both profiles have the same checked caps/rates; one gate spans the stage.
    gate=_StudyGate(root,{'execution_profile':V2_PROFILE,'budget':plan['budget']},provenance)
    studies={name:load_study(row['control_root']) for name,row in plan['profiles'].items()}
    results={};halted=False;known_failures=0;live_attempts=0;key=None;transports=[]
    try:
        for cell in plan['cells']:
            if halted:continue
            cell_id=cell['cell_id'];gate.cell_id=cell_id;started=monotonic()
            try:
                if not simulated and key is None:
                    try:key=key_reader()
                    except Exception:raise StudyError('pilot_key_input_failed') from None
                    require(type(key) is str and 1<=len(key)<=4096 and key==key.strip()
                        and all(32<ord(c)<127 for c in key),'pilot_key_invalid')
                inner=scripts[cell_id] if simulated else _ApprovedTransport(key)
                transports.append(inner);wire=_BoundWire(inner,simulated=simulated);gate.wire=wire
                study=studies[cell['profile']]
                task=next(task for task in study['tasks'] if task['id']==cell['task_id'])
                result=_run_study_cell_core(Path(cell['study_root'])/'manifests'/cell['task_id'],
                    dataset_root=Path(plan['pack_root']),strategy=cell['strategy'],
                    provider=DeepSeekProvider(model=MODEL['model_id'],transport=wire,allow_remote=False),
                    out=root/'cells'/cell_id,fixed_plan=task['fixed_plan'],
                    admit=gate.admit,settle=gate.settle,provenance=provenance)
                live_attempts+=wire.live_attempts
            except (PolicyRunError,StudyError) as error:
                result={'task_id':cell['task_id'],'strategy':cell['strategy'],'status':'failed',
                    'error_code':error.code,'answer':None,'review_status':'not_ready',
                    'requests_attempted':gate.state(cell_id)['request_count'],'model_calls_live':0,
                    'requests':[],'tool_reads':0,'elapsed_seconds':monotonic()-started,
                    'timing_seconds':{'preparation':monotonic()-started,'provider':0.,'tool':0.}}
            result.update(cell_id=cell_id,accounting=gate.state(cell_id))
            results[cell_id]=result
            _write_exclusive(root/'cells'/f'{cell_id}.accounted.json',result,1048576)
            error=result.get('error_code');valid=result['status'] in {'semantic_pending','ask_user_terminated'}
            known_failure=not valid and error in KNOWN_CONTRACT_ERRORS
            known_failures+=int(known_failure)
            halted=(gate.halted or gate.pending is not None or known_failures>=3
                or (not valid and not known_failure))
        accounting=gate.state() | {'model_calls_live':live_attempts}
        report=build_pilot_report(plan,results,accounting,halted=halted)
        summary=report | {'provenance':provenance,'model_calls_live':live_attempts,
            'promotion_gate':report.get('r_gate'),'known_contract_failures':known_failures}
        _write_exclusive(root/'summary.json',summary,262144)
        packets=export_pilot_review_packets(plan,results)
        _write_exclusive(root/'blind-review-packets.json',{'packets':packets['packets'],
            'blinding_limit':packets['blinding_limit']},1048576)
        _write_exclusive(root/'review-keeper-mapping.json',packets['keeper_mapping'],65536)
        return summary
    finally:
        key=None
        for transport in transports:
            if type(transport) is _ApprovedTransport:transport._api_key=''
