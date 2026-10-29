"""Single-pass scripted study: persistent simulated admission, never live access."""
from __future__ import annotations

from pathlib import Path
from time import monotonic

from macr.evals.deepseek_policy_batch import _KNOWN_CONTRACT_ERRORS
from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_policy_spend import _parse_usd, _usd, _usage_evidence
from macr.evals.deepseek_study_profile import StudyError, load_study, digest
from macr.evals.deepseek_study_runner import run_study_cell
from macr.evals.deepseek_study_generation import get_generation
from macr.providers.base import validate_billing_evidence, validate_identity_evidence

KNOWN_CONTRACT_ERRORS = _KNOWN_CONTRACT_ERRORS | {
    'read_tool_arguments_invalid', 'read_tool_limit_or_forbidden', 'read_tool_budget_exhausted'}


class _FixtureGate:
    def __init__(self, root, study, *, provenance='scripted_offline_fixture'):
        self.root, self.study = root, study
        self.provenance = provenance
        self.rows, self.pending = [], None
        self.halted, self.fingerprint = False, None
        self.cell_id = None
        self.generation = get_generation(study.get('execution_profile'))
        self.reservation = self.generation['reservation_micro_usd']
        self.output_cap = self.generation['max_output_per_request']

    def state(self, cell_id=None):
        rows = [row for row in self.rows if cell_id is None or row['cell_id'] == cell_id]
        spent = sum(row.get('charged_micro_usd', 0) for row in rows)
        held = sum(row['reserved_micro_usd'] for row in rows if row['state'] != 'settled')
        return {'state':'halted_unknown' if self.halted else 'awaiting_outcome' if self.pending else 'open',
            'request_count':len(rows), 'known_accrued_usd':_usd(spent),
            'unknown_held_usd':_usd(held), 'full_miss_sensitivity_usd':_usd(sum(
                ((row['usage']['prompt_tokens']*300000 + row['usage']['completion_tokens']*1200000
                 + 999999)//1000000) for row in rows if row['state']=='settled')),
            'model_calls_live':None if self.provenance=='approved_live' else 0,
            'basis':('provider_usage_peak_local_accrual_not_supplier_bill'
                     if self.provenance=='approved_live' else 'scripted_usage_not_supplier_bill')}

    def admit(self, request_sha256):
        if self.halted or self.pending is not None:
            raise PolicyRunError('study_halted_unknown')
        budget = self.study['budget']
        if len(self.rows) >= budget['max_requests']:
            raise PolicyRunError('study_request_budget_exhausted')
        occupied = sum(row.get('charged_micro_usd',row['reserved_micro_usd']) for row in self.rows)
        if occupied + self.reservation > _parse_usd(budget['max_usd'],code='study_budget_invalid'):
            raise PolicyRunError('study_usd_budget_exhausted')
        row = {'number':len(self.rows)+1,'cell_id':self.cell_id,'request_sha256':request_sha256,
               'reserved_micro_usd':self.reservation,'state':'reserved_unknown','usage':None}
        _write_exclusive(self.root/'ledger'/f"{row['number']:03d}.reservation.json", row,4096)
        self.rows.append(row)
        self.pending = row

    def settle(self, request_sha256, *, response=None, error=None):
        row = self.pending
        if row is None or row['request_sha256'] != request_sha256:
            raise PolicyRunError('study_receipt_binding_invalid')
        raw = None
        if (response is not None and response.model == 'deepseek-flash'
                and response.provider == 'deepseek'):
            raw = response.raw_usage
        elif error is not None and error.billing_evidence is not None:
            try:
                billing = validate_billing_evidence(error.billing_evidence)
                if (billing['request_sha256']==request_sha256 and billing['model']=='deepseek-flash'
                        and billing['provider']=='deepseek'
                        and billing['max_output_tokens']==self.output_cap):
                    raw = billing['raw_usage']
            except ValueError:
                pass
        # Identity and billing are separate envelopes: a content failure may
        # invalidate the assistant while both pieces of wire evidence survive.
        identity = None
        identity_rejected = False
        if error is not None:
            try:
                candidate = error.identity_evidence
                if candidate is not None:
                    candidate = validate_identity_evidence(candidate)
                    if (candidate['request_sha256'] != request_sha256 or
                            candidate['provider'] != 'deepseek' or
                            candidate['requested_model'] != 'deepseek-flash'):
                        raise ValueError()
                    identity = candidate
                    row['identity_evidence'] = candidate
            except (AttributeError, ValueError):
                identity_rejected = True
                row['identity_evidence_rejected'] = True
        evidence = _usage_evidence(raw,self.output_cap)
        if evidence is not None:
            cost, usage = evidence
            row.update(state='settled',charged_micro_usd=cost,usage=usage)
        else:
            row['state']='unknown_reservation_retained'
            self.halted = True
        _write_exclusive(self.root/'ledger'/f"{row['number']:03d}.outcome.json",row,4096)
        self.pending = None
        if self.halted:
            raise PolicyRunError('study_halted_unknown')
        if identity_rejected:
            raise PolicyRunError('study_identity_evidence_invalid')
        if identity is not None:
            if identity['model_state'] == 'mismatch':
                raise PolicyRunError('model_identity_drift')
            if identity['model_state'] != 'validated':
                raise PolicyRunError('model_identity_unknown')
            fingerprint = (identity['system_fingerprint']
                if identity['fingerprint_state'] == 'validated' else None)
        else:
            canonical=response if response is not None else getattr(error,'response',None)
            fingerprint=getattr(canonical,'system_fingerprint',None)
        if not isinstance(fingerprint,str) or not fingerprint.strip():
            raise PolicyRunError('system_fingerprint_unknown')
        if self.fingerprint is not None and fingerprint != self.fingerprint:
            raise PolicyRunError('system_fingerprint_drift')
        self.fingerprint=fingerprint


def run_study(control_root, *, provider_factory):
    """Consume a frozen proposed study once using only trusted fixture transports.

    Exclusive claims remain after errors. This deliberately has no credential,
    HTTP, resume, approval or legacy-ledger migration interface.
    """
    return _run_study_core(control_root,provider_factory=provider_factory,
        cell_runner=run_study_cell,provenance='scripted_offline_fixture')


def _run_study_core(control_root, *, provider_factory, cell_runner,
                    provenance, gate_factory=None, execution_cells=None, report_builder=None):
    """One claimed, sequential pass; no reset, recovery or implicit retry."""
    if provenance not in {'scripted_offline_fixture','approved_live_fake','approved_live'}:
        raise StudyError('study_provenance_invalid')
    root = Path(control_root).absolute()
    study = load_study(root)
    selected=study['cells'] if execution_cells is None else execution_cells
    if execution_cells is not None:
        approved={cell['cell_id']:(index,cell) for index,cell in enumerate(study['cells'])}
        valid=(type(selected) is list and 0<len(selected)<=len(approved)
            and all(type(cell) is dict and type(cell.get('cell_id')) is str
                and cell['cell_id'] in approved and cell==approved[cell['cell_id']][1] for cell in selected))
        indices=[approved[cell['cell_id']][0] for cell in selected] if valid else []
        if not valid or indices!=sorted(set(indices)) or not callable(report_builder):
            raise StudyError('study_execution_subset_invalid')
    try:
        _write_exclusive(root/'execution.claim.json',{'profile_sha256':study['profile_sha256'],
            'state':'claimed_before_dispatch','provenance':provenance},4096)
    except FileExistsError:
        raise StudyError('execution_already_claimed') from None
    (root/'cells').mkdir(mode=0o700)
    (root/'ledger').mkdir(mode=0o700)
    gate = (gate_factory or _FixtureGate)(root,study,provenance=provenance)
    results, rows = {}, []
    live_attempts = 0
    tasks={task['id']:task for task in study['tasks']}
    halted = False
    for cell in selected:
        cell_id=cell['cell_id']
        if halted:
            rows.append(cell|{'status':'not_attempted'})
            continue
        gate.cell_id=cell_id
        task=tasks[cell['task_id']]
        started=monotonic()
        try:
            result=cell_runner(root/'manifests'/cell['task_id'],
                dataset_root=Path(study['pack_root']),strategy=cell['strategy'],
                provider=provider_factory(cell),out=root/'cells'/cell_id,
                fixed_plan=task['fixed_plan'],admit=gate.admit,settle=gate.settle)
        except (PolicyRunError, StudyError) as error:
            result={'task_id':cell['task_id'],'strategy':cell['strategy'],'status':'failed',
                'error_code':error.code,'answer':None,'review_status':'not_ready',
                'requests_attempted':gate.state(cell_id)['request_count'],'model_calls_live':0,
                'requests':[],'tool_reads':0,'elapsed_seconds':monotonic()-started,
                'timing_seconds':{'preparation':monotonic()-started,'provider':0.,'tool':0.}}
        result['accounting']=gate.state(cell_id) | {'model_calls_live':result.get('model_calls_live',0)}
        live_attempts += result.get('model_calls_live',0)
        result['cell_id']=cell_id
        results[cell_id]=result
        _write_exclusive(root/'cells'/f'{cell_id}.accounted.json',result,1048576)
        rows.append(cell|{'status':result['status'],'error_code':result.get('error_code'),
                         'result_sha256':digest(result)})
        error=result.get('error_code')
        halted = (gate.halted or gate.pending is not None or
            (result['status'] not in {'semantic_pending','ask_user_terminated'} and
             error not in KNOWN_CONTRACT_ERRORS))
    from macr.evals.deepseek_study_report import build_study_report
    report=(report_builder or build_study_report)(study,results)
    _write_exclusive(root/'paired-report.json',report,262144)
    summary={'schema':('macr.deepseek.study.dryrun.v1' if provenance=='scripted_offline_fixture'
                      else 'macr.deepseek.study.execution.v1'),
        'profile_sha256':study['profile_sha256'],'provenance':provenance,
        'planned_cells':len(selected),'planned_request_ceiling':len(selected)*3,
        'rows':rows,'accounting':gate.state() | {'model_calls_live':live_attempts},
        'model_calls_live':live_attempts,
        'semantic_scoring':'pending_independent_review','batch_halted':halted}
    if execution_cells is not None:
        summary['original_planned_cells']=len(study['cells'])
    _write_exclusive(root/'summary.json',summary,262144)
    return summary
