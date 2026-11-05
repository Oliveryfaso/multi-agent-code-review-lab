"""Public-test bridge, not a native executor, grant or producer attestation.

The embedding application supplies an already reviewed/authorized backend and
its public-only structured-result reader. No plugin, file or log is discovered.
Persisted audits are revalidated without calling either supplied object again.
"""
from copy import deepcopy
from dataclasses import asdict
import re

from macr.execution.results import classify_check
from macr.tools.test_runner import TestRunnerTool
from .records import CheckSpec, CheckResult, SnapshotRef
from .validation import ContractError, decode_record
from .repair_io import bounded_json, encode_bounded
from hashlib import sha256

ORIGIN='backend_reported_unattested'
COUNTS={'passed','failed','errors','collection_errors','skipped','xfailed','xpassed','executed','collected'}


def _require(ok, code='public_backend_contract'):
    if not ok:raise ContractError('repair_loop_invalid',code)


def _digest(value):
    return sha256(encode_bounded(value,262144)).hexdigest()


def descriptor(backend_id, execution_profile):
    _require(type(backend_id) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',backend_id),
             'public_backend_identity')
    _require(type(execution_profile) is str and execution_profile!='none'
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',execution_profile),'public_execution_profile')
    return {'schema':'macr.repair.public-backend.v1','backend_id':backend_id,'execution_profile':execution_profile,
            'phase':'public','origin':ORIGIN}


def validate_descriptor(value):
    _require(type(value) is dict and value==descriptor(value.get('backend_id'),value.get('execution_profile')),'public_backend_identity')
    return deepcopy(value)


def public_request(identity, snapshot, spec, source_id, names):
    validate_descriptor(identity)
    snapshot_record=asdict(decode_record(SnapshotRef,asdict(snapshot)))
    spec_record=asdict(decode_record(CheckSpec,asdict(spec)))
    _require(spec_record['execution_profile']==identity['execution_profile'],'public_execution_profile')
    _require(type(source_id) is str and re.fullmatch(r'[0-9a-f]{64}',source_id)
        and type(names) is list and 1<=len(names)<=32 and names==sorted(set(names))
        and all(type(n) is str and 0<len(n.encode())<=2048 for n in names))
    request={'schema':'macr.repair.public-check.v1','phase':'public','backend':deepcopy(identity),
        'source_id':source_id,'snapshot':snapshot_record,'spec':spec_record,'test_names':list(names)}
    bounded_json(request,max_bytes=32768,max_depth=12)
    return request


def _safe_result(result, spec):
    """Keep categorical results/counts only; never open or retain log refs."""
    _require(type(result) is CheckResult and result.check_id==spec.check_id)
    decode_record(CheckResult,asdict(result))
    counts=result.test_counts
    _require(counts is None or type(counts) is dict and set(counts)<=COUNTS
        and all(type(v) is int and 0<=v<=65536 for v in counts.values()))
    return asdict(classify_check({'execution_status':result.execution_status,'exit_code':result.exit_code,
        'test_counts':deepcopy(counts),'output_truncated':result.output_truncated},spec))


def _receipt(raw, request):
    bounded_json(raw,max_bytes=32768,max_depth=12)
    _require(type(raw) is dict and set(raw)=={'schema','phase','request_sha256','observation'}
        and raw['schema']=='macr.repair.public-check-receipt.v1' and raw['phase']=='public'
        and raw['request_sha256']==_digest(request),'public_receipt_binding')
    observation=raw['observation']
    _require(type(observation) is dict and set(observation)=={
        'source_id','execution_status','outcomes','diagnostics'}
        and observation['source_id']==request['source_id'] and observation['execution_status']=='completed'
        and type(observation['outcomes']) is dict and set(observation['outcomes'])==set(request['test_names'])
        and all(v in ('passed','failed') for v in observation['outcomes'].values()),'public_receipt_binding')
    # Source locations are checked against complete bytes by the shared loop.
    _require(type(observation['diagnostics']) is list and len(observation['diagnostics'])<=8)
    return deepcopy(raw)


class _Dispatch:
    """Track the single call through the existing capability gate."""
    def __init__(self, backend):
        self.backend=backend;self.attempted=False;self.raised=False;self.bad_result=False
    def capabilities(self):return self.backend.capabilities()
    def run(self, check, snapshot):
        _require(not self.attempted,'public_backend_repeated_dispatch')
        self.attempted=True
        try:
            result=self.backend.run(deepcopy(check),deepcopy(snapshot))
            try:_safe_result(result,check)
            except Exception:self.bad_result=True
            return result
        except Exception:
            self.raised=True
            raise


class BackendVerifier:
    """Injected application interface; construction grants no execution rights."""
    def __init__(self, backend, public_result_reader, *, backend_id, execution_profile):
        _require(callable(public_result_reader),'public_result_reader')
        self.backend=backend;self.public_result_reader=public_result_reader
        self.identity=descriptor(backend_id,execution_profile)

    def available(self):
        validate_descriptor(self.identity)
        return TestRunnerTool.supports(self.backend)

    def observe(self, request, snapshot, spec):
        audit={'request':deepcopy(request),'backend_result':None,'receipt':None,
               'dispatch_attempted':False,'failure':None}
        dispatch=_Dispatch(self.backend)
        try:
            result=TestRunnerTool().run_check(snapshot,spec,dispatch)
            audit['dispatch_attempted']=dispatch.attempted
            if dispatch.raised or dispatch.bad_result:
                audit['failure']='backend_outcome_unknown'
                result=classify_check({'execution_status':'outcome_unknown'},spec)
            audit['backend_result']=_safe_result(result,spec)
        except Exception:
            audit['dispatch_attempted']=dispatch.attempted
            audit['failure']='backend_outcome_unknown' if dispatch.attempted else 'backend_unavailable'
            audit['backend_result']=asdict(classify_check({'execution_status':
                'outcome_unknown' if dispatch.attempted else 'blocked'},spec))
        if audit['backend_result']['execution_status']=='completed':
            try:raw=self.public_result_reader(deepcopy(request))
            except Exception:audit['failure']='reader_unavailable'
            else:
                try:audit['receipt']=_receipt(raw,request)
                except Exception:audit['failure']='receipt_invalid'
        return audit


def validate_audit(audit, request, files, names, spec, *, proposal_contract, full_originals):
    """Pure receipt replay. Never dispatches, reads logs or opens scorer data."""
    from . import repair_loop as loop
    bounded_json(audit,max_bytes=65536,max_depth=24)
    _require(type(audit) is dict and set(audit)=={
        'request','backend_result','receipt','dispatch_attempted','failure'}
        and audit['request']==request and type(audit['dispatch_attempted']) is bool
        and audit['failure'] in (None,'backend_outcome_unknown','backend_unavailable',
                                 'reader_unavailable','receipt_invalid'),'public_audit_binding')
    result=decode_record(CheckResult,audit['backend_result'])
    _require(audit['backend_result']==_safe_result(result,spec),'public_audit_result')
    state=result.execution_status
    if audit['failure']=='backend_outcome_unknown':
        _require(audit['dispatch_attempted'] and state=='outcome_unknown')
    elif audit['failure']=='backend_unavailable':
        _require(not audit['dispatch_attempted'] and state=='blocked')
    if state!='blocked':_require(audit['dispatch_attempted'],'public_audit_dispatch')
    if audit['failure'] in ('reader_unavailable','receipt_invalid'):
        _require(state=='completed' and audit['receipt'] is None)
        return None,classify_check({'execution_status':'error'},spec),True
    if state!='completed':
        _require(audit['receipt'] is None)
        return {'source_id':request['source_id'],'execution_status':state,'outcomes':{},'diagnostics':[]},result,False
    receipt=_receipt(audit['receipt'],request);raw=receipt['observation']
    loop._public_diagnostics(raw,files,names)
    # Early proposal versions do not expose diagnostics, but backend receipts
    # always have the same public contract and are checked before projection.
    observation=raw if proposal_contract in ('v4','v5','v6') else {
        k:v for k,v in raw.items() if k!='diagnostics'}
    expected=loop._observation(observation,files,names,spec,proposal_contract=proposal_contract,full_originals=full_originals)
    counts=result.test_counts or {};wanted=expected.test_counts
    _require(not result.output_truncated and result.execution_status==expected.execution_status
        and result.observed_status==expected.observed_status and result.exit_code==expected.exit_code
        and all(counts.get(k,0)==wanted.get(k,0) for k in COUNTS),'public_result_contradiction')
    return raw,expected,False
