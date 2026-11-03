"""Normal CLI boundary for a neutral, bounded repair task. No live transport."""
from __future__ import annotations

import json
import math
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
    # No Key lookup, socket transport or remote enable flag is reachable here.
    from macr.providers.repair_binding import RepairProviderBinding
    return RepairProviderBinding(config)


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
    loop._require(task['issue_sha256'] == loop.digest(config['issue'])
        and task['initial_context_sha256'] == initial_context_id(original,config['issue'])
        and plan['spec']['model']['configuration_sha256'] == loop.digest(model_config)
        and plan['spec']['model']['output_contract_sha256'] == loop.digest(loop.OUTPUT_CONTRACT)
        and plan['spec']['limits'] == {'max_requests':limits['max_requests'],
            'input_tokens':limits['max_total_input_tokens'],'output_tokens':limits['max_total_output_tokens'],
            'max_usd':limits['max_usd']}, 'score_shared_contract_binding')
    row = _empty(task,plan['spec']); meter=report['provider_accounting']
    answer = report['status'] in ('simulation_passed','candidate_unverified','no_candidate','insufficient_evidence')
    row['status'] = ('answer_recorded' if answer else 'unknown' if report['unknown_request_reservation'] else
        'verification_unavailable' if report['status']=='verification_unavailable' else
        'verification_timeout' if report['status']=='verification_timeout' else
        'not_attempted' if meter['requests']==0 else 'contract_failed')
    final_output = None; chain = None; receipt = None
    if meter['requests']:
        row.update(candidate_origin='simulation',verification_origin=(
            'simulation' if report['verification_origin']=='synthetic_replay' else 'unavailable'),
            requests=meter['requests'],input_tokens=report['input_tokens'],output_tokens=report['output_tokens'],
            known_usd=meter['known_usd'],unknown_reserved_usd=meter['unknown_reserved_usd'],
            feedback_validation_attempts=len(report['checks']) if config['strategy']=='api_debugger' else 0)
        identity=meter['calls'][-1]['identity']
        row['model_identity_sha256']=(loop.digest(identity) if identity
            and identity['model_state']=='validated' and identity['fingerprint_state']=='validated'
            and identity.get('identity_matches_config') is True else None)
        if answer:
            chain = [loop.load_json(loop._read(root,f'rounds/{n}/response.json')['content'],
                    max_bytes=65536,max_depth=8) for n in range(1,len(report['rounds'])+1)]
            final_output=chain[-1]
            row['verdict']='defect' if final_output['status']=='candidate' else 'unknown'
            row['final_output_sha256']=loop.digest(final_output)
            if final_output['status']=='candidate': row['candidate_sha256']=report['final_source_id']
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
    try:
        task = load_task(args.repo,args.task)
        config = validate_config(_json(args.model_config))
        provider = _make_provider(config)
        repair_loop._require(provider.config == config, 'provider_config_binding')
        report = repair_loop.run_repair_loop(args.repo,task['issue'],
            editable_paths=task['editable_paths'],test_paths=task['test_paths'],
            task_id=task['task_id'],strategy=args.strategy,provider=provider,
            verifier=_verification_backend(),out=args.out,expected_source_files=task['source_files'])
        meter = report['provider_accounting']
        summary = {key:report[key] for key in ('status','simulation','proposal_requests','provider_requests',
            'candidate_origin','verification_origin','repaired','target_code_executed','input_tokens','output_tokens')}
        summary.update(known_usd=meter['known_usd'],unknown_reserved_usd=meter['unknown_reserved_usd'],
            task_id=task['task_id'],strategy=args.strategy,remote_enabled=False,
            model_configuration_sha256=meter['configuration_sha256'],
            cost_basis=meter['cost_basis'],total_seconds=report['total_seconds'])
        print(json.dumps(summary))
        # No candidate or abstention here is declared a successful real repair.
        return 0 if report['status'] in ('simulation_passed','candidate_unverified','no_candidate','insufficient_evidence') else 2
    except (ContractError,ProviderError,OSError,ValueError,TypeError,KeyError,UnicodeError) as error:
        meter = provider.snapshot() if provider is not None else None
        print(json.dumps({'status':'rejected','error':getattr(error,'code','repair_input_invalid'),
            'provider_requests':meter['requests'] if meter else 0,
            'known_usd':meter['known_usd'] if meter else '0.000000',
            'unknown_reserved_usd':meter['unknown_reserved_usd'] if meter else '0.000000',
            'target_code_executed':False,'remote_enabled':False}))
        return 2
