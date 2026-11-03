"""Shared repair-model contract; durable HTTP mode is explicitly opt-in.

The normal provider encoder/parser is reused with an injected transport. Local
prices and input envelopes are declared engineering assumptions, not a verified
current tariff or supplier-enforced spending cap. No credential input exists.
The default meter is in-memory. An explicit durable binding shares the existing
HTTP adapter and a locked spend ledger; neither is operator/model attestation.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import threading
import math
from time import perf_counter

from macr.providers.base import (ConversationRequest, ModelBudget, ModelRequest,
    ProviderError, validate_request, validate_billing_evidence, validate_identity_evidence)
from macr.providers.deepseek import DeepSeekProvider
from macr.investigation.repair_io import encode_bounded


_SCHEMA = 'macr.repair.provider-config.v1'
_FIELDS = {'schema','model','endpoint','thinking','reasoning_effort',
           'expected_fingerprint','limits','pricing'}
_LIMITS = {'max_requests','max_input_per_request','max_output_per_request',
           'max_total_input_tokens','max_total_output_tokens','timeout_seconds',
           'max_usd','reservation_input_tokens'}
_PRICES = {'input_usd_per_million','output_usd_per_million','basis'}
_PRICE_BASIS = 'caller_frozen_schedule_not_verified_current_price'
_MONEY = re.compile(r'(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?\Z')


def _require(ok, code='repair_config_invalid'):
    if not ok: raise ProviderError(code)


def _money(value):
    _require(type(value) is str and _MONEY.fullmatch(value) is not None)
    try:
        amount = Decimal(value)
        _require(amount.is_finite() and 0 <= amount <= Decimal('100000'))
        return int(amount * 1_000_000)
    except (InvalidOperation, OverflowError):
        raise ProviderError('repair_config_invalid') from None


def _usd(micro):
    return f'{micro//1_000_000}.{micro%1_000_000:06d}'


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def _text(value):
    try:
        return (type(value) is str and 1 <= len(value) <= 256 and value == value.strip()
                and not any(x in value for x in '\x00\n\r') and len(value.encode()) <= 1024)
    except UnicodeError:
        return False


def development_config():
    """Explicit offline default; neither this object nor a valid price enables I/O."""
    return {'schema':_SCHEMA,'model':'deepseek-flash','endpoint':'https://api.deepseek.com',
        'thinking':{'type':'enabled'},'reasoning_effort':'high','expected_fingerprint':None,
        'limits':{'max_requests':3,'max_input_per_request':8192,'max_output_per_request':1024,
            'max_total_input_tokens':24576,'max_total_output_tokens':3072,
            'timeout_seconds':60,'max_usd':'1.000000','reservation_input_tokens':1048576},
        'pricing':{'input_usd_per_million':'0.30','output_usd_per_million':'1.20',
                   'basis':_PRICE_BASIS}}


def validate_config(value):
    _require(type(value) is dict and set(value) == _FIELDS and value['schema'] == _SCHEMA)
    _require(_text(value['model']) and value['endpoint'] == 'https://api.deepseek.com')
    _require(type(value['thinking']) is dict and set(value['thinking']) == {'type'}
        and value['thinking']['type'] in ('enabled','disabled')
        and value['reasoning_effort'] in ('none','high')
        and (value['thinking']['type'] == 'enabled') == (value['reasoning_effort'] == 'high'))
    _require(value['expected_fingerprint'] is None or _text(value['expected_fingerprint']))
    limits, pricing = value['limits'], value['pricing']
    _require(type(limits) is dict and set(limits) == _LIMITS
        and type(pricing) is dict and set(pricing) == _PRICES and pricing['basis'] == _PRICE_BASIS)
    for key, lower, upper in (
        ('max_requests',1,3),('max_input_per_request',1,1048576),
        ('max_output_per_request',1,393216),('max_total_input_tokens',1,3145728),
        ('max_total_output_tokens',1,1179648),('reservation_input_tokens',1,1048576)):
        _require(_integer(limits[key],lower,upper))
    _require(limits['max_total_input_tokens'] >= limits['max_input_per_request']
        and limits['max_total_output_tokens'] >= limits['max_output_per_request']
        and limits['reservation_input_tokens'] >= limits['max_input_per_request'])
    _require(type(limits['timeout_seconds']) in (int,float)
        and 0 < limits['timeout_seconds'] <= 300)
    _require(_money(limits['max_usd']) > 0
        and _money(pricing['input_usd_per_million']) > 0
        and _money(pricing['output_usd_per_million']) > 0)
    try:
        _require(len(json.dumps(value,allow_nan=False,ensure_ascii=False).encode()) <= 8192)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise ProviderError('repair_config_invalid') from None
    return deepcopy(value)


class RepairProviderBinding:
    """One shared model/limit/meter implementation; strategy is not a parameter.

    No default transport, Key reader or allow_remote switch exists. Injected
    transports are a trusted application/test dependency, not authenticated I/O.
    Even a successful invocation here does not attest real-model execution.
    """
    name = 'repair_deepseek_binding'

    def __init__(self, config, *, transport=None, spend_ledger=None):
        self._config = validate_config(config)
        self._transport = transport
        self._spend = spend_ledger
        self._spend_ref = None
        self._durable_stop = False
        self._official_http = False
        from .repair_transport import OfficialRepairTransport
        if spend_ledger is not None:
            from .repair_spend import RepairSpendLedger
            _require(type(spend_ledger) is RepairSpendLedger, 'repair_durable_claim_required')
            ref = spend_ledger.ref(); state = spend_ledger.summary()
            _require(type(ref['cell_id']) is str and ref['cell_id']
                and state['configuration_sha256'] == sha256(encode_bounded(self._config,8192)).hexdigest()
                and not state['stopped'], 'repair_durable_claim_required')
            self._spend_ref = deepcopy(ref)
        if type(transport) is OfficialRepairTransport:
            _require(spend_ledger is not None and transport.enabled, 'repair_durable_claim_required')
            self._official_http = True
        self._requests = self._inputs = self._outputs = self._known = self._reserved = 0
        self._stopped = False
        self._fingerprint = self._config['expected_fingerprint']
        self._calls = []
        self._lock = threading.Lock()

    @property
    def config(self): return deepcopy(self._config)

    @property
    def enabled(self): return self._transport is not None

    def prepare_request(self, request: ModelRequest):
        limits = self._config['limits']
        return ModelBudget(limits['max_input_per_request'],limits['max_output_per_request'],
                           limits['timeout_seconds']).apply(request)

    def _conversation(self, request):
        validate_request(request)
        limits = self._config['limits']
        _require((request.max_input_tokens,request.max_output_tokens,request.timeout_seconds) ==
            (limits['max_input_per_request'],limits['max_output_per_request'],limits['timeout_seconds']),
            'repair_request_mismatch')
        _require(request.tools == [], 'repair_request_mismatch')
        return ConversationRequest(messages=deepcopy(request.messages),tools=[],
            response_schema=deepcopy(request.response_schema),max_input_tokens=request.max_input_tokens,
            max_output_tokens=request.max_output_tokens,timeout_seconds=request.timeout_seconds,
            metadata=deepcopy(request.metadata),thinking=deepcopy(self._config['thinking']),
            reasoning_effort=self._config['reasoning_effort'])

    def _charge(self, inputs, outputs):
        pricing = self._config['pricing']
        # All input is charged at the declared all-miss rate; cache discounts are
        # deliberately not inferred. Round each receipt upward to micro-USD.
        numerator = inputs*_money(pricing['input_usd_per_million']) + outputs*_money(pricing['output_usd_per_million'])
        return (numerator+999999)//1_000_000

    def _identity(self, response=None, evidence=None):
        if response is not None:
            model, fingerprint = response.model, response.system_fingerprint
            valid = model == self._config['model'] and _text(fingerprint)
        elif evidence is not None:
            model, fingerprint = evidence['model'], evidence['system_fingerprint']
            valid = (evidence['model_state'] == 'validated' and evidence['fingerprint_state'] == 'validated'
                     and model == self._config['model'])
        else:
            model = fingerprint = None; valid = False
        if valid and self._fingerprint is None: self._fingerprint = fingerprint
        valid = valid and fingerprint == self._fingerprint
        return {'model':model,'system_fingerprint':fingerprint,
                'model_state':'validated' if model == self._config['model'] else 'unverified',
                'fingerprint_state':'validated' if _text(fingerprint) else 'unverified',
                'identity_matches_config':valid}, valid

    def _settle(self, call, response=None, billing=None):
        if response is not None and response.usage_kind == 'measured':
            usage = response.usage
        elif billing is not None:
            raw = billing['raw_usage']; usage={'input_tokens':raw['prompt_tokens'],'output_tokens':raw['completion_tokens']}
        else: return False
        if not {'input_tokens','output_tokens'} <= usage.keys() or any(type(usage[k]) is not int or usage[k]<0
                for k in ('input_tokens','output_tokens')):
            return False
        inputs, outputs = usage['input_tokens'],usage['output_tokens']
        call['usage'] = deepcopy(usage); call['known_micro_usd'] = self._charge(inputs,outputs)
        self._known += call['known_micro_usd']; self._inputs += inputs;self._outputs += outputs
        self._reserved -= call['reserved_micro_usd'];call['reservation_retained']=False
        return True

    def complete(self, request):
        _require(self._transport is not None, 'remote_disabled')
        _require(self._lock.acquire(blocking=False), 'repair_binding_busy')
        before = self._requests
        completed = None
        failure = None
        try:
            completed = self._complete(request)
            return completed
        except BaseException as error:
            failure = error
            # Once an invocation has a dispatch intent, interruption is not
            # proof of remote cancellation. Never leave this object reusable.
            if self._requests > before:
                self._stopped = True
                call = self._calls[-1]
                if call['status'] == 'dispatch_intent':
                    call['status'] = 'outcome_unknown' if call['reservation_retained'] else 'processing_failed'
            raise
        finally:
            try:
                if self._spend is not None and self._requests > before:
                    try:
                        state = self._spend.settle(self._calls[-1])
                    except BaseException as error:
                        self._stopped = self._durable_stop = True
                        if not isinstance(error, Exception): raise
                        if failure is not None and not isinstance(failure, Exception): raise failure
                        original = failure if isinstance(failure, ProviderError) else None
                        raise ProviderError('repair_durable_settlement_unknown',
                            response=completed if completed is not None else original.response if original else None,
                            diagnostic=original.diagnostic if original else None,
                            billing_evidence=original.billing_evidence if original else None,
                            identity_evidence=original.identity_evidence if original else None) from None
                    if state['stopped']:
                        self._stopped = self._durable_stop = True
                        if completed is not None:
                            raise ProviderError('repair_durable_stopped',response=completed)
            finally:
                self._lock.release()

    def _complete(self, request):
        _require(not self._stopped,'repair_binding_stopped')
        # Validate all metadata before deepcopy, reservation or dispatch. The
        # underlying wire does not contain metadata, but our durable intent does.
        try:
            validate_request(request)
            canonical=encode_bounded({key:getattr(request,key) for key in (
                'messages','tools','response_schema','max_input_tokens','max_output_tokens',
                'timeout_seconds','metadata')}, 1024*1024)
            request_hash=sha256(canonical).hexdigest()
        except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError):
            raise ProviderError('repair_request_mismatch') from None
        conversation=self._conversation(request); limits=self._config['limits']
        reserve=self._charge(limits['reservation_input_tokens'],limits['max_output_per_request'])
        _require(self._requests < limits['max_requests']
            and self._inputs+request.max_input_tokens <= limits['max_total_input_tokens']
            and self._outputs+request.max_output_tokens <= limits['max_total_output_tokens']
            and self._known+self._reserved+reserve <= _money(limits['max_usd']), 'repair_budget_exhausted')
        provider=DeepSeekProvider(base_url=self._config['endpoint'],model=self._config['model'],
            timeout_seconds=limits['timeout_seconds'],transport=self._transport,allow_remote=False)
        wire=provider.encode_conversation_request(conversation)
        call={'number':self._requests+1,'request_sha256':request_hash,
            'wire_sha256':sha256(wire).hexdigest(),'reserved_micro_usd':reserve,
            'reservation_retained':True,'known_micro_usd':0,'usage':{},'identity':None,
            'billing_evidence':None,'identity_evidence':None,
            'diagnostic':None,'status':'dispatch_intent','provider_seconds':0.0}
        if self._spend is not None:
            try:
                _require(self._spend.ref() == self._spend_ref, 'repair_durable_claim_changed')
                # The durable, cross-cell reservation must exist before I/O or
                # the in-memory invocation count can advance.
                self._spend.reserve(call)
            except Exception:
                self._stopped = self._durable_stop = True
                raise ProviderError('repair_durable_reservation_failed') from None
        self._requests+=1; self._reserved+=reserve
        self._calls.append(call); started=perf_counter()
        try:
            response=provider.converse(conversation)
        except ProviderError as error:
            self._stopped=True
            call['provider_seconds']=perf_counter()-started
            billing, identity = error.billing_evidence,error.identity_evidence
            # Evidence is independently bound to the exact wire request, rather
            # than trusting an arbitrary transport-supplied usage object.
            if billing is not None and billing['request_sha256'] != call['wire_sha256']:billing=None
            if identity is not None and identity['request_sha256'] != call['wire_sha256']:identity=None
            call['billing_evidence']=billing;call['identity_evidence']=identity
            # A typed error.response can originate in an injected transport and
            # lacks wire binding. Only independently extracted billing/identity
            # from the received envelope may settle failed invocations.
            settled=self._settle(call,billing=billing)
            call['identity'], valid=self._identity(evidence=identity)
            call['diagnostic']=error.diagnostic
            call['status']='known_provider_failure' if settled else 'outcome_unknown'
            raise
        call['provider_seconds']=perf_counter()-started
        settled=self._settle(call,response=response)
        call['identity'],valid=self._identity(response)
        if not settled:
            self._stopped=True;call['status']='outcome_unknown'
            raise ProviderError('repair_usage_unknown',response=response)
        if not valid:
            self._stopped=True;call['status']='identity_invalid'
            raise ProviderError('repair_identity_invalid',response=response)
        if (response.usage['input_tokens'] > request.max_input_tokens
            or response.usage['output_tokens'] > request.max_output_tokens
            or self._inputs > limits['max_total_input_tokens'] or self._outputs > limits['max_total_output_tokens']
            or self._known > _money(limits['max_usd'])):
            self._stopped=True;call['status']='usage_limit_exceeded'
            raise ProviderError('repair_usage_limit',response=response)
        call['status']='response_recorded'
        return response

    def snapshot(self):
        snapshot = {'schema':'macr.repair.provider-meter.v1','requests':self._requests,
            'attempted_calls':self._requests,'enabled':self.enabled,
            'status':'stopped' if self._stopped else 'in_flight' if self._calls and self._calls[-1]['status']=='dispatch_intent'
                else 'ready' if self.enabled else 'disabled',
            'configuration_sha256':sha256(json.dumps(self._config,sort_keys=True,separators=(',',':'),
                ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
            'input_tokens':self._inputs,'output_tokens':self._outputs,'known_usd':_usd(self._known),
            'unknown_reserved_usd':_usd(self._reserved),'stopped':self._stopped,
            'expected_fingerprint':self._fingerprint,'calls':self._calls,
            'remote_enabled':False,'transport_origin':'injected_unverified' if self._transport is not None else 'disabled',
            'cost_basis':'caller_schedule_all_input_miss_rounded_up_not_supplier_invoice',
            'identity_authenticated':False,'durable_cross_process_ledger':False,
            'automatic_retry_allowed':False}
        if self._spend is not None:
            snapshot.update(schema='macr.repair.provider-meter.v2',
                spend_commitment=self._spend_ref, durable_stop=self._durable_stop,
                durable_cross_process_ledger=True, remote_enabled=self._official_http,
                transport_origin=('official_https_configured_unattested' if self._official_http
                                  else 'injected_unverified' if self.enabled else 'disabled'))
        return deepcopy(snapshot)


def validate_meter(config, snapshot, request_digests):
    """Recompute local meter consistency and its prefix of durable intent hashes.

    This authenticates neither a producer nor a model. A missing dispatch after
    an intent is allowed; a call without its matching intent is rejected. No
    state is reconstructed for resuming, no reservation is released here.
    """
    try:
        binding=RepairProviderBinding(config)
        durable = type(snapshot) is dict and snapshot.get('schema') == 'macr.repair.provider-meter.v2'
        fields=set(binding.snapshot())
        if durable:
            fields |= {'spend_commitment','durable_stop'}
        _require(type(snapshot) is dict and set(snapshot)==fields,'repair_meter_invalid')
        if durable:
            ref = snapshot['spend_commitment']
            _require(type(ref) is dict and set(ref) == {'plan_sha256','cell_id'}
                and type(ref['plan_sha256']) is str and re.fullmatch('[0-9a-f]{64}',ref['plan_sha256'])
                and type(ref['cell_id']) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',ref['cell_id'])
                and type(snapshot['durable_stop']) is bool and type(snapshot['remote_enabled']) is bool
                and snapshot['durable_cross_process_ledger'] is True
                and (not snapshot['remote_enabled'] or snapshot['enabled'] is True),'repair_meter_invalid')
        _require(all(type(snapshot[key]) is int and snapshot[key]>=0 for key in
            ('requests','attempted_calls','input_tokens','output_tokens')),'repair_meter_invalid')
        _require(type(request_digests) is list and len(request_digests)<=16
            and all(type(h) is str and re.fullmatch('[0-9a-f]{64}',h) for h in request_digests),
            'repair_meter_invalid')
        calls=snapshot['calls'];limits=binding.config['limits']
        _require(type(calls) is list and len(calls)<=limits['max_requests']
            and len(calls)<=len(request_digests),'repair_meter_invalid')
        call_fields={'number','request_sha256','wire_sha256','reserved_micro_usd',
            'reservation_retained','known_micro_usd','usage','identity','billing_evidence',
            'identity_evidence','diagnostic','status','provider_seconds'}
        statuses={'dispatch_intent','response_recorded','known_provider_failure',
                  'outcome_unknown','identity_invalid','usage_limit_exceeded','processing_failed'}
        terminal=False;expected_fingerprint=binding.config['expected_fingerprint']
        inputs=outputs=known=reserved=0
        reserve=binding._charge(limits['reservation_input_tokens'],limits['max_output_per_request'])
        for index,call in enumerate(calls,1):
            # Replay admission before settling each call. Small actual charges
            # cannot retrospectively justify a dispatch whose full reservation
            # was unaffordable (the same applies to declared token ceilings).
            _require(inputs+limits['max_input_per_request']<=limits['max_total_input_tokens']
                and outputs+limits['max_output_per_request']<=limits['max_total_output_tokens']
                and known+reserved+reserve<=_money(limits['max_usd']), 'repair_meter_invalid')
            _require(not terminal and type(call) is dict and set(call)==call_fields
                and type(call['number']) is int and call['number']==index
                and call['request_sha256']==request_digests[index-1]
                and type(call['wire_sha256']) is str and re.fullmatch('[0-9a-f]{64}',call['wire_sha256'])
                and call['status'] in statuses,'repair_meter_invalid')
            _require(type(call['reserved_micro_usd']) is int and call['reserved_micro_usd']==reserve
                and type(call['reservation_retained']) is bool
                and type(call['known_micro_usd']) is int and call['known_micro_usd']>=0
                and type(call['provider_seconds']) in (int,float)
                and math.isfinite(call['provider_seconds']) and call['provider_seconds']>=0,
                'repair_meter_invalid')
            for field,validator in (('billing_evidence',validate_billing_evidence),
                                    ('identity_evidence',validate_identity_evidence)):
                evidence=call[field]
                if evidence is not None:
                    _require(validator(evidence)==evidence and evidence['request_sha256']==call['wire_sha256'],
                        'repair_meter_invalid')
            if call['diagnostic'] is not None:
                from macr.providers.failure_evidence import validate_failure_diagnostic
                _require(validate_failure_diagnostic(call['diagnostic'])==call['diagnostic'],'repair_meter_invalid')
            usage=call['usage']
            _require(type(usage) is dict,'repair_meter_invalid')
            if call['reservation_retained']:
                _require(usage=={} and call['known_micro_usd']==0
                    and call['status'] in {'dispatch_intent','outcome_unknown'},'repair_meter_invalid')
                reserved+=reserve
            else:
                _require({'input_tokens','output_tokens'}<=usage.keys()
                    and all(type(v) is int and v>=0 for v in usage.values()),'repair_meter_invalid')
                i,o=usage['input_tokens'],usage['output_tokens']
                _require(call['known_micro_usd']==binding._charge(i,o),'repair_meter_invalid')
                billing=call['billing_evidence']
                if billing is not None:
                    _require(billing['max_output_tokens']==limits['max_output_per_request']
                        and i==billing['raw_usage']['prompt_tokens']
                        and o==billing['raw_usage']['completion_tokens'],'repair_meter_invalid')
                if call['status']=='known_provider_failure':
                    _require(billing is not None,'repair_meter_invalid')
                inputs+=i;outputs+=o;known+=call['known_micro_usd']
            identity=call['identity'];identity_valid=False
            if identity is not None:
                _require(type(identity) is dict and set(identity)=={'model','system_fingerprint',
                    'model_state','fingerprint_state','identity_matches_config'},'repair_meter_invalid')
                model=identity['model'];fingerprint=identity['system_fingerprint']
                model_valid=model==binding.config['model'];fp_valid=_text(fingerprint)
                if model_valid and fp_valid and expected_fingerprint is None:expected_fingerprint=fingerprint
                identity_valid=model_valid and fp_valid and fingerprint==expected_fingerprint
                _require(identity['model_state']==('validated' if model_valid else 'unverified')
                    and identity['fingerprint_state']==('validated' if fp_valid else 'unverified')
                    and type(identity['identity_matches_config']) is bool
                    and identity['identity_matches_config']==identity_valid,'repair_meter_invalid')
                evidence=call['identity_evidence']
                if evidence is not None:
                    _require(evidence['requested_model']==binding.config['model']
                        and model==evidence['model'] and fingerprint==evidence['system_fingerprint'],
                        'repair_meter_invalid')
            if call['status']=='response_recorded':
                _require(not call['reservation_retained'] and identity_valid
                    and usage['input_tokens']<=limits['max_input_per_request']
                    and usage['output_tokens']<=limits['max_output_per_request']
                    and inputs<=limits['max_total_input_tokens'] and outputs<=limits['max_total_output_tokens']
                    and known<=_money(limits['max_usd']),'repair_meter_invalid')
            elif call['status']=='identity_invalid':
                _require(not call['reservation_retained'] and not identity_valid,'repair_meter_invalid')
                terminal=True
            elif call['status']=='usage_limit_exceeded':
                _require(not call['reservation_retained'] and identity_valid and (
                    usage['input_tokens']>limits['max_input_per_request']
                    or usage['output_tokens']>limits['max_output_per_request']
                    or inputs>limits['max_total_input_tokens'] or outputs>limits['max_total_output_tokens']
                    or known>_money(limits['max_usd'])),'repair_meter_invalid')
                terminal=True
            elif call['status']!='dispatch_intent':
                terminal=True
            else:
                _require(index==len(calls),'repair_meter_invalid')
        if durable: terminal = terminal or snapshot['durable_stop']
        _require(type(snapshot['enabled']) is bool and type(snapshot['stopped']) is bool
            and snapshot['stopped']==terminal,'repair_meter_invalid')
        expected_status='stopped' if terminal else 'in_flight' if calls and calls[-1]['status']=='dispatch_intent' \
            else 'ready' if snapshot['enabled'] else 'disabled'
        expected=binding.snapshot()
        expected.update(requests=len(calls),attempted_calls=len(calls),input_tokens=inputs,
            output_tokens=outputs,known_usd=_usd(known),unknown_reserved_usd=_usd(reserved),
            stopped=terminal,expected_fingerprint=expected_fingerprint,calls=deepcopy(calls),
            enabled=snapshot['enabled'],status=expected_status,
            transport_origin='injected_unverified' if snapshot['enabled'] else 'disabled')
        if durable:
            expected.update(schema='macr.repair.provider-meter.v2',
                spend_commitment=deepcopy(snapshot['spend_commitment']),
                durable_stop=snapshot['durable_stop'],durable_cross_process_ledger=True,
                remote_enabled=snapshot['remote_enabled'],
                transport_origin=('official_https_configured_unattested' if snapshot['remote_enabled']
                    else 'injected_unverified' if snapshot['enabled'] else 'disabled'))
        _require(snapshot==expected and (snapshot['enabled'] or not calls),'repair_meter_invalid')
        return deepcopy(snapshot)
    except (ProviderError,ValueError,TypeError,KeyError,OverflowError,RecursionError,AttributeError):
        raise ProviderError('repair_meter_invalid') from None
