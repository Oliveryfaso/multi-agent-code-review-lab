"""Trusted control-flow tests; fixture Python is never imported or executed."""
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from repair_fixtures import SOURCES, OLD, NEW
from macr.investigation import repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.base import ProviderError


class RepairLoopTests(TestCase):
    def setUp(self):
        self.files = {n: s.encode() for n, s in SOURCES.items()}
        self.repo = write_files(fixture_root(), SOURCES)
        self.out = fixture_root() / 'session'
        self.tests = loop.test_manifest(self.files, ['checks.py'])

    def proposal(self, old=OLD, new=NEW):
        return {'status': 'candidate', 'summary': 'scripted development edit',
                'edits': [{'file': 'builder.py', 'old': old, 'new': new}]}

    def observation(self, files, failed=(), status='completed'):
        return {'source_id': loop.source_id(files), 'execution_status': status,
                'outcomes': {n: ('failed' if n in failed else 'passed') for n in self.tests}
                    if status == 'completed' else {}}

    def run_loop(self, replies, observations=None, **kwargs):
        self.provider = loop.ScriptedPatchProvider(replies)
        self.verifier = None if observations is None else loop.ReplayVerifier(observations)
        return loop.run_simulated_loop(self.repo, 'Discount applies only to items.',
            editable_paths=['app.py', 'builder.py', 'records.py'], test_paths=['checks.py'],
            provider=self.provider, verifier=self.verifier, out=self.out, **kwargs)

    def test_failed_candidate_feedback_is_in_next_serialized_request(self):
        wrong = deepcopy(self.files); wrong['builder.py'] = wrong['builder.py'].replace(OLD.encode(), b'return 2000')
        fixed = deepcopy(wrong); fixed['builder.py'] = fixed['builder.py'].replace(b'return 2000', NEW.encode())
        fail = self.tests[0]
        result = self.run_loop([self.proposal(new='return 2000'), self.proposal(old='return 2000')],
            [self.observation(self.files, [fail]), self.observation(wrong, self.tests[1:]),
             self.observation(fixed)])
        self.assertEqual(result['status'], 'simulation_passed')
        self.assertEqual(result['proposal_requests'], 2)
        self.assertFalse(result['repaired']); self.assertFalse(result['target_code_executed'])
        self.assertEqual(result['candidate_origin'], 'scripted_provider')
        self.assertEqual(result['verification_origin'], 'synthetic_replay')
        payload = json.loads(self.provider.requests[1]['messages'][1]['content'])
        self.assertIn('return 2000', payload['files']['builder.py'])
        self.assertEqual(payload['feedback']['outcomes'][self.tests[1]], 'failed')
        self.assertEqual(payload['feedback']['source_id'], loop.source_id(wrong))
        self.assertEqual(result, loop.verify_loop(self.out))
        self.assertEqual((self.repo/'builder.py').read_bytes(), self.files['builder.py'])
        for n in (0, 1, 2):
            self.assertEqual((self.out/f'sources/{n}/checks.py').read_bytes(), self.files['checks.py'])

    def test_default_unavailable_validation_makes_no_proposal_or_repair(self):
        r = self.run_loop([self.proposal()])
        self.assertEqual(r['status'], 'verification_unavailable')
        self.assertEqual(self.provider.requests, [])
        self.assertFalse(r['repaired'])

    def test_invalid_test_edit_is_retained_and_never_validated(self):
        p = self.proposal(); p['edits'][0]['file'] = 'checks.py'
        r = self.run_loop([p], [self.observation(self.files, [self.tests[0]])])
        self.assertEqual(r['status'], 'proposal_invalid')
        self.assertEqual(self.verifier.calls, 1)
        self.assertTrue((self.out/'rounds/1/response.json').is_file())

    def test_unknown_provider_is_not_retried_or_relaunchable(self):
        r = self.run_loop([ProviderError('model_transport'), self.proposal()],
                          [self.observation(self.files, [self.tests[0]])])
        self.assertEqual(r['status'], 'provider_outcome_unknown')
        self.assertEqual(r['proposal_requests'], 1)
        with self.assertRaises(ContractError):
            self.run_loop([self.proposal()], [])

    def test_unknown_verification_stops_before_next_model_request(self):
        fixed = deepcopy(self.files); fixed['builder.py'] = fixed['builder.py'].replace(OLD.encode(), NEW.encode())
        r = self.run_loop([self.proposal(), self.proposal()],
            [self.observation(self.files, [self.tests[0]]), self.observation(fixed, status='outcome_unknown')])
        self.assertEqual(r['status'], 'verification_outcome_unknown')
        self.assertEqual(r['proposal_requests'], 1)

    def test_wrong_source_or_incomplete_replay_cannot_pass(self):
        for field in ('source_id', 'outcomes'):
            with self.subTest(field=field):
                self.out = fixture_root()/'session'
                o = self.observation(self.files, [self.tests[0]])
                o[field] = '0'*64 if field == 'source_id' else {}
                r = self.run_loop([self.proposal()], [o])
                self.assertEqual(r['status'], 'verification_invalid')
                self.assertEqual(r['proposal_requests'], 0)

    def test_tamper_with_saved_response_or_claimed_success_is_rejected(self):
        self.run_loop([], [self.observation(self.files)])
        p = self.out/'report.json'; raw = json.loads(p.read_text()); raw['repaired'] = True
        p.write_text(json.dumps(raw))
        with self.assertRaises(ContractError): loop.verify_loop(self.out)

    def test_terminal_status_usage_and_reservation_are_rederived(self):
        self.run_loop([], [self.observation(self.files)])
        p = self.out/'report.json'; original = json.loads(p.read_text())
        for field, value in (('status', 'simulation_passed'), ('input_tokens', 9),
                             ('unknown_request_reservation', True)):
            with self.subTest(field=field):
                bad = deepcopy(original); bad[field] = value; p.write_text(json.dumps(bad))
                with self.assertRaises(ContractError): loop.verify_loop(self.out)
        p.write_text(json.dumps(original))

    def test_feedback_cannot_contradict_normalized_observation_even_if_rehashed(self):
        self.run_loop([], [self.observation(self.files)])
        p = self.out/'report.json'; report = json.loads(p.read_text())
        receipt = report['checks'][0]; receipt['outcomes'][self.tests[0]] = 'failed'
        (self.out/'checks/0/receipt.json').write_text(json.dumps(receipt))
        report['artifact_hashes'] = loop._file_hashes(self.out, skip=('report.json',))
        p.write_text(json.dumps(report))
        with self.assertRaises(ContractError): loop.verify_loop(self.out)

    def test_three_failed_proposals_end_at_round_limit_and_no_fourth(self):
        files = self.files; observations = [self.observation(files, self.tests)]
        replies = []
        previous = OLD
        for value in ('return 2000', 'return 2001', 'return 2002'):
            replies.append(self.proposal(old=previous, new=value))
            files = deepcopy(files); files['builder.py'] = files['builder.py'].replace(previous.encode(), value.encode())
            observations.append(self.observation(files, self.tests)); previous = value
        r = self.run_loop(replies, observations)
        self.assertEqual(r['status'], 'round_limit'); self.assertEqual(r['proposal_requests'], 3)

    def test_no_candidate_is_preserved_as_abstention_without_validation(self):
        r = self.run_loop([{'status':'insufficient_evidence', 'summary':'Need a real trace.', 'edits':[]}],
                         [self.observation(self.files, self.tests)])
        self.assertEqual(r['status'], 'insufficient_evidence')
        self.assertEqual(self.verifier.calls, 1)

    def test_wrong_proposal_source_binding_stops(self):
        p = self.proposal(); p['base_content_id'] = '0'*64
        r = self.run_loop([p], [self.observation(self.files, self.tests)])
        self.assertEqual(r['status'], 'proposal_invalid')

    def test_real_cli_demo_is_scripted_and_cannot_repeat(self):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_repair_loop_demo.py'))
        with (patch('subprocess.run', side_effect=AssertionError('no target process')),
              patch('socket.create_connection', side_effect=AssertionError('no network'))):
            for action, code in (('simulate', 0), ('verify', 0), ('simulate', 2)):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = namespace['main']([action, '--out', str(self.out)])
                self.assertEqual(result, code)
                report = json.loads(output.getvalue()); self.assertFalse(report['target_code_executed'])
            saved = self.out/'report.json'; report = json.loads(saved.read_text()); del report['repaired']
            saved.write_text(json.dumps(report))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(namespace['main'](['verify', '--out', str(self.out)]), 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'rejected')

    def test_input_token_reservation_prevents_overbudget_dispatch(self):
        r = self.run_loop([self.proposal()], [self.observation(self.files, [self.tests[0]])],
                          max_total_input_tokens=1)
        self.assertEqual(r['status'], 'budget_exhausted')
        self.assertEqual(r['proposal_requests'], 0)

    def test_non_scripted_provider_rejected_before_output_or_dispatch(self):
        with self.assertRaises(ContractError):
            loop.run_simulated_loop(self.repo, 'issue', editable_paths=['builder.py'],
                test_paths=['checks.py'], provider=object(), verifier=None, out=self.out)
        self.assertFalse(self.out.exists())
