"""Provider bridge tests use captured synthetic HTTP envelopes, never networking."""
from copy import deepcopy
from dataclasses import asdict
import json
from unittest import TestCase
from unittest.mock import patch

from macr.providers.base import ModelRequest, ProviderError
from macr.providers.repair_binding import RepairProviderBinding, development_config, validate_config, validate_meter


def reply(*, model='deepseek-flash', fingerprint='fp-fixture', usage=True, finish='stop'):
    value = {'model':model, 'system_fingerprint':fingerprint,
        'choices':[{'message':{'role':'assistant','content':'{"status":"no_candidate"}',
                             'reasoning_content':'synthetic reasoning'}, 'finish_reason':finish}]}
    if usage: value['usage'] = {'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}
    return value


class Transport:
    def __init__(self, outputs): self.outputs, self.calls = outputs, []
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.calls.append((endpoint, body, timeout_seconds, max_response_bytes))
        value = self.outputs.pop(0)
        if isinstance(value, Exception): raise value
        return deepcopy(value)


class RepairProviderBindingTests(TestCase):
    def request(self, bridge):
        return bridge.prepare_request(ModelRequest(messages=[{'role':'user','content':'synthetic issue'}]))

    def test_default_disabled_rejects_before_any_network_or_request_reservation(self):
        binding = RepairProviderBinding(development_config())
        with patch('macr.providers.deepseek._ApprovedTransport', side_effect=AssertionError('no network')):
            with self.assertRaisesRegex(ProviderError, '^remote_disabled$'):
                binding.complete(self.request(binding))
        self.assertEqual(binding.snapshot()['requests'], 0)

    def test_same_configuration_uses_existing_conversation_wire_and_normalized_identity(self):
        transport = Transport([reply()]); binding = RepairProviderBinding(development_config(), transport=transport)
        request = self.request(binding); response = binding.complete(request)
        self.assertEqual(response.usage, {'input_tokens':10,'output_tokens':4})
        self.assertEqual(response.system_fingerprint, 'fp-fixture')
        endpoint, body, timeout, cap = transport.calls[0]; wire = json.loads(body)
        self.assertEqual(endpoint, 'https://api.deepseek.com/chat/completions')
        self.assertEqual(wire['model'], 'deepseek-flash')
        self.assertEqual(wire['thinking'], {'type':'enabled'})
        self.assertEqual(wire['reasoning_effort'], 'high')
        self.assertEqual(wire['max_tokens'], request.max_output_tokens)
        self.assertEqual(wire['messages'], request.messages)
        state = binding.snapshot()
        self.assertEqual((state['requests'],state['input_tokens'],state['output_tokens']), (1,10,4))
        self.assertEqual(state['known_usd'], '0.000008')
        self.assertEqual(state['unknown_reserved_usd'], '0.000000')
        self.assertEqual(state['transport_origin'], 'injected_unverified')
        self.assertFalse(state['remote_enabled']); self.assertFalse(state['identity_authenticated'])

    def test_unknown_keeps_reserve_and_stops_without_retry(self):
        transport = Transport([TimeoutError(), reply()]); binding = RepairProviderBinding(development_config(),transport=transport)
        with self.assertRaisesRegex(ProviderError, '^model_timeout$'): binding.complete(self.request(binding))
        state = binding.snapshot()
        self.assertTrue(state['stopped']); self.assertEqual(state['requests'],1)
        self.assertNotEqual(state['unknown_reserved_usd'],'0.000000')
        with self.assertRaisesRegex(ProviderError, '^repair_binding_stopped$'): binding.complete(self.request(binding))
        self.assertEqual(len(transport.calls),1)

    def test_missing_usage_is_unknown_and_cannot_be_settled_as_zero(self):
        binding = RepairProviderBinding(development_config(),transport=Transport([reply(usage=False)]))
        with self.assertRaisesRegex(ProviderError,'^repair_usage_unknown$'): binding.complete(self.request(binding))
        self.assertTrue(binding.snapshot()['stopped'])
        self.assertNotEqual(binding.snapshot()['unknown_reserved_usd'],'0.000000')

    def test_contract_failure_retains_independent_billing_and_identity(self):
        data = reply(); data['choices'][0]['message']['tool_calls'] = [
            {'id':'c1','type':'function','function':{'name':'undeclared','arguments':'{}'}}]
        data['choices'][0]['finish_reason'] = 'tool_calls'
        binding = RepairProviderBinding(development_config(),transport=Transport([data]))
        with self.assertRaisesRegex(ProviderError,'^model_response_invalid$') as caught:
            binding.complete(self.request(binding))
        self.assertIsNotNone(caught.exception.billing_evidence)
        state = binding.snapshot(); self.assertEqual(state['unknown_reserved_usd'],'0.000000')
        self.assertEqual(state['known_usd'],'0.000008')
        self.assertEqual(state['calls'][0]['identity']['fingerprint_state'],'validated')
        self.assertEqual(state['calls'][0]['diagnostic']['stage'],'assistant_message')

    def test_identity_drift_stops_but_preserves_known_charge(self):
        transport = Transport([reply(),reply(fingerprint='fp-other'),reply()])
        binding = RepairProviderBinding(development_config(),transport=transport)
        binding.complete(self.request(binding))
        with self.assertRaisesRegex(ProviderError,'^repair_identity_invalid$'): binding.complete(self.request(binding))
        self.assertTrue(binding.snapshot()['stopped']); self.assertEqual(binding.snapshot()['requests'],2)
        self.assertEqual(binding.snapshot()['known_usd'],'0.000016')
        self.assertEqual(binding.snapshot()['unknown_reserved_usd'],'0.000000')

    def test_per_request_and_cumulative_limits_cannot_be_widened_by_caller(self):
        config = development_config(); config['limits']['max_requests']=1
        transport = Transport([reply(),reply()]); binding=RepairProviderBinding(config,transport=transport)
        request=self.request(binding); widened=deepcopy(request); widened.max_output_tokens+=1
        with self.assertRaisesRegex(ProviderError,'^repair_request_mismatch$'): binding.complete(widened)
        self.assertEqual(len(transport.calls),0)
        binding.complete(request)
        with self.assertRaisesRegex(ProviderError,'^repair_budget_exhausted$'): binding.complete(request)
        self.assertEqual(len(transport.calls),1)

    def test_insufficient_usd_reserve_prevents_dispatch(self):
        config=development_config();config['limits']['max_usd']='0.000001'
        transport=Transport([reply()]);binding=RepairProviderBinding(config,transport=transport)
        with self.assertRaisesRegex(ProviderError,'^repair_budget_exhausted$'): binding.complete(self.request(binding))
        self.assertEqual(len(transport.calls),0)

    def test_mutable_inputs_and_snapshot_cannot_reset_metering(self):
        config=development_config();binding=RepairProviderBinding(config,transport=Transport([reply()]))
        config['limits']['max_requests']=999
        binding.complete(self.request(binding)); state=binding.snapshot();state['requests']=0
        state['calls'][0]['usage']['input_tokens']=0
        self.assertEqual(binding.snapshot()['requests'],1)
        self.assertEqual(binding.snapshot()['calls'][0]['usage']['input_tokens'],10)

    def test_config_has_no_credentials_arbitrary_endpoints_or_unbounded_prices(self):
        config=development_config()
        for mutate in (
            lambda x:x.update(api_key='DUMMY'),
            lambda x:x.update(endpoint='https://api.deepseek.com.evil'),
            lambda x:x['limits'].update(max_requests=True),
            lambda x:x['pricing'].update(input_usd_per_million='NaN'),
            lambda x:x['limits'].update(reservation_input_tokens=1),
        ):
            value=deepcopy(config);mutate(value)
            with self.assertRaisesRegex(ProviderError,'^repair_config_invalid$'):validate_config(value)

    def test_independent_failure_usage_exceeding_limit_is_still_charged_and_stops(self):
        data=reply(finish='length'); data['usage']['prompt_tokens']=9000;data['usage']['total_tokens']=9004
        transport=Transport([data,reply()]); binding=RepairProviderBinding(development_config(),transport=transport)
        with self.assertRaisesRegex(ProviderError,'^model_output_incomplete$'):binding.complete(self.request(binding))
        state=binding.snapshot();self.assertEqual(state['input_tokens'],9000)
        self.assertEqual(state['unknown_reserved_usd'],'0.000000');self.assertTrue(state['stopped'])
        self.assertEqual(len(transport.calls),1)

    def test_usage_over_declared_bound_retains_actual_not_clipped_charge(self):
        data=reply();data['usage']['prompt_tokens']=9000;data['usage']['total_tokens']=9004
        binding=RepairProviderBinding(development_config(),transport=Transport([data]))
        with self.assertRaisesRegex(ProviderError,'^repair_usage_limit$'):binding.complete(self.request(binding))
        self.assertEqual(binding.snapshot()['input_tokens'],9000)
        self.assertEqual(binding.snapshot()['known_usd'],'0.002705')

    def test_interrupted_dispatch_cannot_be_replayed_on_same_object(self):
        transport=Transport([KeyboardInterrupt(),reply()])
        # BaseException deliberately models interruption after reservation.
        def post(*args,**kwargs):
            transport.calls.append(args)
            value=transport.outputs.pop(0)
            if isinstance(value,BaseException):raise value
            return value
        transport.post=post
        binding=RepairProviderBinding(development_config(),transport=transport)
        with self.assertRaises(KeyboardInterrupt):binding.complete(self.request(binding))
        self.assertTrue(binding.snapshot()['stopped'])
        with self.assertRaisesRegex(ProviderError,'^repair_binding_stopped$'):binding.complete(self.request(binding))
        self.assertEqual(len(transport.calls),1)

    def test_validate_meter_rederives_aggregates_reserves_and_intent_bindings(self):
        binding=RepairProviderBinding(development_config(),transport=Transport([reply()]))
        binding.complete(self.request(binding));state=binding.snapshot()
        digests=[state['calls'][0]['request_sha256']]
        self.assertEqual(validate_meter(binding.config,state,digests),state)
        self.assertEqual(validate_meter(binding.config,state,digests+['0'*64]),state)
        for mutate in (
            lambda x:x.update(requests=0),lambda x:x.update(known_usd='0.000000'),
            lambda x:x.update(input_tokens=0),lambda x:x.update(status='stopped'),lambda x:x.update(requests=True),
            lambda x:x['calls'][0].update(known_micro_usd=0),
            lambda x:x['calls'][0].update(reservation_retained=True),
            lambda x:x['calls'][0].update(request_sha256='0'*64),
        ):
            tampered=deepcopy(state);mutate(tampered)
            with self.subTest(mutate=mutate),self.assertRaisesRegex(ProviderError,'^repair_meter_invalid$'):
                validate_meter(binding.config,tampered,digests)
        with self.assertRaisesRegex(ProviderError,'^repair_meter_invalid$'):validate_meter(binding.config,state,[])

    def test_validate_meter_unknown_and_known_failure_do_not_lose_reservation_truth(self):
        for data in (TimeoutError(),reply(finish='length')):
            binding=RepairProviderBinding(development_config(),transport=Transport([data]))
            with self.assertRaises(ProviderError):binding.complete(self.request(binding))
            state=binding.snapshot();digests=[state['calls'][0]['request_sha256']]
            self.assertEqual(validate_meter(binding.config,state,digests),state)
            tampered=deepcopy(state);tampered['stopped']=False
            with self.assertRaisesRegex(ProviderError,'^repair_meter_invalid$'):
                validate_meter(binding.config,tampered,digests)

    def test_metadata_failure_precedes_all_reservations_and_transport(self):
        for bad in (float('nan'),object(),{'nested':float('inf')}):
            transport=Transport([reply()]);binding=RepairProviderBinding(development_config(),transport=transport)
            request=self.request(binding);request.metadata={'bad':bad}
            with self.assertRaisesRegex(ProviderError,'^repair_request_mismatch$'):binding.complete(request)
            state=binding.snapshot();self.assertEqual(state['requests'],0);self.assertEqual(state['calls'],[])
            self.assertEqual(state['unknown_reserved_usd'],'0.000000');self.assertEqual(transport.calls,[])

    def test_transport_error_response_is_not_independent_billing_or_identity_evidence(self):
        from macr.providers.base import ModelResponse
        response=ModelResponse('forged transport content',usage={'input_tokens':10,'output_tokens':4},usage_kind='measured')
        transport=Transport([ProviderError('model_response_invalid',response=response),reply()])
        binding=RepairProviderBinding(development_config(),transport=transport)
        with self.assertRaisesRegex(ProviderError,'^model_response_invalid$'):binding.complete(self.request(binding))
        state=binding.snapshot();self.assertTrue(state['stopped']);self.assertEqual(state['known_usd'],'0.000000')
        self.assertNotEqual(state['unknown_reserved_usd'],'0.000000')
        self.assertEqual(state['calls'][0]['identity']['fingerprint_state'],'unverified')
        self.assertEqual(validate_meter(binding.config,state,[state['calls'][0]['request_sha256']]),state)
        with self.assertRaisesRegex(ProviderError,'^repair_binding_stopped$'):binding.complete(self.request(binding))
        self.assertEqual(len(transport.calls),1)

    def test_validate_meter_replays_each_call_reservation_gate_not_just_actual_totals(self):
        for field, value in (('max_usd','0.315803'),('max_total_input_tokens',8192),
                             ('max_total_output_tokens',1024)):
            config=development_config();config['limits'][field]=value
            binding=RepairProviderBinding(config,transport=Transport([reply(),reply()]))
            request=self.request(binding);binding.complete(request)
            with self.assertRaisesRegex(ProviderError,'^repair_budget_exhausted$'):binding.complete(request)
            state=binding.snapshot();extra=deepcopy(state['calls'][0]);extra['number']=2
            state['calls'].append(extra);state['requests']=state['attempted_calls']=2
            state['input_tokens']=20;state['output_tokens']=8;state['known_usd']='0.000016'
            with self.subTest(field=field),self.assertRaisesRegex(ProviderError,'^repair_meter_invalid$'):
                validate_meter(config,state,[call['request_sha256'] for call in state['calls']])

    def test_reentrant_complete_cannot_add_an_unmetered_parallel_call(self):
        transport=Transport([reply()]);binding=RepairProviderBinding(development_config(),transport=transport)
        observed=[]
        original=transport.post
        def reentrant(*args,**kwargs):
            try:binding.complete(self.request(binding))
            except ProviderError as error:observed.append(error.code)
            return original(*args,**kwargs)
        transport.post=reentrant
        binding.complete(self.request(binding))
        self.assertEqual(observed,['repair_binding_busy'])
        self.assertEqual(binding.snapshot()['requests'],1);self.assertEqual(len(transport.calls),1)
