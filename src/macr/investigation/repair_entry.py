"""Normal repair CLI; official HTTP requires an explicit bounded spend plan."""
from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
import re
from macr.repair_cli import add_repair_parser as add_parser

_TASK_FIELDS = {'schema','task_id','issue','source_files','editable_paths','test_paths'}


def _task_shape(task):
    from .repair_loop import _require
    _require(type(task) is dict and task.get('schema') in ('macr.repair.task.v1','macr.repair.task.v2','macr.repair.task.v3','macr.repair.task.v4'),
             'neutral_task_contract')
    windowed=task['schema']=='macr.repair.task.v4'
    scoped=task['schema'] in ('macr.repair.task.v3','macr.repair.task.v4')
    selected=task['schema']=='macr.repair.task.v2' or scoped
    fields=(_TASK_FIELDS|{'test_plan'} if selected else _TASK_FIELDS)|({'source_scope'} if scoped else set())
    if windowed: fields|={'source_windows'}
    _require(set(task)==fields, 'neutral_task_contract')
    if scoped: _require(task['source_scope']=='declared_files', 'neutral_task_contract')
    if windowed:
        from .repair_windows import validate_selection_shape
        validate_selection_shape(task['source_windows'],expected_paths=task['source_files'])
    if selected and (not scoped or task['test_plan'] is not None):
        from .repair_test_plan import validate_plan_shape
        validate_plan_shape(task['test_plan'])
    return task





def _json(path):
    from .repair_io import load_json
    from .scope import read_safe, validate_workspace_path
    # Explicit sanitized control inputs may live below owned artifact roots;
    # private/secret ancestry remains forbidden. Never discover configurations.
    path = Path(path).absolute(); validate_workspace_path(path, output=True)
    return load_json(read_safe(path.parent,path.name,32768).decode(),max_bytes=32768,max_depth=12)


