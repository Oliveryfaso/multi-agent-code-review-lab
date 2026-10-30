"""Reproduce the proposed four-strategy contract using known public fixtures only."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'src'))

from macr.evals.deepseek_study_profile import StudyError, prepare_study, PACK_SCHEMA, validate_budget
from macr.evals.deepseek_study_batch import run_study
from macr.evals.deepseek_study_generation import V2_PROFILE, EVIDENCE_PROFILE
from macr.evals.deepseek_policy_runner import _write_exclusive
from macr.investigation.scope import validate_workspace_path
from macr.providers.deepseek import DeepSeekProvider

UNKNOWN = {'verdict':'unknown','cause':None,'when':None,'refs':[],
           'unknowns':['Contract fixture only; independent semantic review has not occurred.']}


class ScriptedTransport:
    offline_fixture = True

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        wire=json.loads(body)
        if 'tools' in wire:
            message={'role':'assistant','content':None,'reasoning_content':'Fixture terminal action.',
                'tool_calls':[{'id':'fixture-finish','type':'function','function':{
                    'name':'finish','arguments':json.dumps({'diagnosis':UNKNOWN})}}]}
            finish='tool_calls'
        else:
            message={'role':'assistant','content':json.dumps(UNKNOWN),
                     'reasoning_content':'Known scripted contract fixture.'}
            finish='stop'
        return {'model':'deepseek-flash','system_fingerprint':'scripted-study-v1',
            'choices':[{'message':message,'finish_reason':finish}],
            'usage':{'prompt_tokens':12,'completion_tokens':8,'prompt_cache_hit_tokens':0,
                'prompt_cache_miss_tokens':12,'completion_tokens_details':{'reasoning_tokens':2}}}


def write_development_pack(root):
    """Repeated known mechanism intentionally avoids inventing an unseen benchmark."""
    root.mkdir(mode=0o700)
    tasks=[]
    for project in range(3):
        for variant in range(3):
            name=f'dev{project}{variant}'
            snapshot=f'snapshots/{name}'
            path=root/snapshot
            path.mkdir(parents=True)
            (path/'entry.py').write_text('from helper import double\ndef run(x):\n    return double(x)\n',encoding='utf-8')
            (path/'helper.py').write_text('def double(x):\n    return x * 2\n',encoding='utf-8')
            tasks.append({'id':name,'project_id':f'fixture{project}','snapshot_root':snapshot,
                'question':'Describe this known fixture with source support.',
                'scenario':'Public contract exercise, not a blind evaluation.',
                'assumptions':['x is an integer.'],'authorized_files':['entry.py','helper.py'],
                'initial_evidence_files':['entry.py','helper.py'] if variant==0 else ['entry.py'],
                'entrypoint':{'file':'entry.py','symbol':'run'},
                'license':{'spdx':'CC0-1.0','provenance':'Self-authored public development fixture.'}})
    _write_exclusive(root/'pack.json',{'schema':PACK_SCHEMA,
        'pack_id':'known-contract-fixture-v1','tasks':tasks},65536)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--max-requests',type=int,default=108)
    parser.add_argument('--max-usd',default='2.000000',help='Simulated local planning budget only.')
    parser.add_argument('--profile',choices=[V2_PROFILE,EVIDENCE_PROFILE],default=None,
        help='Explicit offline generation/evidence profile; omission preserves the original contract.')
    args=parser.parse_args(argv)
    out=args.out.absolute()
    validate_workspace_path(out,output=True)
    if out.is_relative_to(PROJECT) or out.exists():
        raise StudyError('study_output_scope_invalid_or_exists')
    budget=validate_budget({'max_requests':args.max_requests,'max_usd':args.max_usd})
    out.mkdir(mode=0o700)
    write_development_pack(out/'pack')
    prepare_study(out/'pack',custodian={'schema':'macr.deepseek.study.custodian.v1',
        'version':'public-fixture-only','gold_sha256':'a'*64,'rubric_sha256':'b'*64,
        'qualification_statement':'Dummy commitment; no real gold or unseen eligibility claimed.'},
        out=out/'control',project_root=PROJECT,budget=budget,execution_profile=args.profile)
    summary=run_study(out/'control',provider_factory=lambda _:DeepSeekProvider(
        model='deepseek-flash',transport=ScriptedTransport()))
    print(json.dumps({'planned_cells':summary['planned_cells'],
        'scripted_requests':summary['accounting']['request_count'],'model_calls_live':0,
        'batch_halted':summary['batch_halted'],'semantic_scoring':summary['semantic_scoring']}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
