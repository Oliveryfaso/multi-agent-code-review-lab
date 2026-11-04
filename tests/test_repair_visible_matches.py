"""Authored visible-span challenges; fixture source is never imported or run."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_repair_entry import InterceptedTransport
from macr.evals import repair_comparison as comparison
from macr.investigation import repair_edits, repair_entry as entry, repair_loop as loop
from macr.investigation import repair_windows as windows
from macr.investigation.validation import ContractError
from macr.providers.base import ModelResponse
from macr.providers.repair_binding import RepairProviderBinding, development_config


def selection(*ranges, version='v2'):
    return {'schema': 'macr.repair.source-windows.' + version, 'files': {
        'app.py': [{'start_line': start, 'end_line': end} for start, end in ranges]}}


class RepairVisibleMatchesTests(unittest.TestCase):
    def setUp(self):
        self.prefix = '# café λ: authored bytes\nvalue = 1\n# hidden separator\n'.encode()
        self.suffix = b'raise AssertionError("AUTHORED_SOURCE_MUST_NEVER_RUN")\n'
        self.files = {'app.py': self.prefix + b'value = 1\n' + self.suffix,
            'checks.py': (b'import unittest\n\nclass Checks(unittest.TestCase):\n'
                b'    def test_public(self):\n        self.assertEqual(1, 2)\n'),
            'helper.py': b'OMITTED_HELPER_SENTINEL = "unchanged"\n'}
        self.chosen = selection((4, 4))
        self.chosen['files']['checks.py'] = [{'start_line': 1, 'end_line': 5}]
        self.edit = {'file': 'app.py', 'old': 'value = 1\n', 'new': 'value = 2\n'}
        self.issue = 'Authored fixture: change only the displayed second assignment.'
        self.fixed = dict(self.files)
        self.fixed['app.py'] = self.prefix + b'value = 2\n' + self.suffix

    def apply(self, *, files=None, edits=None, chosen=None):
        return repair_edits.apply_edits(self.files if files is None else files,
            [self.edit] if edits is None else edits, editable_paths=['app.py'],
            full_originals=True, source_windows=self.chosen if chosen is None else chosen)

    def request(self):
        return loop._request(self.files, self.issue, ['app.py'], ['checks.py'], {}, 1,
            strategy='direct_api', bound=True, proposal_contract='v6',
            source_scope='declared_files', source_windows=self.chosen)

    def reply(self, request):
        payload = json.loads(request.messages[1]['content'])
        return ModelResponse(content=json.dumps({
            **{key: payload[key] for key in ('base_content_id', 'hypothesis_id', 'evidence_ids')},
            'status': 'candidate', 'summary': 'Authored visible edit; no execution.',
            'selected_content_id': None, 'edits': [self.edit]}, ensure_ascii=False))

    def record(self, arm):
        root = fixture_root()
        repo = root / 'repo'; repo.mkdir()
        for path, body in self.files.items():
            (repo / path).write_bytes(body)
        transport = InterceptedTransport([
            {'status': 'candidate', 'summary': 'Authored local edit; not a model repair.',
             'edits': [self.edit]},
            {'status': 'finalize_current', 'summary': 'Keep bytes without claiming verification.',
             'edits': []}])
        config = development_config()
        provider = RepairProviderBinding(config, transport=transport)
        names = loop.test_manifest(self.files, ['checks.py'], full_originals=True)
        verifier = loop.ReplayVerifier([{'source_id': loop.source_id(files, full_originals=True),
            'execution_status': 'completed', 'outcomes': {name: 'failed' for name in names}}
            for files in (self.files, self.fixed)])
        out = root / 'record'
        report = loop.run_repair_loop(repo, self.issue, editable_paths=['app.py'],
            test_paths=['checks.py'], provider=provider, out=out, strategy=arm,
            task_id='visible-span-development', verifier=verifier,
            expected_source_files={path: sha256(body).hexdigest() for path, body in self.files.items()},
            proposal_contract='v6', source_scope='declared_files', source_windows=self.chosen)
        return report, out, repo, transport, verifier, config

    def test_complete_duplicated_visible_line_changes_only_second_occurrence(self):
        before = deepcopy(self.files)
        legacy = deepcopy(self.chosen); legacy['schema'] = 'macr.repair.source-windows.v1'
        with self.assertRaises(ContractError):
            self.apply(chosen=legacy)
        result = self.apply()
        self.assertEqual(result, self.fixed)
        self.assertEqual(result['app.py'][:len(self.prefix)], self.prefix)
        self.assertEqual(result['app.py'][-len(self.suffix):], self.suffix)
        self.assertEqual(result['checks.py'], before['checks.py'])
        self.assertEqual(result['helper.py'], before['helper.py'])
        self.assertEqual(self.files, before)
        self.assertIsNot(result, self.files)

    def test_utf8_prefix_and_exact_lf_crlf_cr_and_unterminated_eof_offsets(self):
        for terminator in ('\n', '\r\n', '\r'):
            for final_newline in (True, False):
                with self.subTest(terminator=repr(terminator), final_newline=final_newline):
                    first = ('# café λ' + terminator + 'value = 1' + terminator
                             + '# omitted' + terminator).encode()
                    ending = terminator if final_newline else ''
                    body = first + ('value = 1' + ending).encode()
                    edit = {'file': 'app.py', 'old': 'value = 1' + ending,
                            'new': 'value = 2' + ending}
                    result = self.apply(files={'app.py': body}, edits=[edit],
                                        chosen=selection((4, 4)))
                    self.assertEqual(result, {'app.py': first + ('value = 2' + ending).encode()})

    def test_two_visible_or_overlapping_occurrences_are_rejected(self):
        cases = [({'app.py': b'value = 1\n# gap\nvalue = 1\n'},
                  selection((1, 1), (3, 3)), self.edit),
                 ({'app.py': b'value = 1; value = 1\n'}, selection((1, 1)),
                  {'file': 'app.py', 'old': 'value = 1', 'new': 'value = 2'}),
                 ({'app.py': b'# aaa\n'}, selection((1, 1)),
                  {'file': 'app.py', 'old': 'aa', 'new': 'bb'})]
        for files, chosen, edit in cases:
            with self.subTest(files=files):
                windows.project_source_windows(files, chosen)
                before = deepcopy(files)
                with self.assertRaises(ContractError):
                    self.apply(files=files, edits=[edit], chosen=chosen)
                self.assertEqual(files, before)

    def test_unseen_cross_boundary_and_concatenated_windows_are_not_editable(self):
        files = {'app.py': b'first = 1\nhidden = 9\nlast = 3\n'}
        chosen = selection((1, 1), (3, 3))
        windows.project_source_windows(files, chosen)
        for old in ('hidden = 9', 'first = 1\nhidden = 9', 'first = 1\nlast = 3\n'):
            with self.subTest(old=old), self.assertRaises(ContractError):
                self.apply(files=files, chosen=chosen,
                    edits=[{'file': 'app.py', 'old': old, 'new': 'replacement = 4'}])
        self.assertEqual(files['app.py'], b'first = 1\nhidden = 9\nlast = 3\n')

    def test_protected_paths_and_duplicate_file_edits_keep_existing_authority(self):
        windows.project_source_windows(self.files, self.chosen, protected_paths=['checks.py'])
        before = deepcopy(self.files)
        for edit in ({'file': 'checks.py', 'old': 'self.assertEqual(1, 2)',
                      'new': 'self.assertEqual(1, 1)'},
                     {'file': 'helper.py', 'old': '"unchanged"', 'new': '"changed"'},
                     {'file': 'new.py', 'old': 'value = 1', 'new': 'value = 2'}):
            with self.subTest(path=edit['file']), self.assertRaises(ContractError):
                self.apply(edits=[edit])
        with self.assertRaises(ContractError):
            self.apply(edits=[self.edit, self.edit])
        self.assertEqual(self.files, before)

    def test_opt_in_schema_policy_full_hashes_and_context_are_bound(self):
        legacy = deepcopy(self.chosen); legacy['schema'] = 'macr.repair.source-windows.v1'
        view = windows.project_source_windows(self.files, self.chosen)
        old_view = windows.project_source_windows(self.files, legacy)
        self.assertEqual(set(view), {'schema', 'edit_policy', 'files'})
        self.assertEqual(view['schema'], 'macr.repair.source-window-projection.v2')
        self.assertEqual(view['edit_policy'], 'unique_shown_span')
        self.assertEqual(old_view['schema'], 'macr.repair.source-window-projection.v1')
        self.assertNotIn('edit_policy', old_view)
        self.assertEqual(view['files'], old_view['files'])
        row = view['files']['app.py']
        self.assertEqual(row['sha256'], sha256(self.files['app.py']).hexdigest())
        self.assertEqual(row['windows'], [{'start_line': 4, 'end_line': 4, 'line_numbers': [4],
            'text': 'value = 1\n', 'sha256': sha256(b'value = 1\n').hexdigest()}])
        self.assertNotIn('AUTHORED_SOURCE_MUST_NEVER_RUN', json.dumps(view))
        current_id = entry.initial_context_id(self.files, self.issue,
            source_scope='declared_files', source_windows=self.chosen)
        self.assertNotEqual(current_id, entry.initial_context_id(self.files, self.issue,
            source_scope='declared_files', source_windows=legacy))
        hidden_changed = dict(self.files)
        hidden_changed['helper.py'] = b'OMITTED_HELPER_SENTINEL = "different"\n'
        self.assertNotEqual(current_id, entry.initial_context_id(hidden_changed, self.issue,
            source_scope='declared_files', source_windows=self.chosen))
        task = {'schema': 'macr.repair.task.v4', 'task_id': 'visible-span-development',
            'issue': self.issue, 'source_files': {p: sha256(b).hexdigest() for p, b in self.files.items()},
            'editable_paths': ['app.py'], 'test_paths': ['checks.py'], 'test_plan': None,
            'source_scope': 'declared_files', 'source_windows': self.chosen}
        self.assertEqual(entry._task_shape(task), task)
        for invalid in (self.chosen | {'edit_policy': 'unique_shown_span'},
                        self.chosen | {'schema': 'macr.repair.source-windows.v99'}):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                windows.project_source_windows(self.files, invalid)

    def test_proposal_accepts_current_span_and_rejects_tampered_projection_or_base(self):
        request = self.request(); response = self.reply(request)
        proposal, changed = loop._proposal(response, request, self.files, ['app.py'])
        self.assertEqual(proposal['edits'], [self.edit])
        self.assertEqual(changed, self.fixed)
        payload = json.loads(request.messages[1]['content'])
        mutations = []
        for key, value in (('edit_policy', 'complete_file_unique'),
                           ('schema', 'macr.repair.source-window-projection.v1')):
            altered = deepcopy(payload); altered['source_windows'][key] = value
            mutations.append(altered)
        altered = deepcopy(payload); del altered['source_windows']['edit_policy']
        mutations.append(altered)
        altered = deepcopy(payload); altered['source_windows']['files']['app.py']['sha256'] = '0' * 64
        mutations.append(altered)
        altered = deepcopy(payload)
        altered['source_windows']['files']['app.py']['windows'][0]['text'] = 'value = 7\n'
        mutations.append(altered)
        for altered in mutations:
            bad_request = deepcopy(request)
            bad_request.messages[1]['content'] = json.dumps(altered, ensure_ascii=False)
            with self.subTest(projection=altered['source_windows']), self.assertRaises(ContractError):
                loop._proposal(response, bad_request, self.files, ['app.py'])
        stale = json.loads(response.content); stale['base_content_id'] = '0' * 64
        with self.assertRaises(ContractError):
            loop._proposal(ModelResponse(content=json.dumps(stale)), request, self.files, ['app.py'])
        self.assertEqual(self.files['app.py'], self.prefix + b'value = 1\n' + self.suffix)

    def test_normal_three_arms_share_projection_and_limits_and_preserve_inputs(self):
        views = []; limits = []
        for arm in ('direct_api', 'api_test_feedback', 'api_debugger'):
            with self.subTest(arm=arm):
                report, out, repo, transport, verifier, config = self.record(arm)
                self.assertEqual(report['status'], 'candidate_finalized')
                self.assertEqual(report['provider_requests'], 2)
                self.assertEqual(verifier.calls, 0 if arm == 'direct_api' else 2)
                self.assertEqual(loop.verify_loop(out), report)
                self.assertFalse(report['target_code_executed'])
                self.assertFalse(report['execution_authorized']); self.assertFalse(report['repaired'])
                for path, body in self.files.items():
                    self.assertEqual((repo / path).read_bytes(), body)
                    self.assertEqual((out / 'sources/0' / path).read_bytes(), body)
                    self.assertEqual((out / 'sources/1' / path).read_bytes(), self.fixed[path])
                first, second = [json.loads(wire['messages'][1]['content']) for wire in transport.requests]
                views.append((first['files'], first['source_windows'], first['issue'], first['base_content_id']))
                self.assertEqual(first['files'], {})
                self.assertEqual(second['source_windows']['files']['app.py']['windows'][0]['text'], 'value = 2\n')
                self.assertNotEqual(first['base_content_id'], second['base_content_id'])
                intent = json.loads((out / 'rounds/1/intent.json').read_text())['request']
                limits.append((intent['max_input_tokens'], intent['max_output_tokens'], config['limits']))
                self.assertNotIn('OMITTED_HELPER_SENTINEL', json.dumps(transport.requests))
                self.assertNotIn('AUTHORED_SOURCE_MUST_NEVER_RUN', json.dumps(transport.requests))
        self.assertTrue(all(view == views[0] for view in views))
        self.assertTrue(all(limit == limits[0] for limit in limits))

    def test_scoring_replays_visible_offset_and_rejects_legacy_policy_graft(self):
        report, out, _, _, _, config = self.record('direct_api')
        spec = json.loads((Path(__file__).resolve().parents[1] /
            'examples/repair_comparison_three_arm_development.json').read_text())
        spec['tasks'] = [{'task_id': 'visible-span-development',
            'snapshot_sha256': loop.source_id(self.files, full_originals=True),
            'issue_sha256': loop.digest(self.issue),
            'visible_tests_sha256': loop.source_id({'checks.py': self.files['checks.py']}, full_originals=True),
            'initial_context_sha256': entry.initial_context_id(self.files, self.issue,
                source_scope='declared_files', source_windows=self.chosen)}]
        spec['model']['configuration_sha256'] = loop.digest(config)
        spec['model']['output_contract_sha256'] = loop.digest(loop.output_contract('v6'))
        limits = config['limits']
        spec['limits'] = {'max_requests': limits['max_requests'],
            'input_tokens': limits['max_total_input_tokens'],
            'output_tokens': limits['max_total_output_tokens'], 'max_usd': limits['max_usd']}
        plan = comparison.compile_comparison(spec)
        cell = next(cell for cell in plan['cells'] if cell['arm'] == 'direct_api')
        packet = entry.score_handoff(out, plan, cell['cell_id'])
        self.assertEqual(packet['candidate_files'], {path: body.decode() for path, body in self.fixed.items()})
        self.assertEqual(packet['source_windows'], self.chosen)
        self.assertEqual(packet['result']['candidate_sha256'], report['final_source_id'])
        self.assertIsNone(packet['result']['review'])
        self.assertFalse(packet['semantic_truth_validated'])
        self.assertFalse(packet['provenance_authenticated'])
        legacy = deepcopy(self.chosen); legacy['schema'] = 'macr.repair.source-windows.v1'
        with self.assertRaises(ValueError):
            comparison.score_packet(plan, cell['cell_id'], packet['result'], original_files=self.files,
                editable_paths=['app.py'], protected_paths=['checks.py'],
                final_output=packet['final_output'], verification_receipt=packet['verification_receipt'],
                proposal_chain=packet['proposal_chain'], source_scope='declared_files', source_windows=legacy)


if __name__ == '__main__':
    unittest.main()
