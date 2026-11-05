"""Local issue-to-source proposal; never grants edits or dispatches a provider."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time

from .index import RepositoryIndex
from .records import ScopeProfile
from .repair_io import _root, encode_bounded, safe_write
from .scope import TEXT_SUFFIXES, RepositoryScope, paths_overlap, read_safe, safe_parent, validate_workspace_path
from .snapshots import SnapshotStore
from .validation import ContractError
from macr.repair_cli import add_localize_parser as add_parser

JSON_LIMIT=2*1024*1024





def discovery_profile(excludes):
    if not (type(excludes) is list and len(excludes)<=32 and all(type(p) is str
            and 0<len(p.encode('utf-8'))<=256 and not any(c in p for c in '\x00\r\n') for p in excludes)):
        raise ContractError('repair_localize_invalid','exclusions')
    # Scope and snapshot inventory use the same pre-read exclusions. Filtering
    # after inventory would already have read JSON/config/Markdown contents.
    non_python=['*.'+''.join('['+c.lower()+c.upper()+']' for c in suffix[1:])
                for suffix in sorted(TEXT_SUFFIXES-{'.py'})]
    return ScopeProfile(profile_id='repair-locate-python',version='1',max_python_files=256,
        max_python_lines=65536,max_text_bytes=4*1024*1024,max_file_bytes=512*1024,
        exclude_patterns=non_python+['*.pY','*.Py','*.PY']+excludes)


def run(args):
    started=time.monotonic()
    try:
        issue_path=Path(args.issue_file).absolute();validate_workspace_path(issue_path,output=True)
        raw=read_safe(issue_path.parent,issue_path.name,2048)
        issue=raw.decode('utf-8')
        if not issue.strip() or '\x00' in issue:
            raise ContractError('repair_localize_invalid','issue')
        if type(args.max_candidates) is not int or not 1<=args.max_candidates<=32:
            raise ContractError('repair_localize_invalid','candidate_limit')
        profile=discovery_profile(args.exclude)
        repo=_root(args.repo);out=Path(args.out).absolute();validate_workspace_path(out,output=True)
        if out!=out.resolve() or paths_overlap(out,repo) or paths_overlap(out,issue_path.resolve()):
            raise ContractError('repair_localize_invalid','output_overlap')
        with safe_parent(out.parent,out.name) as (parent,leaf):
            os.mkdir(leaf,0o700,dir_fd=parent)
        claim={'schema':'macr.repair.localize-claim.v1','issue_sha256':sha256(raw).hexdigest(),
            'discovery_profile':asdict(profile),'max_candidates':args.max_candidates,
            'model_calls':0,'target_code_executed':False,'task_created':False}
        safe_write(out,'claim.json',encode_bounded(claim,JSON_LIMIT),limit=JSON_LIMIT)
        scope=RepositoryScope().inventory(repo,profile)
        scope_summary={'eligible_files':len(scope.eligible),'excluded_entries':len(scope.excluded),
            'unsupported':[asdict(item) for item in scope.unsupported[:32]],
            'unreadable':[asdict(item) for item in scope.unreadable[:32]],
            'errors':[asdict(item) for item in scope.errors[:32]],
            'detail_truncated':any(len(items)>32 for items in (scope.unsupported,scope.unreadable,scope.errors))}
        safe_write(out,'discovery-summary.json',encode_bounded(scope_summary,JSON_LIMIT),limit=JSON_LIMIT)
        store=SnapshotStore(out/'snapshots',allowed_root=out)
        bundle=store.capture(repo,scope)
        index=RepositoryIndex(store,out/'index',allowed_root=out)
        index.build(bundle)
        from .repair_localize import propose_repair_sources
        proposal=propose_repair_sources(bundle,index,issue,max_candidates=args.max_candidates)
        if getattr(args,'windowed_input',False):
            from .repair_windows import propose_source_windows
            proposal['windowed_source']=None
            try:
                selected={row['path']:store.read_verified(bundle.ref,row['path']) for row in proposal['candidates']}
                proposal['windowed_source']=propose_source_windows(selected,proposal['candidates'])
            except ContractError as error:
                proposal['gaps'].append({'code':'windowed_input_unavailable','error':{'code':error.code,'field':error.field}})
        proposal['discovery_profile']=asdict(profile)
        proposal['snapshot_ref']=asdict(bundle.ref)
        proposal['discovery_snapshot_retained_locally']=True
        proposal['elapsed_seconds']=time.monotonic()-started
        safe_write(out,'proposal.json',encode_bounded(proposal,JSON_LIMIT),limit=JSON_LIMIT)
        summary={'status':proposal['status'],'candidate_count':len(proposal['candidates']),
            'source_inventory_proposed':proposal['source_files'] is not None,
            'task_ready':False,'model_calls':0,'target_code_executed':False,'remote_enabled':False,
            'snapshot_content_id':bundle.ref.content_id,'elapsed_seconds':proposal['elapsed_seconds']}
        if getattr(args,'windowed_input',False):
            summary['windowed_input_proposed']=proposal['windowed_source'] is not None
        code=0
    except (ContractError,OSError,ValueError,TypeError,KeyError,UnicodeError,RuntimeError,MemoryError) as error:
        error_code=getattr(error,'code','repair_localize_input_unavailable')
        if not isinstance(error_code,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',error_code):
            error_code='repair_localize_input_unavailable'
        summary={'status':'rejected','error_code':error_code,'task_ready':False,
            'model_calls':0,'target_code_executed':False,'remote_enabled':False}
        code=2
    print(json.dumps(summary,sort_keys=True))
    return code
