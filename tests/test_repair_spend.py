"""Deterministic ledger tests: no HTTP, credentials, SDK or target execution."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from macr.providers.repair_binding import development_config
try:
    from macr.providers import repair_spend as spend
except ImportError:
    spend = None


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class RepairSpendTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(spend, 'persistent repair spend ledger is missing')
        self.temp = tempfile.TemporaryDirectory(prefix='repair-spend-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'ledger'
        self.config = development_config()
        self.plan = {'schema':'macr.repair.spend-plan.v1','run_id':'development-only',
            'ledger_root':str(self.root),'configuration_sha256':digest(self.config),
            'output_contract_sha256':'e'*64,'max_requests':6,'max_usd':'2.000000',
            'max_total_input_tokens':49152,'max_total_output_tokens':6144,
            'expires_at':2000,'cells':[{'cell_id':'raw','binding_sha256':'a'*64},
                                     {'cell_id':'debugger','binding_sha256':'b'*64}]}
        clock = patch.object(spend.time, 'time', return_value=1000)
        clock.start();self.addCleanup(clock.stop)

    def ledger(self, root=None, plan=None):
        return spend.RepairSpendLedger(root or self.root, plan or self.plan, self.config)

    def call(self, number=1):
        # ceil((1,048,576*.30 + 1,024*1.20)/1M USD) =315,802 microUSD.
        return {'number':number,'request_sha256':str(number)*64,'wire_sha256':'c'*64,
            'reserved_micro_usd':315802,'reservation_retained':True,
            'known_micro_usd':0,'usage':{},'identity':None,'billing_evidence':None,
            'identity_evidence':None,'diagnostic':None,'status':'dispatch_intent',
            'provider_seconds':0.0}

    def settled(self, call=None, *, fingerprint='fixture-fingerprint', inputs=100, outputs=10):
        call = deepcopy(call or self.call())
        call.update(reservation_retained=False,known_micro_usd=42,
            usage={'input_tokens':inputs,'output_tokens':outputs},
            identity={'model':'deepseek-flash','system_fingerprint':fingerprint,
                'model_state':'validated','fingerprint_state':'validated','identity_matches_config':True},
            status='response_recorded')
        return call

    def test_known_prefix_reopens_without_resetting_global_cost_or_fingerprint(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            ledger.settle(self.settled());ledger.finish_cell('completed')
        with self.ledger() as ledger:
            self.assertEqual(ledger.summary()['known_usd'],'0.000042')
            ledger.claim_cell('debugger','b'*64);ledger.reserve(self.call())
            ledger.settle(self.settled());ledger.finish_cell('completed')
            state=ledger.summary()
            self.assertEqual((state['requests'],state['known_usd'],state['input_tokens']),
                             (2,'0.000084',200))
            self.assertEqual(state['expected_fingerprint'],'fixture-fingerprint')
            self.assertFalse(state['stopped'])

    def test_incomplete_request_keeps_reservation_and_blocks_other_process(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
        with self.ledger() as ledger:
            self.assertTrue(ledger.summary()['stopped'])
            self.assertEqual(ledger.summary()['unknown_reserved_usd'],'0.315802')
            with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('debugger','b'*64)
            with self.assertRaises(spend.RepairSpendError):ledger.settle(self.settled())

    def test_identity_drift_is_known_cost_and_durable_stop(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            ledger.settle(self.settled());ledger.finish_cell('completed')
            ledger.claim_cell('debugger','b'*64);ledger.reserve(self.call())
            state=ledger.settle(self.settled(fingerprint='different'))
            self.assertTrue(state['stopped'])
            self.assertEqual((state['known_usd'],state['unknown_reserved_usd']),('0.000084','0.000000'))
            ledger.finish_cell('failed')
        with self.ledger() as ledger:self.assertTrue(ledger.summary()['stopped'])

    def test_untrusted_usage_retains_reserve_and_cannot_be_reclassified_free(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            bad=self.settled();bad['usage']['input_tokens']=True
            self.assertTrue(ledger.settle(bad)['stopped'])
            self.assertEqual(ledger.summary()['unknown_reserved_usd'],'0.315802')

    def test_global_request_and_money_and_token_budgets_block_before_reservation(self):
        for field,value in [('max_requests',1),('max_usd','0.315820'),
                            ('max_total_input_tokens',8192),('max_total_output_tokens',1024)]:
            with self.subTest(field=field):
                root=self.root.with_name('ledger-'+field);plan=deepcopy(self.plan)
                plan.update(ledger_root=str(root));plan[field]=value
                with self.ledger(root,plan) as ledger:
                    ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
                    ledger.settle(self.settled());ledger.finish_cell('completed')
                    ledger.claim_cell('debugger','b'*64)
                    with self.assertRaises(spend.RepairSpendError):ledger.reserve(self.call())
                    self.assertEqual(ledger.summary()['requests'],1)

    def test_actual_overrun_is_recorded_without_clipping_and_stops(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            state=ledger.settle(self.settled(inputs=1000000,outputs=20000))
            self.assertTrue(state['stopped'])
            self.assertEqual((state['input_tokens'],state['output_tokens'],state['known_usd']),
                             (1000000,20000,'0.324000'))

    def test_claim_number_and_request_binding_are_not_reusable(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            with self.assertRaises(spend.RepairSpendError):ledger.reserve(self.call(2))
            ledger.reserve(self.call());ledger.settle(self.settled());ledger.finish_cell('failed')
            with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('raw','a'*64)
        with self.ledger() as ledger:
            with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('raw','a'*64)

    def test_copied_root_and_changed_plan_do_not_reset_budget(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.finish_cell('failed')
        copied=self.root.with_name('copy');shutil.copytree(self.root,copied)
        plan=deepcopy(self.plan);plan['ledger_root']=str(copied)
        with self.assertRaises(spend.RepairSpendError):
            with self.ledger(copied,plan):pass
        plan=deepcopy(self.plan);plan['max_usd']='3.000000'
        with self.assertRaises(spend.RepairSpendError):
            with self.ledger(plan=plan):pass

    def test_second_file_descriptor_cannot_enter_while_first_owns_cell(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            with self.assertRaises(spend.RepairSpendError):
                with self.ledger():pass

    def test_expiry_prevents_new_intent_but_retains_late_settlement(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            with patch.object(spend.time,'time',return_value=2001):
                ledger.settle(self.settled());ledger.finish_cell('completed')
                with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('debugger','b'*64)
            self.assertEqual(ledger.summary()['known_usd'],'0.000042')

    def test_failed_durable_reservation_never_reaches_external_io(self):
        network=[]
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            with patch.object(spend.os,'fsync',side_effect=OSError('synthetic disk error')):
                with self.assertRaises(spend.RepairSpendError):
                    ledger.reserve(self.call());network.append('must-not-dispatch')
        self.assertEqual(network,[])

    def test_symlink_root_and_leaf_are_rejected_without_following(self):
        real=self.root.with_name('real');real.mkdir();self.root.symlink_to(real,target_is_directory=True)
        with self.assertRaises(spend.RepairSpendError):
            with self.ledger():pass

    def test_strict_plan_rejects_unknown_fields_bool_limits_and_duplicate_cells(self):
        for change in [{'unexpected':True},{'max_requests':True},
                       {'cells':self.plan['cells']*2},{'configuration_sha256':'invalid'}]:
            value=deepcopy(self.plan);value.update(change)
            with self.assertRaises(spend.RepairSpendError):spend.validate_spend_plan(value)

    def test_expired_known_ledger_remains_readable_without_new_intent(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            ledger.settle(self.settled());ledger.finish_cell('completed')
        with patch.object(spend.time,'time',return_value=2001):
            with self.ledger() as ledger:
                self.assertEqual(ledger.summary()['known_usd'],'0.000042')
                with self.assertRaises(spend.RepairSpendError):ledger.claim_cell('debugger','b'*64)

    def test_nested_entry_does_not_release_original_writer_lock(self):
        with self.ledger() as ledger:
            with self.assertRaises(spend.RepairSpendError):ledger.__enter__()
            with self.assertRaises(spend.RepairSpendError):
                with self.ledger():pass
            ledger.claim_cell('raw','a'*64);ledger.finish_cell('failed')

    def test_intent_or_unknown_status_cannot_release_reservation_with_claimed_usage(self):
        for status in ('dispatch_intent','outcome_unknown'):
            root=self.root.with_name('ledger-'+status);plan=deepcopy(self.plan)
            plan['ledger_root']=str(root)
            with self.ledger(root,plan) as ledger:
                ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
                call=self.settled();call['status']=status
                state=ledger.settle(call)
                self.assertTrue(state['stopped'])
                self.assertEqual((state['known_usd'],state['unknown_reserved_usd']),
                                 ('0.000000','0.315802'))

    def test_known_processing_failure_keeps_actual_charge_but_stops_batch(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            call=self.settled();call['status']='processing_failed'
            state=ledger.settle(call)
            self.assertTrue(state['stopped'])
            self.assertEqual(state['known_usd'],'0.000042')

    def test_replaced_lock_and_symlink_record_cannot_authorize_dispatch(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64)
            (self.root/'writer.lock').unlink()
            (self.root/'writer.lock').touch(mode=0o600)
            with self.assertRaises(spend.RepairSpendError):ledger.reserve(self.call())
        other=self.root.with_name('symlink-leaf');plan=deepcopy(self.plan);plan['ledger_root']=str(other)
        with self.ledger(other,plan) as ledger:
            ledger.claim_cell('raw','a'*64)
            outside=other.parent/'do-not-write';outside.write_text('unchanged')
            (other/'request-0001.reserve.json').symlink_to(outside)
            with self.assertRaises(spend.RepairSpendError):ledger.reserve(self.call())
            self.assertEqual(outside.read_text(),'unchanged')

    def test_recovered_boolean_sequence_is_not_an_integer_call(self):
        with self.ledger() as ledger:
            ledger.claim_cell('raw','a'*64);ledger.reserve(self.call())
            ledger.settle(self.settled());ledger.finish_cell('completed')
        path=self.root/'request-0001.reserve.json';row=json.loads(path.read_text())
        row['sequence']=True;path.write_text(json.dumps(row))
        # Rebind the corresponding self-asserted hash: strict scalar types must
        # still reject it, even when an editable local digest was changed too.
        outcome=self.root/'request-0001.outcome.json';data=json.loads(outcome.read_text())
        data['reservation_sha256']=digest(row);outcome.write_text(json.dumps(data))
        with self.assertRaises(spend.RepairSpendError):
            with self.ledger():pass


if __name__=='__main__':unittest.main()
