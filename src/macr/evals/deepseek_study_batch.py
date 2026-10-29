"""Single-pass scripted study: persistent simulated admission, never live access."""
from __future__ import annotations

from pathlib import Path
from time import monotonic

from macr.evals.deepseek_policy_batch import _KNOWN_CONTRACT_ERRORS
from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_policy_spend import _parse_usd, _usd, _usage_evidence
from macr.evals.deepseek_study_profile import StudyError, load_study, digest
from macr.evals.deepseek_study_runner import run_study_cell
from macr.providers.base import validate_billing_evidence

KNOWN_CONTRACT_ERRORS = _KNOWN_CONTRACT_ERRORS | {
    'read_tool_arguments_invalid', 'read_tool_limit_or_forbidden', 'read_tool_budget_exhausted'}


class _FixtureGate:
    def __init__(self, root, study):
        self.root, self.study = root, study
        self.rows, self.pending = [], None
        self.halted, self.fingerprint = False, None
        self.cell_id = None

    def state(self, cell_id=None):
        rows = [row for row in self.rows if cell_id is None or row['cell_id'] == cell_id]
        spent = sum(row.get('charged_micro_usd', 0) for row in rows)
        held = 330000 * sum(row['state'] != 'settled' for row in rows)
        return {'state':'halted_unknown' if self.halted else 'awaiting_outcome' if self.pending else 'open',
            'request_count':len(rows), 'known_accrued_usd':_usd(spent),
            'unknown_held_usd':_usd(held), 'full_miss_sensitivity_usd':_usd(sum(
                ((row['usage']['prompt_tokens']*300000 + row['usage']['completion_tokens']*1200000
                 + 999999)//1000000) for row in rows if row['state']=='settled')),
            'model_calls_live':0, 'basis':'scripted_usage_not_supplier_bill'}

    def admit(self, request_sha256):
        if self.halted or self.pending is not None:
            raise PolicyRunError('study_halted_unknown')
        budget = self.study['budget']
        if len(self.rows) >= budget['max_requests']:
            raise PolicyRunError('study_request_budget_exhausted')
        occupied = sum(row.get('charged_micro_usd',330000) for row in self.rows)
        if occupied + 330000 > _parse_usd(budget['max_usd'],code='study_budget_invalid'):
            raise PolicyRunError('study_usd_budget_exhausted')
        row = {'number':len(self.rows)+1,'cell_id':self.cell_id,'request_sha256':request_sha256,
               'reserved_micro_usd':330000,'state':'reserved_unknown','usage':None}
        _write_exclusive(self.root/'ledger'/f"{row['number']:03d}.reservation.json", row,4096)
        self.rows.append(row)
        self.pending = row

    def settle(self, request_sha256, *, response=None, error=None):
        row = self.pending
        if row is None or row['request_sha256'] != request_sha256:
            raise PolicyRunError('study_receipt_binding_invalid')
        raw = None
        if response is not None and response.model == 'deepseek-flash':
            raw = response.raw_usage
        elif error is not None and error.billing_evidence is not None:
            try:
                billing = validate_billing_evidence(error.billing_evidence)
                if (billing['request_sha256']==request_sha256 and billing['model']=='deepseek-flash'
                        and billing['max_output_tokens']==2000):
                    raw = billing['raw_usage']
            except ValueError:
                pass
        evidence = _usage_evidence(raw,2000)
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
        identity=response if response is not None else getattr(error,'response',None)
        fingerprint=getattr(identity,'system_fingerprint',None)
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
    root = Path(control_root).absolute()
    study = load_study(root)
    try:
        _write_exclusive(root/'execution.claim.json',{'profile_sha256':study['profile_sha256'],
            'state':'claimed_before_dispatch','provenance':'scripted_offline_fixture'},4096)
    except FileExistsError:
        raise StudyError('execution_already_claimed') from None
    (root/'cells').mkdir(mode=0o700)
    (root/'ledger').mkdir(mode=0o700)
    gate = _FixtureGate(root,study)
    results, rows = {}, []
    tasks={task['id']:task for task in study['tasks']}
    halted = False
    for cell in study['cells']:
        cell_id=cell['cell_id']
        if halted:
            rows.append(cell|{'status':'not_attempted'})
            continue
        gate.cell_id=cell_id
        task=tasks[cell['task_id']]
        started=monotonic()
        try:
            result=run_study_cell(root/'manifests'/cell['task_id'],
                dataset_root=Path(study['pack_root']),strategy=cell['strategy'],
                provider=provider_factory(cell),out=root/'cells'/cell_id,
                fixed_plan=task['fixed_plan'],admit=gate.admit,settle=gate.settle)
        except (PolicyRunError, StudyError) as error:
            result={'task_id':cell['task_id'],'strategy':cell['strategy'],'status':'failed',
                'error_code':error.code,'answer':None,'review_status':'not_ready',
                'requests_attempted':gate.state(cell_id)['request_count'],'model_calls_live':0,
                'requests':[],'tool_reads':0,'elapsed_seconds':monotonic()-started,
                'timing_seconds':{'preparation':monotonic()-started,'provider':0.,'tool':0.}}
        result['accounting']=gate.state(cell_id)
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
    report=build_study_report(study,results)
    _write_exclusive(root/'paired-report.json',report,262144)
    summary={'schema':'macr.deepseek.study.dryrun.v1','profile_sha256':study['profile_sha256'],
        'planned_cells':len(study['cells']),'planned_request_ceiling':study['planned_request_ceiling'],
        'rows':rows,'accounting':gate.state(),'model_calls_live':0,
        'semantic_scoring':'pending_independent_review','batch_halted':halted}
    _write_exclusive(root/'summary.json',summary,262144)
    return summary
