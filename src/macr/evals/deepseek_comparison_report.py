"""Pure selected-cell pilot accounting; no gold input or automatic semantic score."""
from __future__ import annotations

from copy import deepcopy
from statistics import median

from macr.evals.deepseek_study_generation import V2_PROFILE, EVIDENCE_PROFILE
from macr.evals.deepseek_study_report import (
    REVIEWABLE, _actual, _require, _state, _sum_actual, _usd,
    result_digest)

SCHEMA='macr.deepseek.comparison.report.v1'
PROFILES=(V2_PROFILE,EVIDENCE_PROFILE)


def _selected(plan,results):
    _require(type(plan) is dict and plan.get('phase') in ('R','U')
        and type(plan.get('plan_sha256')) is str and len(plan['plan_sha256'])==64,
        'pilot_report_plan_invalid')
    tasks,cells=plan.get('tasks'),plan.get('cells')
    _require(type(tasks) is list and type(cells) is list
        and len(cells)==(7 if plan['phase']=='R' else 18), 'pilot_report_grid_invalid')
    by_task={}
    for task in tasks:
        _require(type(task) is dict and type(task.get('id')) is str
            and task['id'] not in by_task,'pilot_report_task_invalid')
        by_task[task['id']]=task
    by_cell,pairs={},set()
    for cell in cells:
        _require(type(cell) is dict and type(cell.get('cell_id')) is str
            and cell['cell_id'] not in by_cell and cell.get('task_id') in by_task
            and cell.get('profile') in PROFILES and type(cell.get('strategy')) is str,
            'pilot_report_cell_invalid')
        pair=(cell['task_id'],cell['profile'],cell['strategy'])
        _require(pair not in pairs,'pilot_report_cell_invalid')
        pairs.add(pair);by_cell[cell['cell_id']]=cell
    if plan['phase']=='U':
        _require(len(by_task)==9 and pairs=={(task,p,'adaptive_read_3')
            for task in by_task for p in PROFILES},'pilot_report_grid_invalid')
    else:
        _require(all(c['profile']==V2_PROFILE for c in cells),'pilot_report_grid_invalid')
    _require(type(results) is dict and set(results)<=set(by_cell),'pilot_report_result_unknown')
    for cell_id,result in results.items():
        cell=by_cell[cell_id]
        _require(type(result) is dict and result.get('task_id')==cell['task_id']
            and result.get('strategy')==cell['strategy'],'pilot_report_result_identity')
        result_digest(result)
    return by_task,by_cell


def _identity(result):
    fingerprints=[]
    for request in result.get('requests',[]):
        response=request.get('response')
        if type(response) is not dict:return fingerprints,False
        fingerprint=response.get('system_fingerprint')
        if response.get('model')!='deepseek-flash' or not isinstance(fingerprint,str) or not fingerprint.strip():
            identity=response.get('identity_evidence')
            if (type(identity) is not dict or identity.get('model_state')!='validated'
                    or identity.get('fingerprint_state')!='validated'
                    or identity.get('requested_model')!='deepseek-flash'):
                return fingerprints,False
            fingerprint=identity.get('system_fingerprint')
        if type(fingerprint) is not str or not fingerprint.strip():return fingerprints,False
        fingerprints.append(fingerprint)
    return fingerprints,bool(fingerprints) and len(set(fingerprints))==1


def _length_requests(result):
    count=0
    for request in result.get('requests',[]):
        response=request.get('response')
        if type(response) is not dict:continue
        diagnostic=response.get('failure_diagnostic')
        count+=bool(response.get('finish_reason')=='length' or
            (type(diagnostic) is dict and diagnostic.get('finish_reason')=='length'))
    return count