def load_task(repo, task_path):
    from . import repair_loop
    from .scope import walk_entries
    task = _task_shape(_json(task_path))
    repair_loop._require(type(task['task_id']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', task['task_id'])
        and type(task['issue']) is str and 0 < len(task['issue'].encode()) <= 2048,
        'neutral_task_contract')
    expected = task['source_files']
    repair_loop._require(type(expected) is dict and 1 <= len(expected) <= 32
        and all(type(n) is str and type(h) is str and re.fullmatch(r'[0-9a-f]{64}',h)
                for n,h in expected.items()), 'source_commitment')
    repo = repair_loop._root(repo)
    # Inspect names only before reading any source content. An unlisted hidden
    # test file cannot silently enter the model's full-source generation view.
    source_scope=task.get('source_scope')
    if source_scope is None:
        inventory = list(walk_entries(repo, repair_loop.PROFILE))
        repair_loop._require({n for n,k,_ in inventory if k == 'file'} == set(expected), 'source_inventory_mismatch')
    windows=task.get('source_windows');full_originals=windows is not None
    files = repair_loop._files(repo,expected,source_scope=source_scope,full_originals=full_originals)
    repair_loop._require(repair_loop._hashes(files) == expected, 'source_digest_mismatch')
    repair_loop.test_manifest(files,task['test_paths'],test_plan=task.get('test_plan'),full_originals=full_originals)
    repair_loop._require(type(task['editable_paths']) is list and 0 < len(task['editable_paths']) <= len(files)
        and all(type(n) is str and n in files for n in task['editable_paths'])
        and len(set(task['editable_paths'])) == len(task['editable_paths'])
        and not set(task['editable_paths']) & set(task['test_paths']), 'edit_authority')
    if full_originals:
        from .repair_windows import project_source_windows
        repair_loop._require(all(path in windows['files'] for path in task['editable_paths']), 'windowed_editable_unshown')
        project_source_windows(files,windows,protected_paths=task['test_paths'])
    return task


def _make_provider(config):
    # The default application path still has no credential reader or transport.
    from macr.providers.repair_binding import RepairProviderBinding
    return RepairProviderBinding(config)


def spend_binding_sha256(task,strategy,config,*,proposal_contract='v2'):
    """Bind neutral data and common limits, independently of an output path."""
    from . import repair_loop as loop
    from macr.providers.repair_binding import validate_config
    _task_shape(task)
    loop._validate_strategy(strategy,proposal_contract)
    return loop.digest({'task':task,'strategy':strategy,
        'configuration_sha256':loop.digest(validate_config(config)),
        'output_contract_sha256':loop.digest(loop.output_contract(proposal_contract))})


def _read_runtime_credential():
    """One temporary terminal input; no discovery, echo fallback or logging."""
    import getpass,sys,warnings
    from macr.providers.base import ProviderError
    value=None;failed=False
    try:
        if not sys.stdin.isatty():failed=True
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error',getpass.GetPassWarning)
                value=getpass.getpass('DeepSeek API Key (hidden): ')
    except (Exception,KeyboardInterrupt):failed=True
    if failed or not (type(value) is str and 0<len(value)<=4096
                      and all(33<=ord(c)<=126 for c in value)):
        value=None
        raise ProviderError('repair_credential_input_unavailable')
    return value


def _make_runtime_provider(config,ledger,*,credential_reader):
    from macr.providers.repair_binding import RepairProviderBinding
    from macr.providers.repair_transport import OfficialRepairTransport
    return RepairProviderBinding(config,spend_ledger=ledger,
        transport=OfficialRepairTransport(credential_reader,allow_remote=True))


def _runtime_plan(args,task,config):
    import time
    from macr.providers.repair_spend import validate_spend_plan
    from . import repair_loop as loop
    from .scope import validate_workspace_path
    plan=validate_spend_plan(_json(args.spend_plan))
    binding=spend_binding_sha256(task,args.strategy,config,proposal_contract=args.proposal_contract)
    loop._require(plan['configuration_sha256']==loop.digest(config)
        and plan['output_contract_sha256']==loop.digest(loop.output_contract(args.proposal_contract))
        and any(c=={'cell_id':args.spend_cell,'binding_sha256':binding} for c in plan['cells']),
        'repair_spend_scope_mismatch')
    loop._require(time.time()<plan['expires_at'],'repair_spend_expired')
    ledger_root=Path(plan['ledger_root']);out=Path(args.out).absolute();repo=Path(args.repo).absolute()
    validate_workspace_path(ledger_root,output=True);validate_workspace_path(out,output=True)
    for a,b in ((ledger_root,out),(ledger_root,repo),(repo,out)):
        loop._require(not(a==b or a.is_relative_to(b) or b.is_relative_to(a)),'repair_spend_output_overlap')
    loop._require(out==out.resolve() and not out.exists(),'output_exists_no_resume')
    loop._root(out.parent)
    return plan,binding


def _run_report(args,task,provider,verifier):
    from . import repair_loop as loop
    return loop.run_repair_loop(args.repo,task['issue'],editable_paths=task['editable_paths'],
        test_paths=task['test_paths'],task_id=task['task_id'],strategy=args.strategy,provider=provider,
        proposal_contract=args.proposal_contract,verifier=verifier,out=args.out,
        expected_source_files=task['source_files'],test_plan=task.get('test_plan'),source_scope=task.get('source_scope'),
        source_windows=task.get('source_windows'))


def _run_durable(args,task,config,plan,binding,state,verifier=None):
    from . import repair_loop as loop
    from macr.providers.repair_spend import RepairSpendLedger
    box={};failure=None;report=None
    try:
        with RepairSpendLedger(Path(plan['ledger_root']),plan,config) as ledger:
            state['global_spend']=ledger.summary()
            try:
                loop._require(not state['global_spend']['stopped'],'repair_spend_stopped')
                ledger.claim_cell(args.spend_cell,binding)
                box['credential']=_read_runtime_credential()
                provider=_make_runtime_provider(config,ledger,credential_reader=lambda:box['credential'])
                state['provider']=provider
                loop._require(provider.config==config,'provider_config_binding')
                report=_run_report(args,task,provider,verifier)
            except BaseException as error:
                failure=error
            finally:
                box.clear()
                current=ledger.summary()
                if ledger.cell_id is not None:
                    status='stopped' if current['stopped'] or isinstance(failure,KeyboardInterrupt) or (
                        report is not None and report['status']=='verification_outcome_unknown') else (
                        'failed' if failure is not None else 'budget_exhausted' if report['status']=='budget_exhausted'
                        else 'completed' if report['status'] in ('public_tests_reported_passed','candidate_unverified','candidate_finalized','candidate_selected_unverified',
                            'no_candidate','insufficient_evidence') else 'failed')
                    ledger.finish_cell(status)
                state['global_spend']=ledger.summary()
        state['spend_status']='reconciled'
    except BaseException:
        state['global_spend']=None;state['spend_status']='unreconciled'
        raise
    finally:box.clear()
    if failure is not None:raise failure
    return report


def _verification_backend():
    # Existing generic/native admission gates are deliberately untouched.
    return None


def initial_context_id(files, issue, *, test_plan=None, source_scope=None, source_windows=None):
    """The common source/issue payload, excluding each strategy's later feedback."""
    from .repair_loop import digest
    if source_windows is not None:
        from .repair_windows import windowed_context_id
        return windowed_context_id(files,digest(issue),source_windows,source_scope=source_scope,test_plan=test_plan)
    value={'files':{p:b.decode() for p,b in sorted(files.items())},'issue':issue}
    if test_plan is not None:
        from .repair_test_plan import validate_plan_shape, validate_test_plan
        validate_plan_shape(test_plan)
        paths=sorted({node.split('::',1)[0] for node in test_plan['nodeids']})
        value['test_plan']=validate_test_plan(files,paths,test_plan)
    if source_scope is not None:
        from .repair_sources import source_view
        value['source_view']=source_view(source_scope)
    return digest(value)


def score_handoff(record_root, plan, cell_id):
    """Reverify the real application record, then produce an UNscored local packet.

    No private scorer file, target code or transport is opened. All current
    adapters are unverified/injected, so this export cannot claim model gain.
    """
    from . import repair_loop as loop
    from macr.evals.repair_comparison import validate_comparison, score_packet, _empty
    report = loop.verify_loop(record_root); root = loop._root(record_root)
    config = loop._read(root,'claim.json')['config']
    plan = validate_comparison(plan)
    cell = next((c for c in plan['cells'] if c['cell_id'] == cell_id),None)
    loop._require(cell is not None and cell['task_id'] == config['task_id']
        and cell['arm'] == config['strategy'], 'score_cell_binding')
    task = next(t for t in plan['spec']['tasks'] if t['task_id'] == config['task_id'])
    windows=config.get('source_windows')
    original = loop._files(root/'sources/0',full_originals=windows is not None)
    model_config = config['provider_config']; limits = model_config['limits']
    proposal_contract = config.get('proposal_contract','v1')
    loop._require(task['issue_sha256'] == loop.digest(config['issue'])
        and task['initial_context_sha256'] == initial_context_id(original,config['issue'],test_plan=config.get('test_plan'),
            source_scope=config.get('source_scope'),source_windows=windows)
        and plan['spec']['model']['configuration_sha256'] == loop.digest(model_config)
        and plan['spec']['model']['output_contract_sha256'] == loop.digest(loop.output_contract(proposal_contract))
        and plan['spec']['limits'] == {'max_requests':limits['max_requests'],
            'input_tokens':limits['max_total_input_tokens'],'output_tokens':limits['max_total_output_tokens'],
            'max_usd':limits['max_usd']}, 'score_shared_contract_binding')
    row = _empty(task,plan['spec']); meter=report['provider_accounting']
    # verify_loop has already rebound actual usage to persisted responses/wires.
    # A failed provider envelope may still carry independently settled usage;
    # preserve that overrun even when its call status is known_provider_failure.
    budget_failure = (meter['input_tokens'] > limits['max_total_input_tokens']
        or meter['output_tokens'] > limits['max_total_output_tokens']
        or Decimal(meter['known_usd']) > Decimal(limits['max_usd'])
        or any(call['usage'] and (
            call['usage']['input_tokens'] > limits['max_input_per_request']
            or call['usage']['output_tokens'] > limits['max_output_per_request'])
            for call in meter['calls']))
    answer = not budget_failure and report['status'] in (
        'simulation_passed','public_tests_reported_passed','candidate_unverified','candidate_finalized','candidate_selected_unverified','no_candidate','insufficient_evidence')
    row['status'] = ('unknown' if report['unknown_request_reservation'] else
        'budget_exceeded' if budget_failure else 'answer_recorded' if answer else
        'verification_unknown' if report['status']=='verification_outcome_unknown' else
        'verification_unavailable' if report['status']=='verification_unavailable' else
        'verification_timeout' if report['status']=='verification_timeout' else
        'verification_failed' if report['status'] in ('verification_failed','verification_invalid') else
        'not_attempted' if meter['requests']==0 else 'contract_failed')
    final_output = None; chain = None; receipt = None
    if meter['requests']:
        row.update(candidate_origin=('provider_unattested' if 'provider_runtime' in config else 'simulation'),verification_origin=(
            'simulation' if report['verification_origin']=='synthetic_replay' else 'unavailable'),
            requests=meter['requests'],input_tokens=report['input_tokens'],output_tokens=report['output_tokens'],
            known_usd=meter['known_usd'],unknown_reserved_usd=meter['unknown_reserved_usd'],
            feedback_validation_attempts=len(report['checks']) if config['strategy'] in ('api_debugger','api_test_feedback') else 0)
        identity=meter['calls'][-1]['identity']
        row['model_identity_sha256']=(loop.digest(identity) if identity
            and identity['model_state']=='validated' and identity['fingerprint_state']=='validated'
            and identity.get('identity_matches_config') is True else None)
        if answer:
            chain = [loop.parse_proposal_content(loop.read_proposal_response(root,n)['content'],
                                                 proposal_contract=proposal_contract)
                     for n in range(1,len(report['rounds'])+1)]
            final_output=chain[-1]
            retained = final_output['status'] in ('candidate','finalize_current','select_candidate')
            row['verdict']='defect' if retained else 'unknown'
            row['final_output_sha256']=loop.digest(final_output)
            if retained: row['candidate_sha256']=report['final_source_id']
        selection=report.get('final_selection')
        if selection is not None:
            row['verification_origin']='unavailable'
            receipt={'source_id':selection['selected_source_id'],'origin':'unavailable',
                'execution_status':'not_performed','selection':selection,
                'historical_receipt_sha256':loop.digest(report['checks'][selection['accepted_round']])}
        else:
            check=report['checks'][-1]
            receipt={key:check[key] for key in ('source_id','spec','origin','outcomes','result')}
        row['verification_receipt_sha256']=loop.digest(receipt)
    elif row['status']=='verification_unavailable':
        row['verification_origin']='unavailable'
    elif row['status'] in ('verification_timeout','verification_unknown','verification_failed'):
        row['verification_origin']='simulation' if report['verification_origin']=='synthetic_replay' else 'unavailable'
        row['feedback_validation_attempts']=len(report['checks'])
        check=report['checks'][-1]
        receipt={key:check[key] for key in ('source_id','spec','origin','outcomes','result')}
        row['verification_receipt_sha256']=loop.digest(receipt)
    phase=report['phase_seconds']
    row['timing_ms']={'preparation':int(phase['preparation']*1000),
        'provider':int(phase['provider']*1000),'verification':int(phase['validation']*1000),
        'total':math.ceil(report['total_seconds']*1000)}
    return score_packet(plan,cell_id,row,original_files=original,
        editable_paths=config['editable_paths'],protected_paths=config['test_paths'],
        final_output=final_output,verification_receipt=receipt,proposal_chain=chain,
        test_plan=config.get('test_plan'),source_scope=config.get('source_scope'),source_windows=windows)


def run(args, *, verifier_factory=None, provider_factory=None, emit=None):
    from macr.providers.base import ProviderError
    from macr.providers.repair_binding import validate_config
    from . import repair_loop
    from .validation import ContractError
    provider = None;verifier=None
    state={'provider':None,'global_spend':None,'spend_status':'unreconciled'}
    remote=getattr(args,'enable_remote',False)
    try:
        complete=remote and getattr(args,'spend_plan',None) is not None and getattr(args,'spend_cell',None) is not None
        requested=remote or getattr(args,'spend_plan',None) is not None or getattr(args,'spend_cell',None) is not None
        repair_loop._validate_strategy(args.strategy,args.proposal_contract)
        repair_loop._require(not requested or (complete and args.proposal_contract in ('v2','v3','v4','v5','v6')),'repair_remote_scope_required')
        task = load_task(args.repo,args.task)
        config = validate_config(_json(args.model_config))
        # Factories are application-owned and side-effect-free. CLI text cannot
        # load plugins or grant a backend; direct self-review never constructs it.
        verifier=None if args.strategy=='direct_api' else (verifier_factory or _verification_backend)()
        if remote:
            plan,binding=_runtime_plan(args,task,config)
            if args.strategy in ('api_debugger','api_test_feedback') and (
                    type(verifier) is not repair_loop.BackendVerifier or not verifier.available()):
                # Missing backends and synthetic replays cannot consume a live
                # cell or read a credential. No capability-probe target run.
                state['spend_status']='not_opened_no_verifier'
                provider=_make_provider(config)
                repair_loop._require(provider.config==config,'provider_config_binding')
                report=_run_report(args,task,provider,None)
            else:
                report=_run_durable(args,task,config,plan,binding,state,verifier);provider=state['provider']
        else:
            provider=(provider_factory or _make_provider)(config)
            repair_loop._require(provider.config==config,'provider_config_binding')
            report=_run_report(args,task,provider,verifier)
        meter = report['provider_accounting']
        summary = {key:report[key] for key in ('status','simulation','proposal_requests','provider_requests',
            'candidate_origin','verification_origin','repaired','target_code_executed','input_tokens','output_tokens')}
        summary.update(known_usd=meter['known_usd'],unknown_reserved_usd=meter['unknown_reserved_usd'],
            task_id=task['task_id'],strategy=args.strategy,remote_enabled=meter['remote_enabled'],
            model_configuration_sha256=meter['configuration_sha256'],
            cost_basis=meter['cost_basis'],total_seconds=report['total_seconds'])
        if report.get('final_selection') is not None:
            summary.update(selected_content_id=report['final_selection']['selected_source_id'],
                           final_verification_status='not_rerun',verification_origin='unavailable',
                           historical_verification_origin=report['verification_origin'])
        if 'source_view' in report: summary['source_view']=report['source_view']
        if 'source_windows' in report:
            summary.update(source_context='partial_files',source_window_contract=report['source_windows']['schema'])
        if remote:summary.update(global_spend=state['global_spend'],spend_status=state['spend_status'])
        if emit is None: print(json.dumps(summary))
        else: emit(summary)
        # No candidate or abstention here is declared a successful real repair.
        return 0 if report['status'] in ('simulation_passed','public_tests_reported_passed','candidate_unverified','candidate_finalized','candidate_selected_unverified',
                                        'no_candidate','insufficient_evidence') else 2
    except (ContractError,ProviderError,OSError,ValueError,TypeError,KeyError,UnicodeError,RuntimeError,EOFError,KeyboardInterrupt) as error:
        provider=provider or state['provider']
        meter = provider.snapshot() if provider is not None else None
        summary={'status':'rejected','error':getattr(error,'code','repair_input_invalid'),
            'provider_requests':meter['requests'] if meter else 0,
            'known_usd':meter['known_usd'] if meter else '0.000000',
            'unknown_reserved_usd':meter['unknown_reserved_usd'] if meter else '0.000000',
            'target_code_executed':None if type(verifier) is repair_loop.BackendVerifier else False,
            'remote_enabled':meter['remote_enabled'] if meter else False}
        if remote:summary.update(global_spend=state['global_spend'],spend_status=state['spend_status'])
        if emit is None: print(json.dumps(summary))
        else: emit(summary)
        return 2
