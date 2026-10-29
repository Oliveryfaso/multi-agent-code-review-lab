"""Candidate activation boundary exercised with known development fixtures only."""
import importlib
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import warnings

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import development_pack, COMMITMENT
from test_deepseek_policy_runner import UNKNOWN, call, reply
from macr.evals.deepseek_study_profile import prepare_study, StudyError


class StudyLiveTests(unittest.TestCase):
    def live(self):
        self.assertIsNotNone(importlib.util.find_spec('macr.evals.deepseek_study_live'),
            'separate, disabled study activation entry is missing')
        return importlib.import_module('macr.evals.deepseek_study_live')

    def prepare(self, count=9):
        root = fixture_root()
        development_pack(root, count)
        out = root.parent / (root.name + '-study')
        study = prepare_study(root, custodian=COMMITMENT, out=out,
            project_root=Path(__file__).resolve().parents[1])
        return out, study

    def approval(self, live, out):
        proposal = live.propose_study_live_approval(out)
        return proposal | {'authorized': True, 'confirmation_ref': 'fixture-user-confirmation'}

    def scripted(self, study):
        return {cell['cell_id']: ((reply(None, calls=[call('end', 'finish',
            {'diagnosis': UNKNOWN})]),) if cell['strategy'] == 'adaptive_read_3'
            else tuple(reply(json.dumps(UNKNOWN)) for _ in range(3)))
            for cell in study['cells']}

    def test_proposal_is_unapproved_and_binds_new_108_request_scope(self):
        live = self.live()
        out, study = self.prepare()
        proposal = live.propose_study_live_approval(out)
        self.assertFalse(proposal['authorized'])
        self.assertEqual(proposal['confirmation_ref'], '')
        self.assertEqual(proposal['profile_sha256'], study['profile_sha256'])
        self.assertEqual(proposal['max_requests'], 108)
        self.assertEqual(proposal['max_usd'], '2.000000')
        self.assertEqual(len(proposal['sources']), 18)
        self.assertEqual(proposal['cell_ids'], [row['cell_id'] for row in study['cells']])
        self.assertEqual(proposal['log_policy'], {'retention_days': 14, 'upload': False,
            'auto_delete': False, 'purge_requires_user_approval': True})
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_disabled_and_unapproved_entries_never_read_key_or_claim(self):
        live = self.live()
        out, study = self.prepare()
        reads = []
        approval = self.approval(live, out)
        with self.assertRaisesRegex(StudyError, 'study_live_disabled'):
            live.execute_approved_study(out, approval, key_reader=lambda: reads.append(1))
        with self.assertRaisesRegex(StudyError, 'study_live_approval_missing'):
            live.execute_approved_study(out, live.propose_study_live_approval(out),
                enabled=True, key_reader=lambda: reads.append(1))
        self.assertEqual(reads, [])
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_changed_scope_or_old_carry_fields_fail_before_key(self):
        live = self.live()
        out, study = self.prepare()
        approval = self.approval(live, out)
        for change in ({'profile_sha256': '0' * 64}, {'max_requests': 84},
                {'max_usd': '3.000000'}, {'predecessor_batch_sha256': 'a' * 64},
                {'cell_ids': list(reversed(approval['cell_ids']))},
                {'confirmation_ref': ' '}):
            with self.subTest(change=change):
                with self.assertRaises(StudyError):
                    live.execute_approved_study(out, approval | change, enabled=True,
                        key_reader=lambda: self.fail('preflight read a Key'))
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_candidate_rejects_incomplete_grid_and_source_drift(self):
        live = self.live()
        out, study = self.prepare(1)
        with self.assertRaisesRegex(StudyError, 'study_live_grid_invalid'):
            live.propose_study_live_approval(out)
        out, study = self.prepare()
        approval = self.approval(live, out)
        (Path(study['pack_root']) / 'snapshots/dev0/helper.py').write_text('changed\n')
        with self.assertRaises(StudyError):
            live.execute_approved_study(out, approval, enabled=True,
                key_reader=lambda: self.fail('drift read a Key'))

    def test_profile_replaced_after_preflight_cannot_reach_key_ingress(self):
        live = self.live()
        out, study = self.prepare()
        approval = self.approval(live, out)
        original = live._run_study_core
        def replace_before_claim(*args, **kwargs):
            from macr.evals.deepseek_study_profile import digest
            changed = json.loads((out / 'study.json').read_text())
            changed['cells'] = list(reversed(changed['cells']))
            changed['profile_sha256'] = digest({key: value for key, value in changed.items()
                if key != 'profile_sha256'})
            (out / 'study.json').write_text(json.dumps(changed))
            return original(*args, **kwargs)
        with patch.object(live, '_run_study_core', side_effect=replace_before_claim):
            with self.assertRaisesRegex(StudyError, 'study_live_profile_changed'):
                live.execute_approved_study(out, approval, enabled=True,
                    key_reader=lambda: self.fail('replaced profile read a Key'),
                    scripted_by_cell=self.scripted(study))
        self.assertTrue((out / 'execution.claim.json').is_file())

    def test_fake_execution_claims_before_single_key_read_and_cannot_repeat(self):
        live = self.live()
        out, study = self.prepare()
        approval = self.approval(live, out)
        reads = []
        def key():
            self.assertTrue((out / 'execution.claim.json').is_file())
            self.assertTrue((out / 'retention.json').is_file())
            reads.append(1)
            return 'fixture-only-key'
        result = live.execute_approved_study(out, approval, enabled=True,
            key_reader=key, scripted_by_cell=self.scripted(study))
        self.assertEqual(reads, [1])
        self.assertEqual(result['accounting']['request_count'], 90)
        self.assertEqual(result['model_calls_live'], 0)
        self.assertEqual(result['provenance'], 'approved_live_fake')
        self.assertFalse(result['batch_halted'])
        retention = json.loads((out / 'retention.json').read_text())
        self.assertEqual(retention['profile_sha256'], study['profile_sha256'])
        self.assertEqual(retention['retention_days'], 14)
        self.assertFalse(retention['auto_delete'])
        self.assertNotIn('fixture-only-key', (out / 'summary.json').read_text())
        with self.assertRaisesRegex(StudyError, 'execution_already_claimed'):
            live.execute_approved_study(out, approval, enabled=True,
                key_reader=lambda: self.fail('second execution read a Key'),
                scripted_by_cell=self.scripted(study))

    def test_unknown_fake_outcome_holds_reservation_and_stops(self):
        live = self.live()
        out, study = self.prepare()
        scripts = self.scripted(study)
        scripts[study['cells'][0]['cell_id']] = ('timeout',)
        result = live.execute_approved_study(out, self.approval(live, out), enabled=True,
            key_reader=lambda: 'fixture-only-key', scripted_by_cell=scripts)
        self.assertEqual(result['accounting']['request_count'], 1)
        self.assertEqual(result['accounting']['unknown_held_usd'], '0.330000')
        self.assertTrue(result['batch_halted'])
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in result['rows']), 35)

    def test_parallel_activation_gets_one_claim_and_one_key_ingress(self):
        live = self.live()
        out, study = self.prepare()
        approval = self.approval(live, out)
        entered, release = threading.Event(), threading.Event()
        outcomes, failures = [], []
        def key():
            entered.set()
            if not release.wait(5):
                raise AssertionError('fixture release missing')
            return 'fixture-only-key'
        def execute():
            try:
                outcomes.append(live.execute_approved_study(out, approval, enabled=True,
                    key_reader=key, scripted_by_cell=self.scripted(study)))
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=execute)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(StudyError, 'execution_already_claimed'):
                live.execute_approved_study(out, approval, enabled=True,
                    key_reader=lambda: self.fail('parallel execution read a Key'),
                    scripted_by_cell=self.scripted(study))
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(outcomes), 1)

    def test_failed_key_ingress_keeps_execution_claim_and_never_retries(self):
        live = self.live()
        out, study = self.prepare()
        approval = self.approval(live, out)
        reads = []
        def key():
            reads.append(1)
            raise ValueError('fixture secret must not escape')
        result = live.execute_approved_study(out, approval, enabled=True,
            key_reader=key, scripted_by_cell=self.scripted(study))
        self.assertEqual(reads, [1])
        self.assertTrue(result['batch_halted'])
        self.assertEqual(result['accounting']['request_count'], 0)
        self.assertEqual(result['rows'][0]['error_code'], 'study_live_key_input_failed')
        self.assertNotIn('fixture secret', (out / 'summary.json').read_text())
        with self.assertRaisesRegex(StudyError, 'execution_already_claimed'):
            live.execute_approved_study(out, approval, enabled=True,
                key_reader=lambda: self.fail('failed ingress retried a Key'),
                scripted_by_cell=self.scripted(study))

    def test_scripted_inputs_cannot_smuggle_callable_transport(self):
        live = self.live()
        out, study = self.prepare()
        scripts = self.scripted(study)
        scripts[study['cells'][0]['cell_id']] = (lambda: None,)
        with self.assertRaisesRegex(StudyError, 'study_live_script_invalid'):
            live.execute_approved_study(out, self.approval(live, out), enabled=True,
                key_reader=lambda: self.fail('invalid script read a Key'), scripted_by_cell=scripts)

    def test_wire_requires_single_exact_admitted_body_and_endpoint(self):
        live = self.live()
        from hashlib import sha256
        from macr.providers.base import ProviderError
        wire = live._BoundWire(live._ScriptedWire((reply(json.dumps(UNKNOWN)),)), simulated=True)
        body = b'{"model":"deepseek-flash"}'
        wire.expected_hash = sha256(body).hexdigest()
        with self.assertRaisesRegex(ProviderError, 'model_request_invalid'):
            wire.post('https://elsewhere.invalid/chat/completions', body, 1, 4096)
        with self.assertRaisesRegex(ProviderError, 'model_request_invalid'):
            wire.post(live.ENDPOINT, body + b' ', 1, 4096)
        wire.post(live.ENDPOINT, body, 1, 4096)
        with self.assertRaisesRegex(ProviderError, 'model_request_invalid'):
            wire.post(live.ENDPOINT, body, 1, 4096)

    def test_cli_defaults_to_proposal_and_rejects_non_tty_before_key(self):
        live = self.live()
        spec = importlib.util.spec_from_file_location('study_live_cli',
            Path(__file__).resolve().parents[1] / 'scripts/run_deepseek_study_live.py')
        self.assertIsNotNone(spec)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out, study = self.prepare()
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(['--control', str(out)]), 0)
        self.assertFalse(json.loads(output.getvalue())['authorized'])
        approval = out / 'fixture-approval.json'
        approval.write_text(json.dumps(self.approval(live, out)))
        with patch.object(cli.sys.stdin, 'isatty', return_value=False), \
                patch.object(cli, '_read_key_without_echo', side_effect=AssertionError('Key read')), \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(cli.main(['--control', str(out), '--approval', str(approval),
                '--execute', '--enable-live']), 2)
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_cli_marks_halted_execution_failed_and_rejects_echo_fallback(self):
        self.live()
        spec = importlib.util.spec_from_file_location('study_live_cli_exit',
            Path(__file__).resolve().parents[1] / 'scripts/run_deepseek_study_live.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(cli.getpass, 'getpass', side_effect=lambda *args, **kwargs:
                warnings.warn('echo unavailable', cli.getpass.GetPassWarning)):
            with self.assertRaisesRegex(StudyError, 'study_live_no_echo_input_unavailable'):
                cli._read_key_without_echo()
        out, study = self.prepare()
        approval = out / 'fixture-approval.json'
        approval.write_text(json.dumps(self.approval(self.live(), out)))
        with patch.object(cli.sys.stdin, 'isatty', return_value=True), \
                patch.object(cli.sys.stderr, 'isatty', return_value=True), \
                patch.object(cli, 'execute_approved_study', return_value={'batch_halted': True}), \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(cli.main(['--control', str(out), '--approval', str(approval),
                '--execute', '--enable-live']), 1)