def build_pilot_report(plan,results,accounting,*,halted=False):
    """Keep the selected denominator; machine promotion is not semantic success."""
    tasks,cells=_selected(plan,results)
    _require(type(accounting) is dict and type(halted) is bool,'pilot_report_accounting_invalid')
    rows=[];fingerprints=set();cell_identities={}
    for cell_id,cell in cells.items():
        result=results.get(cell_id)
        actual=_actual(result)
        state=_state(result,actual,False)
        fps,identity_ok=_identity(result) if result is not None else ([],False)
        cell_identities[cell_id]=set(fps)
        fingerprints.update(fps)
        length=_length_requests(result) if result is not None else 0
        terminal=result is not None and result.get('status') in REVIEWABLE|{'failed','remote_outcome_unknown'}
        operational=(result is not None and (not identity_ok
            or actual['requests_with_unknown_usage']>0 or state in ('unknown','failed')
            or actual['known_accrued_usd'] is None or actual['unknown_reserved_usd'] is None))
        rows.append({'cell_id':cell_id,'task_id':cell['task_id'],'profile':cell['profile'],
            'strategy':cell['strategy'],'state':state,
            'status':result['status'] if result is not None else 'not_attempted',
            'result_sha256':result_digest(result) if result is not None else None,
            'error_code':None if result is None else result.get('error_code'),
            'terminal':terminal,'identity_verified':identity_ok,'operational_anomaly':operational,
            'length_request_count':length,'truncated':length>0,'actual':actual})
    profiles={}
    for profile in dict.fromkeys(cell['profile'] for cell in cells.values()):
        selected=[r for r in rows if r['profile']==profile]
        elapsed=[r['actual']['elapsed_seconds'] for r in selected if r['cell_id'] in results]
        profiles[profile]={'planned_cells':len(selected),
            'counts':{state:sum(r['state']==state for r in selected)
                for state in ('review_pending','contract_failed','unknown','not_attempted','failed')},
            'actual':_sum_actual(selected),'latency_seconds':{'count':len(elapsed),
                'median':median(elapsed) if elapsed else None,
                'min':min(elapsed) if elapsed else None,'max':max(elapsed) if elapsed else None}}
    count=accounting.get('request_count')
    held=_usd(accounting.get('unknown_held_usd'))
    known=_usd(accounting.get('known_accrued_usd'))
    totals=_sum_actual(rows)
    metering=(type(count) is not int or count!=sum(r['actual']['requests'] for r in rows)
        or held is None or known is None or held>0
        or known!=_usd(totals['known_accrued_usd'])
        or held!=_usd(totals['unknown_reserved_usd'])
        or accounting.get('state')!='open')
    anomaly=metering or len(fingerprints)>1 or any(r['operational_anomaly'] for r in rows)
    reviewable=sum(r['state']=='review_pending' for r in rows)
    terminals=sum(r['terminal'] for r in rows)
    truncated=sum(r['truncated'] for r in rows)
    r_gate=None
    if plan['phase']=='R':
        r_gate={'eligible':terminals==7 and reviewable>=5 and truncated<=2 and not halted and not anomaly,
            'terminal_cells':terminals,'reviewable_cells':reviewable,'truncated_cells':truncated,
            'operational_anomaly':bool(anomaly),
            'reason':'machine_terminal_contract_only_not_semantic_quality'}
    pairs=[]
    if plan['phase']=='U':
        for task_id in tasks:
            pair=[r for r in rows if r['task_id']==task_id]
            incomparable=any(r['state'] in ('not_attempted','unknown','failed')
                or r['operational_anomaly'] for r in pair) or len(set().union(
                    *(cell_identities[r['cell_id']] for r in pair)))>1
            pairs.append({'task_id':task_id,'cells':{r['profile']:
                {'cell_id':r['cell_id'],'state':r['state']} for r in pair},
                'outcome':'not_comparable' if incomparable else 'pending',
                'contains_known_contract_failure':any(r['state']=='contract_failed' for r in pair)})
    return {'schema':SCHEMA,'phase':plan['phase'],'plan_sha256':plan['plan_sha256'],
        'planned_cells':len(cells),'processed_cells':len(results),'rows':rows,
        'profiles':profiles,'accounting':deepcopy(accounting),'batch_halted':halted,
        'semantic_scoring':'pending_independent_review','pairs':pairs,'r_gate':r_gate,
        'length_requests':{'numerator':sum(r['length_request_count'] for r in rows),
            'denominator':count},'truncated_cells':{'numerator':truncated,'denominator':len(cells)}}


def export_pilot_review_packets(plan,results):
    """Export only evidence actually supplied; keeper_mapping must stay separate."""
    tasks,cells=_selected(plan,results)
    packets,mapping=[],{}
    for cell_id,cell in cells.items():
        result=results.get(cell_id)
        if result is None or result.get('status') not in REVIEWABLE:continue
        task=tasks[cell['task_id']];digest=result_digest(result)
        review_id=result_digest({'plan':plan['plan_sha256'],'cell':cell_id,'result':digest})[:32]
        evidence=result.get('review_evidence',{})
        _require(type(evidence) is dict,'review_source_invalid')
        source=result.get('source',result.get('visible_source',evidence.get('source',[])))
        _require(type(source) is list,'review_source_invalid')
        safe=[]
        for snippet in source:
            _require(type(snippet) is dict and {'file','start','end','body'}<=set(snippet),
                'review_source_invalid')
            safe.append({key:snippet[key] for key in ('file','start','end','body')})
        packets.append(deepcopy({'review_id':review_id,'result_sha256':digest,
            'question':task.get('question'),'scenario':task.get('scenario'),
            'assumptions':task.get('assumptions',[]),'source':safe,
            'answer':result.get('answer'),'first_answer':result.get('first_answer'),'gap':result.get('gap')}))
        mapping[review_id]=cell_id
    return {'packets':packets,'keeper_mapping':mapping,
        'blinding_limit':'Content may reveal the version or strategy; labels, model and accounting are hidden.'}
