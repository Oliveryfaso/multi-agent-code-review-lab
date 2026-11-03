"""Normal repair CLI; official HTTP requires an explicit bounded spend plan."""
from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
import re

_TASK_FIELDS = {'schema','task_id','issue','source_files','editable_paths','test_paths'}


def add_parser(subparsers):
    parser = subparsers.add_parser('repair', help='Bounded source repair; remote transport disabled, missing validation stops honestly.')
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--task', required=True, type=Path, help='Strict neutral task JSON; never a scoring or private-answer path.')
    parser.add_argument('--model-config', required=True, type=Path, help='Nonsecret shared model, limits and declared price schedule.')
    parser.add_argument('--out', required=True, type=Path, help='New exclusive local record directory.')
    parser.add_argument('--strategy', choices=('direct_api','api_debugger'), default='api_debugger')
    parser.add_argument('--proposal-contract', choices=('v1','v2'), default='v1',
                        help='v2 explicitly allows retaining the current candidate; legacy v1 is unchanged.')
    parser.add_argument('--enable-remote',action='store_true',help='Explicit official HTTP mode; requires v2 and a durable spend plan/cell.')
    parser.add_argument('--spend-plan',type=Path,help='Nonsecret frozen spend plan; never a credential file.')
    parser.add_argument('--spend-cell',help='One exact planned cell; replay in another output directory is forbidden.')


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
    task = _json(task_path)
    repair_loop._require(type(task) is dict and set(task) == _TASK_FIELDS
        and task['schema'] == 'macr.repair.task.v1'
        and type(task['task_id']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', task['task_id'])
        and type(task['issue']) is str and 0 < len(task['issue'].encode()) <= 2048,
        'neutral_task_contract')
    expected = task['source_files']
    repair_loop._require(type(expected) is dict and 1 <= len(expected) <= 32
        and all(type(n) is str and type(h) is str and re.fullmatch(r'[0-9a-f]{64}',h)
                for n,h in expected.items()), 'source_commitment')
    repo = repair_loop._root(repo)
    # Inspect names only before reading any source content. An unlisted hidden
    # test file cannot silently enter the model's full-source generation view.
    inventory = list(walk_entries(repo, repair_loop.PROFILE))
    repair_loop._require({n for n,k,_ in inventory if k == 'file'} == set(expected), 'source_inventory_mismatch')
    files = repair_loop._files(repo)
    repair_loop._require(repair_loop._hashes(files) == expected, 'source_digest_mismatch')
    repair_loop.test_manifest(files,task['test_paths'])
    repair_loop._require(type(task['editable_paths']) is list and 0 < len(task['editable_paths']) <= len(files)
        and all(type(n) is str and n in files for n in task['editable_paths'])
        and len(set(task['editable_paths'])) == len(task['editable_paths'])
        and not set(task['editable_paths']) & set(task['test_paths']), 'edit_authority')
    return task


def _make_provider(config):
    # The default application path still has no credential reader or transport.
    from macr.providers.repair_binding import RepairProviderBinding
    return RepairProviderBinding(config)


def spend_binding_sha256(task,strategy,config,*,proposal_contract='v2'):
    """Bind neutral data and common limits, independently of an output path."""
    from . import repair_loop as loop
    from macr.providers.repair_binding import validate_config
    loop._require(type(task) is dict and set(task)==_TASK_FIELDS,'neutral_task_contract')
    loop._require(strategy in ('direct_api','api_debugger'),'strategy')
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
        expected_source_files=task['source_files'])


