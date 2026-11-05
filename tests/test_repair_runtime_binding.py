"""Real binding and on-disk spend state, with external I/O intercepted."""
from copy import deepcopy
from dataclasses import asdict
import json
from unittest import TestCase
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_repair_provider_binding import Transport, reply
from macr.providers.base import ModelRequest, ProviderError
from macr.providers.repair_binding import RepairProviderBinding, development_config, validate_meter
from macr.providers.repair_transport import OfficialRepairTransport
from macr.investigation.repair_loop import digest
try:
    from macr.providers import repair_spend as spend
except ImportError:
    spend = None


class RepairRuntimeBindingTests(TestCase):
    def test_stopped_finish_remains_halted_after_reload_with_known_usage(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            binding=RepairProviderBinding(self.config,transport=Transport([reply()]),spend_ledger=ledger)
            binding.complete(self.request(binding))
            ledger.finish_cell('stopped')
            self.assertTrue(ledger.summary()['stopped'])
        with self.ledger() as ledger:
            self.assertEqual((ledger.summary()['requests'],ledger.summary()['known_usd'],
                              ledger.summary()['unknown_reserved_usd']),(1,'0.000008','0.000000'))
            with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('feedback','b'*64)

    def test_stopped_finish_rejects_a_later_claim_already_in_persisted_records(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.finish_cell('stopped')
            # An authored old/corrupt prefix, not a permitted new claim.
            ledger._append('cell-002.claim.json',{'schema':'macr.repair.spend-claim.v1',
                'plan_sha256':ledger.plan_sha256,'index':2,'cell_id':'feedback','binding_sha256':'b'*64})
            with self.assertRaisesRegex(spend.RepairSpendError,'repair_spend_claim_after_stop'):
                ledger.summary()

    def setUp(self):
        self.assertIsNotNone(spend, 'durable repair ledger missing')
        self.root=fixture_root()/'ledger';self.config=development_config()
        self.plan={'schema':'macr.repair.spend-plan.v1','run_id':'public-development',
            'ledger_root':str(self.root),'configuration_sha256':digest(self.config),
            'output_contract_sha256':'e'*64,'max_requests':6,'max_usd':'2.000000',
            'max_total_input_tokens':49152,'max_total_output_tokens':6144,
            'expires_at':2000,'cells':[{'cell_id':'raw','binding_sha256':'a'*64},
                                      {'cell_id':'feedback','binding_sha256':'b'*64}]}
        timer=patch.object(spend.time,'time',return_value=1000);timer.start();self.addCleanup(timer.stop)

    def ledger(self):return spend.RepairSpendLedger(self.root,self.plan,self.config)

    def request(self,binding):
        return binding.prepare_request(ModelRequest(messages=[{'role':'user','content':'public fixture'}]))

    def test_durable_known_usage_reloads_and_does_not_reset_between_cells(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            binding=RepairProviderBinding(self.config,transport=Transport([reply()]),spend_ledger=ledger)
            binding.complete(self.request(binding));state=binding.snapshot()
            self.assertEqual(state['schema'],'macr.repair.provider-meter.v2')
            self.assertTrue(state['durable_cross_process_ledger'])
            self.assertEqual(state['spend_commitment']['cell_id'],'raw')
            self.assertEqual(validate_meter(self.config,state,[state['calls'][0]['request_sha256']]),state)
            ledger.finish_cell('completed')
        with self.ledger() as ledger:
            self.assertEqual(ledger.summary()['known_usd'],'0.000008')
            ledger.claim_cell('feedback','b'*64)
            binding=RepairProviderBinding(self.config,transport=Transport([reply()]),spend_ledger=ledger)
            binding.complete(self.request(binding));ledger.finish_cell('completed')
            self.assertEqual((ledger.summary()['requests'],ledger.summary()['known_usd']),
                             (2,'0.000016'))

    def test_reservation_failure_precedes_network_and_local_attempt(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);wire=Transport([reply()])
            binding=RepairProviderBinding(self.config,transport=wire,spend_ledger=ledger)
            with patch.object(ledger,'reserve',side_effect=OSError('fixture disk full')):
                with self.assertRaisesRegex(ProviderError,'^repair_durable_reservation_failed$'):
                    binding.complete(self.request(binding))
            self.assertEqual(wire.calls,[])
            state=binding.snapshot();self.assertEqual(state['requests'],0)
            self.assertTrue(state['durable_stop']);self.assertTrue(state['stopped'])
            self.assertEqual(validate_meter(self.config,state,[]),state)

    def test_unknown_request_is_persisted_and_never_replayed(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);wire=Transport([TimeoutError(),reply()])
            binding=RepairProviderBinding(self.config,transport=wire,spend_ledger=ledger)
            with self.assertRaisesRegex(ProviderError,'^model_timeout$'):
                binding.complete(self.request(binding))
            state=binding.snapshot()
            self.assertEqual(ledger.summary()['unknown_reserved_usd'],'0.315802')
            self.assertTrue(state['durable_stop'])
            self.assertEqual(validate_meter(self.config,state,[state['calls'][0]['request_sha256']]),state)
            with self.assertRaises(ProviderError):binding.complete(self.request(binding))
            self.assertEqual(len(wire.calls),1)
        with self.ledger() as ledger:
            with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('feedback','b'*64)

    def test_global_fingerprint_drift_stops_after_recording_known_charge(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            first=RepairProviderBinding(self.config,transport=Transport([reply()]),spend_ledger=ledger)
            first.complete(self.request(first));ledger.finish_cell('completed')
            ledger.claim_cell('feedback','b'*64)
            second=RepairProviderBinding(self.config,
                transport=Transport([reply(fingerprint='other'),reply()]),spend_ledger=ledger)
            with self.assertRaisesRegex(ProviderError,'^repair_durable_stopped$'):
                second.complete(self.request(second))
            state=second.snapshot()
            self.assertTrue(state['durable_stop']);self.assertTrue(state['stopped'])
            self.assertEqual(ledger.summary()['known_usd'],'0.000016')
            self.assertEqual(ledger.summary()['unknown_reserved_usd'],'0.000000')
            self.assertEqual(validate_meter(self.config,state,[state['calls'][0]['request_sha256']]),state)

    def test_known_provider_failure_settles_independent_billing_without_free_retry(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            binding=RepairProviderBinding(self.config,transport=Transport([reply(finish='length')]),
                                          spend_ledger=ledger)
            with self.assertRaisesRegex(ProviderError,'^model_output_incomplete$'):
                binding.complete(self.request(binding))
            state=binding.snapshot()
            self.assertEqual(ledger.summary()['known_usd'],'0.000008')
            self.assertEqual(ledger.summary()['unknown_reserved_usd'],'0.000000')
            self.assertEqual(validate_meter(self.config,state,[state['calls'][0]['request_sha256']]),state)
            with self.assertRaises(ProviderError):binding.complete(self.request(binding))

    def test_failed_outcome_persistence_keeps_global_reserve_despite_local_usage(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            binding=RepairProviderBinding(self.config,transport=Transport([reply()]),spend_ledger=ledger)
            with patch.object(ledger,'settle',side_effect=OSError('fixture outcome write failed')):
                with self.assertRaisesRegex(ProviderError,'^repair_durable_settlement_unknown$'):
                    binding.complete(self.request(binding))
            state=binding.snapshot()
            self.assertEqual(state['known_usd'],'0.000008')
            self.assertTrue(state['stopped']);self.assertTrue(state['durable_stop'])
            self.assertEqual(validate_meter(self.config,state,[state['calls'][0]['request_sha256']]),state)
        with self.ledger() as ledger:
            summary=ledger.summary()
            self.assertEqual((summary['known_usd'],summary['unknown_reserved_usd']),
                             ('0.000000','0.315802'))
            self.assertTrue(summary['stopped'])
            with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('feedback','b'*64)

    def test_official_transport_requires_durable_claim_and_has_explicit_unattested_provenance(self):
        transport=OfficialRepairTransport(lambda:'DUMMY-NOT-A-REAL-KEY',allow_remote=True)
        with self.assertRaises(ProviderError):RepairProviderBinding(self.config,transport=transport)
        with self.ledger() as ledger:
            with self.assertRaises(ProviderError):
                RepairProviderBinding(self.config,transport=transport,spend_ledger=ledger)
            ledger.claim_cell('raw','a'*64)
            binding=RepairProviderBinding(self.config,transport=transport,spend_ledger=ledger)
            state=binding.snapshot()
            self.assertTrue(state['remote_enabled'])
            self.assertEqual(state['transport_origin'],'official_https_configured_unattested')
            self.assertFalse(state['identity_authenticated'])
            self.assertEqual(validate_meter(self.config,state,[]),state)
            bad=deepcopy(state);bad['spend_commitment']['plan_sha256']='not-a-digest'
            with self.assertRaises(ProviderError):validate_meter(self.config,bad,[])

    def test_settlement_write_failure_preserves_original_safe_provider_evidence(self):
        from macr.providers.deepseek import DeepSeekProvider
        converse=DeepSeekProvider.converse;captured=[]
        def record_error(provider,request):
            try:return converse(provider,request)
            except ProviderError as error:captured.append(error);raise
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            binding=RepairProviderBinding(self.config,transport=Transport([reply(finish='length')]),
                                          spend_ledger=ledger)
            with patch.object(DeepSeekProvider,'converse',record_error), \
                 patch.object(ledger,'settle',side_effect=OSError('fixture write failed')):
                with self.assertRaisesRegex(ProviderError,'^repair_durable_settlement_unknown$') as raised:
                    binding.complete(self.request(binding))
            self.assertEqual(len(captured),1)
            self.assertIsNotNone(captured[0].billing_evidence)
            self.assertIsNotNone(captured[0].identity_evidence)
            for field in ('response','billing_evidence','identity_evidence','diagnostic'):
                self.assertEqual(getattr(raised.exception,field),getattr(captured[0],field))
            self.assertTrue(binding.snapshot()['stopped'])

    def test_settlement_interruption_marks_binding_stopped_and_releases_object_lock(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);wire=Transport([reply(),reply()])
            binding=RepairProviderBinding(self.config,transport=wire,spend_ledger=ledger)
            with patch.object(ledger,'settle',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):binding.complete(self.request(binding))
            state=binding.snapshot()
            self.assertTrue(state['stopped']);self.assertTrue(state['durable_stop'])
            self.assertEqual(validate_meter(self.config,state,[state['calls'][0]['request_sha256']]),state)
            with self.assertRaisesRegex(ProviderError,'^repair_binding_stopped$'):
                binding.complete(self.request(binding))
            self.assertEqual(len(wire.calls),1)
        with self.ledger() as ledger:
            self.assertEqual(ledger.summary()['unknown_reserved_usd'],'0.315802')

    def test_old_binding_cannot_dispatch_under_a_later_cell_claim(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);wire=Transport([reply()])
            binding=RepairProviderBinding(self.config,transport=wire,spend_ledger=ledger)
            ledger.finish_cell('verification_unavailable');ledger.claim_cell('feedback','b'*64)
            with self.assertRaisesRegex(ProviderError,'^repair_durable_reservation_failed$'):
                binding.complete(self.request(binding))
            self.assertEqual(wire.calls,[])
            self.assertEqual(ledger.summary()['requests'],0)
            self.assertEqual(binding.snapshot()['requests'],0)
            self.assertTrue(binding.snapshot()['stopped'])
