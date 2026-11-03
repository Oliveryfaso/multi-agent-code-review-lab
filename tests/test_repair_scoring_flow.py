"""Pure data-only scoring handoff tests; no target, model or hidden files are read."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from macr.evals import repair_comparison as comparison
from macr.investigation.repair_edits import apply_edits
from macr.investigation.repair_loop import source_id


class RepairScoringFlowTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((Path(__file__).resolve().parents[1] /
                              'examples/repair_comparison_development.json').read_text())
        self.files = {'app.py': b'def value():\n    return 1\n',
                      'checks.py': b'import unittest\nclass Check(unittest.TestCase):\n'
                                   b'    def test_value(self):\n        self.assertTrue(True)\n'}
        self.edits = [{'file': 'app.py', 'old': 'return 1', 'new': 'return 2'}]
        self.changed = apply_edits(self.files, self.edits, editable_paths=['app.py'])
        self.spec['tasks'][0]['snapshot_sha256'] = source_id(self.files)
        self.spec['tasks'][0]['visible_tests_sha256'] = source_id({'checks.py': self.files['checks.py']})
        self.plan = comparison.compile_comparison(self.spec)
        self.cell = self.plan['cells'][0]
        self.output = {'status': 'candidate', 'edits': self.edits}
        self.receipt = {'origin': 'simulation', 'target_code_executed': False,
                        'claimed_status': 'passed'}
        self.row = comparison._empty(self.spec['tasks'][0], self.spec)
        self.row.update(status='answer_recorded', candidate_origin='simulation',
            verification_origin='simulation', verdict='defect', model_identity_sha256='a'*64,
            requests=1, input_tokens=20, output_tokens=10, known_usd='0.001000',
            final_output_sha256=comparison.result_digest(self.output),
            candidate_sha256=source_id(self.changed),
            verification_receipt_sha256=comparison.result_digest(self.receipt))

    def packet(self, **overrides):
        options = {'original_files': self.files, 'editable_paths': ['app.py'],
            'protected_paths': ['checks.py'], 'edits': self.edits,
            'final_output': self.output, 'verification_receipt': self.receipt}
        options.update(overrides)
        return comparison.score_packet(self.plan, self.cell['cell_id'], self.row, **options)

    def test_generation_view_excludes_scoring_and_is_a_detached_policy_capsule(self):
        view = comparison.generation_view(self.plan, self.cell['task_id'])
        self.assertEqual(set(view), {'schema', 'task', 'model', 'limits'})
        self.assertEqual(view['task'], self.spec['tasks'][0])
        encoded = json.dumps(view)
        for key in ('hidden_tests', 'rubric', 'custodian', 'scoring'):
            self.assertNotIn(key, encoded)
        view['limits']['max_requests'] = 8
        self.assertEqual(self.plan['spec']['limits']['max_requests'], 3)
        with self.assertRaises(ValueError): comparison.generation_view(self.plan, 'missing-task')

    def test_hidden_content_and_paths_are_not_valid_generation_contract_fields(self):
        for container, field, value in [('spec', 'gold', {'answer': 'not-a-real-gold'}),
                ('task', 'hidden_tests_path', '/not-opened/private.py'),
                ('scoring', 'hidden_content', 'SELF_AUTHORED_SECRET_SENTINEL')]:
            bad = deepcopy(self.plan)
            target = bad['spec'] if container == 'spec' else (
                bad['spec']['tasks'][0] if container == 'task' else bad['spec']['scoring'])
            target[field] = value
            with self.subTest(container=container), self.assertRaises(ValueError):
                comparison.generation_view(bad, self.cell['task_id'])

    def test_score_packet_binds_source_edit_output_and_receipt_but_never_awards_credit(self):
        original = deepcopy(self.files)
        packet = self.packet()
        self.assertEqual(packet['result'], self.row)
        self.assertEqual(packet['candidate_files']['checks.py'].encode(), original['checks.py'])
        self.assertEqual(packet['original_source_id'], source_id(original))
        self.assertEqual(self.files, original)
        self.assertFalse(packet['semantic_truth_validated'])
        self.assertFalse(packet['provenance_authenticated'])
        report = comparison.build_report(self.plan, {self.cell['cell_id']: packet['result']})
        self.assertEqual(report['arms']['direct_api']['scored'], 0)
        self.assertEqual(report['arms']['direct_api']['outcomes']['full_repair'], 0)
        self.assertEqual(report['planned_cells'], 2)

    def test_candidate_cannot_modify_protected_tests_or_add_files(self):
        for change in ([{'file':'checks.py', 'old':'assertTrue(True)', 'new':'assertTrue(False)'}],
                       [{'file':'other.py', 'old':'return 1', 'new':'return 2'}]):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.packet(edits=change)
        with self.assertRaises(ValueError):
            self.packet(editable_paths=['app.py', 'checks.py'])

    def test_scoring_handoff_rejects_mismatched_source_candidate_output_or_receipt(self):
        bad_source = dict(self.files, **{'app.py': b'def value():\n    return 8\n'})
        for options in ({'original_files': bad_source}, {'edits': None},
                        {'final_output': {'status':'unknown'}},
                        {'verification_receipt': {'claimed_status':'passed'}},
                        {'protected_paths': ['app.py']}):
            with self.subTest(options=list(options)), self.assertRaises(ValueError):
                self.packet(**options)

    def test_applied_edit_must_be_the_edit_in_the_bound_model_output(self):
        self.output = {'status':'candidate', 'edits':[{'file':'app.py', 'old':'return 1', 'new':'return 3'}]}
        self.row['final_output_sha256'] = comparison.result_digest(self.output)
        with self.assertRaises(ValueError): self.packet(final_output=self.output)
        self.output = {'status':'no_candidate', 'edits':[]}
        self.row['final_output_sha256'] = comparison.result_digest(self.output)
        with self.assertRaises(ValueError): self.packet(final_output=self.output)

    def test_packet_cannot_import_an_inline_locked_review(self):
        self.row['review'] = {'status': 'locked'}
        with self.assertRaises(ValueError): self.packet()

    def test_missing_unrun_and_timeout_keep_the_full_denominator(self):
        empty = comparison.build_report(self.plan, {})
        self.assertEqual(empty['planned_cells'], 2)
        self.assertEqual(empty['pairs']['not_comparable'], 1)
        timeout = deepcopy(self.row)
        timeout.update(status='verification_timeout', verdict=None,
                       candidate_origin='model', verification_origin='isolated_execution')
        report = comparison.build_report(self.plan, {self.cell['cell_id']: timeout})
        self.assertEqual(report['arms']['direct_api']['statuses']['verification_timeout'], 1)
        self.assertEqual(report['arms']['api_debugger']['statuses']['not_attempted'], 1)
        self.assertEqual(report['arms']['direct_api']['scored'], 0)
        self.assertEqual(report['pairs']['not_comparable'], 1)
        timeout['review'] = {'status': 'locked'}
        with self.assertRaises(ValueError):
            comparison.build_report(self.plan, {self.cell['cell_id']: timeout})

    def test_unrun_cannot_contain_artifacts_or_validation_attempts(self):
        for key, value in [('final_output_sha256', 'b'*64), ('candidate_sha256', 'b'*64),
                           ('verification_receipt_sha256', 'b'*64),
                           ('feedback_validation_attempts', 1)]:
            for status in ('not_attempted', 'skipped'):
                row = comparison._empty(self.spec['tasks'][0], self.spec)
                row.update(status=status); row[key] = value
                with self.subTest(key=key, status=status), self.assertRaises(ValueError):
                    comparison.build_report(self.plan, {self.cell['cell_id']: row})

    def test_pre_dispatch_verifier_unavailable_retains_zero_call_failure(self):
        row = comparison._empty(self.spec['tasks'][0], self.spec)
        row.update(status='verification_unavailable', verification_origin='unavailable')
        report = comparison.build_report(self.plan, {self.cell['cell_id']: row})
        self.assertEqual(report['arms']['direct_api']['statuses']['verification_unavailable'], 1)
        self.assertEqual(report['arms']['direct_api']['requests'], 0)
        self.assertEqual(report['arms']['direct_api']['scored'], 0)
        self.assertEqual(report['planned_cells'], 2)
        for key, value in [('input_tokens', 1), ('known_usd', '0.001000'),
                           ('model_identity_sha256', 'a'*64), ('candidate_origin','model'),
                           ('final_output_sha256', 'b'*64), ('feedback_validation_attempts', 1)]:
            bad = deepcopy(row); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                comparison.build_report(self.plan, {self.cell['cell_id']: bad})
        row['status'] = 'verification_timeout'
        row['verification_origin'] = 'isolated_execution'
        with self.assertRaises(ValueError):
            comparison.build_report(self.plan, {self.cell['cell_id']: row})

    def test_pre_dispatch_timeout_preserves_failure_latency_with_no_model_use(self):
        for origin in ('simulation', 'unavailable'):
            timeout_receipt={'origin':origin,'execution_status':'timeout',
                             'source_id':source_id(self.files),'target_code_executed':False}
            row = comparison._empty(self.spec['tasks'][0], self.spec)
            row.update(status='verification_timeout', verification_origin=origin,
                       feedback_validation_attempts=1,
                       verification_receipt_sha256=comparison.result_digest(timeout_receipt),
                       timing_ms={'preparation':2,'provider':0,'verification':8,'total':10})
            report = comparison.build_report(self.plan, {self.cell['cell_id']:row})
            stats = report['arms']['direct_api']
            self.assertEqual(stats['statuses']['verification_timeout'],1)
            self.assertEqual(stats['requests'],0)
            self.assertEqual(stats['timing_ms']['verification'],8)
            self.assertEqual(stats['scored'],0)
            self.assertEqual(report['planned_cells'],2)
            self.assertEqual(report['pairs']['not_comparable'],1)
            self.row=row
            packet=self.packet(edits=None,final_output=None,verification_receipt=timeout_receipt)
            self.assertEqual(packet['verification_receipt'],timeout_receipt)
            self.assertIsNone(packet['candidate_files'])
            for key, value in [('input_tokens',1),('output_tokens',1),('known_usd','0.001000'),
                    ('unknown_reserved_usd','0.001000'),('model_identity_sha256','a'*64),
                    ('final_output_sha256','b'*64),('candidate_sha256','b'*64),
                    ('candidate_origin','model'),
                    ('feedback_validation_attempts',2),('review',{'status':'locked'})]:
                bad=deepcopy(row);bad[key]=value
                with self.subTest(origin=origin,key=key), self.assertRaises(ValueError):
                    comparison.build_report(self.plan,{self.cell['cell_id']:bad})

    def test_multi_round_chain_applies_incremental_second_edit_to_previous_candidate(self):
        second = {'file':'app.py', 'old':'return 2', 'new':'return 3'}
        chain = [{'status':'candidate','base_content_id':source_id(self.files),'edits':self.edits},
                 {'status':'candidate','base_content_id':source_id(self.changed),'edits':[second]}]
        final = apply_edits(self.changed, [second], editable_paths=['app.py'])
        self.row.update(requests=2, candidate_sha256=source_id(final), final_output_sha256=comparison.result_digest(chain[-1]))
        packet = self.packet(edits=None, final_output=chain[-1], proposal_chain=chain)
        self.assertIn('return 3', packet['candidate_files']['app.py'])
        self.assertEqual(packet['candidate_files']['checks.py'].encode(), self.files['checks.py'])
        self.assertEqual(packet['proposal_chain_sha256'], comparison.result_digest(chain))
        self.assertEqual(packet['last_applied_source_id'], source_id(final))
        self.assertEqual(packet['packet_sha256'], comparison.result_digest({k:v for k,v in packet.items() if k!='packet_sha256'}))

    def test_multi_round_chain_rejects_reordering_wrong_base_test_changes_and_manual_tail(self):
        chain = [{'status':'candidate','base_content_id':source_id(self.files),'edits':self.edits},
                 {'status':'candidate','base_content_id':source_id(self.changed),
                  'edits':[{'file':'app.py','old':'return 2','new':'return 3'}]}]
        final = apply_edits(self.changed, chain[-1]['edits'], editable_paths=['app.py'])
        self.row.update(requests=2, candidate_sha256=source_id(final), final_output_sha256=comparison.result_digest(chain[-1]))
        bad_base = deepcopy(chain); bad_base[-1]['base_content_id'] = 'f'*64
        bad_tests = deepcopy(chain); bad_tests[-1]['edits'][0] = {'file':'checks.py','old':'assertTrue(True)','new':'assertTrue(False)'}
        for bad in (chain[::-1], bad_base, bad_tests):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.packet(edits=None, final_output=chain[-1], proposal_chain=bad)
        self.row['candidate_sha256'] = 'e'*64
        with self.assertRaises(ValueError):
            self.packet(edits=None, final_output=chain[-1], proposal_chain=chain)
        with self.assertRaises(ValueError):
            self.packet(final_output=chain[-1], proposal_chain=chain)

    def test_chain_ending_in_abstention_does_not_offer_prior_failed_patch_as_repair(self):
        end = {'status':'insufficient_evidence','base_content_id':source_id(self.changed),'edits':[]}
        chain = [{'status':'candidate','base_content_id':source_id(self.files),'edits':self.edits},end]
        self.row.update(requests=2, candidate_sha256=None, verdict='unknown', final_output_sha256=comparison.result_digest(end))
        packet = self.packet(edits=None, final_output=end, proposal_chain=chain)
        self.assertIsNone(packet['candidate_files'])
        self.assertEqual(packet['last_applied_source_id'], source_id(self.changed))
        self.assertEqual(comparison.build_report(self.plan,{self.cell['cell_id']:packet['result']})['arms']['direct_api']['scored'],0)

    def test_v2_finalize_preserves_candidate_only_with_bound_prior_chain(self):
        from macr.investigation.repair_loop import output_contract, digest
        self.spec['model']['output_contract_sha256']=digest(output_contract('v2'))
        self.plan=comparison.compile_comparison(self.spec);self.cell=self.plan['cells'][0]
        self.row['output_contract_sha256']=self.spec['model']['output_contract_sha256']
        end={'status':'finalize_current','base_content_id':source_id(self.changed),'edits':[]}
        chain=[{'status':'candidate','base_content_id':source_id(self.files),'edits':self.edits},end]
        self.row.update(requests=2,final_output_sha256=comparison.result_digest(end))
        packet=self.packet(edits=None,final_output=end,proposal_chain=chain)
        self.assertEqual(packet['candidate_files']['app.py'].encode(),self.changed['app.py'])
        self.assertEqual(packet['last_applied_source_id'],source_id(self.changed))
        self.assertFalse(packet['semantic_truth_validated'])
        for bad in ([end],chain+[end],chain[::-1],
                    [chain[0],dict(end,base_content_id=source_id(self.files))],
                    [chain[0],dict(end,edits=self.edits)]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                self.packet(edits=None,final_output=end,proposal_chain=bad)
        with self.assertRaises(ValueError): self.packet(edits=self.edits,final_output=end)
        old=deepcopy(self.spec);old['model']['output_contract_sha256']='1'*64
        self.plan=comparison.compile_comparison(old)
        self.row['output_contract_sha256']='1'*64
        with self.assertRaises(ValueError):
            self.packet(edits=None,final_output=end,proposal_chain=chain)

    def test_chain_cannot_hide_extra_model_proposals_under_one_counted_request(self):
        chain = [{'status':'candidate','base_content_id':source_id(self.files),'edits':self.edits},
                 {'status':'candidate','base_content_id':source_id(self.changed),
                  'edits':[{'file':'app.py','old':'return 2','new':'return 3'}]}]
        final = apply_edits(self.changed, chain[-1]['edits'], editable_paths=['app.py'])
        self.row.update(candidate_sha256=source_id(final), final_output_sha256=comparison.result_digest(chain[-1]))
        with self.assertRaises(ValueError):
            self.packet(edits=None, final_output=chain[-1], proposal_chain=chain)

    def test_no_candidate_packet_has_no_candidate_and_no_success_inference(self):
        self.output = {'status': 'insufficient_evidence', 'edits': []}
        self.row.update(candidate_sha256=None, verdict='unknown',
                        final_output_sha256=comparison.result_digest(self.output))
        packet = self.packet(edits=None, final_output=self.output)
        self.assertIsNone(packet['candidate_files'])
        report = comparison.build_report(self.plan, {self.cell['cell_id']: packet['result']})
        self.assertEqual(report['arms']['direct_api']['outcomes']['necessary_abstention'], 0)
        self.assertEqual(report['arms']['direct_api']['unscored'], 1)