def _run_durable(args,task,config,plan,binding,state):
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
                report=_run_report(args,task,provider,None)
            except BaseException as error:
                failure=error
            finally:
                box.clear()
                current=ledger.summary()
                if ledger.cell_id is not None:
                    status='stopped' if current['stopped'] or isinstance(failure,KeyboardInterrupt) else (
                        'failed' if failure is not None else 'budget_exhausted' if report['status']=='budget_exhausted'
                        else 'completed' if report['status'] in ('candidate_unverified','candidate_finalized',
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


def initial_context_id(files, issue):
    """The common source/issue payload, excluding each strategy's later feedback."""
    from .repair_loop import digest
    return digest({'files':{p:b.decode() for p,b in sorted(files.items())},'issue':issue})


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
    original = loop._files(root/'sources/0')
    model_config = config['provider_config']; limits = model_config['limits']
    proposal_contract = config.get('proposal_contract','v1')
    loop._require(task['issue_sha256'] == loop.digest(config['issue'])
        and task['initial_context_sha256'] == initial_context_id(original,config['issue'])
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
        'simulation_passed','candidate_unverified','candidate_finalized','no_candidate','insufficient_evidence')
    row['status'] = ('unknown' if report['unknown_request_reservation'] else
        'budget_exceeded' if budget_failure else 'answer_recorded' if answer else
        'verification_unavailable' if report['status']=='verification_unavailable' else
        'verification_timeout' if report['status']=='verification_timeout' else
        'not_attempted' if meter['requests']==0 else 'contract_failed')
    final_output = None; chain = None; receipt = None
    if meter['requests']:
        row.update(candidate_origin=('provider_unattested' if 'provider_runtime' in config else 'simulation'),verification_origin=(
            'simulation' if report['verification_origin']=='synthetic_replay' else 'unavailable'),
            requests=meter['requests'],input_tokens=report['input_tokens'],output_tokens=report['output_tokens'],
            known_usd=meter['known_usd'],unknown_reserved_usd=meter['unknown_reserved_usd'],
            feedback_validation_attempts=len(report['checks']) if config['strategy']=='api_debugger' else 0)
        identity=meter['calls'][-1]['identity']
        row['model_identity_sha256']=(loop.digest(identity) if identity
            and identity['model_state']=='validated' and identity['fingerprint_state']=='validated'
            and identity.get('identity_matches_config') is True else None)
        if answer:
            chain = [loop.parse_proposal_content(loop.read_proposal_response(root,n)['content'],
                                                 proposal_contract=proposal_contract)
                     for n in range(1,len(report['rounds'])+1)]
            final_output=chain[-1]
            retained = final_output['status'] in ('candidate','finalize_current')
            row['verdict']='defect' if retained else 'unknown'
            row['final_output_sha256']=loop.digest(final_output)
            if retained: row['candidate_sha256']=report['final_source_id']
        check=report['checks'][-1]
        receipt={key:check[key] for key in ('source_id','spec','origin','outcomes','result')}
        row['verification_receipt_sha256']=loop.digest(receipt)
    elif row['status']=='verification_unavailable':
        row['verification_origin']='unavailable'
    elif row['status']=='verification_timeout':
        row['verification_origin']='simulation'
        row['feedback_validation_attempts']=1
        check=report['checks'][-1]
        receipt={key:check[key] for key in ('source_id','spec','origin','outcomes','result')}
        row['verification_receipt_sha256']=loop.digest(receipt)
    phase=report['phase_seconds']
    row['timing_ms']={'preparation':int(phase['preparation']*1000),
        'provider':int(phase['provider']*1000),'verification':int(phase['validation']*1000),
        'total':math.ceil(report['total_seconds']*1000)}
    return score_packet(plan,cell_id,row,original_files=original,
        editable_paths=config['editable_paths'],protected_paths=config['test_paths'],
        final_output=final_output,verification_receipt=receipt,proposal_chain=chain)


def run(args):
    from macr.providers.base import ProviderError
    from macr.providers.repair_binding import validate_config
    from . import repair_loop
    from .validation import ContractError
    provider = None
    state={'provider':None,'global_spend':None,'spend_status':'unreconciled'}
    remote=getattr(args,'enable_remote',False)
    try:
        complete=remote and getattr(args,'spend_plan',None) is not None and getattr(args,'spend_cell',None) is not None
        requested=remote or getattr(args,'spend_plan',None) is not None or getattr(args,'spend_cell',None) is not None
        repair_loop._require(not requested or (complete and args.proposal_contract=='v2'),'repair_remote_scope_required')
        task = load_task(args.repo,args.task)
        config = validate_config(_json(args.model_config))
        if remote:
            plan,binding=_runtime_plan(args,task,config)
            if args.strategy=='api_debugger':
                # No supported real verifier exists. A synthetic replay is not
                # authority to consume a live cell or read a credential.
                state['spend_status']='not_opened_no_verifier'
                provider=_make_provider(config)
                repair_loop._require(provider.config==config,'provider_config_binding')
                report=_run_report(args,task,provider,None)
            else:
                report=_run_durable(args,task,config,plan,binding,state);provider=state['provider']
        else:
            provider=_make_provider(config)
            repair_loop._require(provider.config==config,'provider_config_binding')
            report=_run_report(args,task,provider,_verification_backend())
        meter = report['provider_accounting']
        summary = {key:report[key] for key in ('status','simulation','proposal_requests','provider_requests',
            'candidate_origin','verification_origin','repaired','target_code_executed','input_tokens','output_tokens')}
        summary.update(known_usd=meter['known_usd'],unknown_reserved_usd=meter['unknown_reserved_usd'],
            task_id=task['task_id'],strategy=args.strategy,remote_enabled=meter['remote_enabled'],
            model_configuration_sha256=meter['configuration_sha256'],
            cost_basis=meter['cost_basis'],total_seconds=report['total_seconds'])
        if remote:summary.update(global_spend=state['global_spend'],spend_status=state['spend_status'])
        print(json.dumps(summary))
        # No candidate or abstention here is declared a successful real repair.
        return 0 if report['status'] in ('simulation_passed','candidate_unverified','candidate_finalized',
                                        'no_candidate','insufficient_evidence') else 2
    except (ContractError,ProviderError,OSError,ValueError,TypeError,KeyError,UnicodeError,RuntimeError,EOFError,KeyboardInterrupt) as error:
        provider=provider or state['provider']
        meter = provider.snapshot() if provider is not None else None
        summary={'status':'rejected','error':getattr(error,'code','repair_input_invalid'),
            'provider_requests':meter['requests'] if meter else 0,
            'known_usd':meter['known_usd'] if meter else '0.000000',
            'unknown_reserved_usd':meter['unknown_reserved_usd'] if meter else '0.000000',
            'target_code_executed':False,'remote_enabled':meter['remote_enabled'] if meter else False}
        if remote:summary.update(global_spend=state['global_spend'],spend_status=state['spend_status'])
        print(json.dumps(summary))
        return 2
