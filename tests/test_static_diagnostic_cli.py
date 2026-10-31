"""Real source capture/index and literal provider envelopes; no model or target execution."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest

from debugger_fixtures import PROJECT, fixture_root

try:
    import run_static_diagnostic as command
except ModuleNotFoundError:
    command = None


class StaticDiagnosticCLITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(command, 'explicit static diagnostic offline entry is missing')
        # Public read-only input stays outside protected artifacts ancestry.
        self.repo = PROJECT / 'examples/static-diagnostic-development/keyword-defect'

    def call(self, action, out, extra=()):
        args = [action, '--repo', str(self.repo), '--entry', 'entry.py:start',
                '--question', 'Does start satisfy the requirement?',
                '--scenario', 'start must return True; integer literals and unchanged module bindings.',
                '--out', str(out), *extra]
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
            code = command.main(args)
        return code, json.loads(stdout.getvalue())

    def prepare(self):
        out = fixture_root() / 'prepared'
        code, summary = self.call('prepare', out)
        self.assertEqual(code, 0, summary)
        return json.loads((out / 'prepared.json').read_text()), out

    def replies(self, prepared, *, wrong_scope=False):
        scope = prepared['scope']
        candidate = {'verdict': 'supported_defect',
                     'cause': 'Keyword arguments bind left to 9 and right to 1; the comparison is false.',
                     'when': 'The fixed start() call requires True but compares 9 < 1.',
                     'refs': [r['id'] for r in scope['source'][:2]], 'unknowns': [],
                     'scope': '0' * 16 if wrong_scope else scope['scope_sha256'][:16]}
        envelope = {'id': 'offline-development', 'model': 'deepseek-flash',
                    'system_fingerprint': 'offline-development-only',
                    'choices': [{'index': 0, 'finish_reason': 'stop',
                                 'message': {'role': 'assistant', 'content': json.dumps(candidate),
                                             'reasoning_content': 'A synthetic contract fixture.'}}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140,
                              'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 100,
                              'completion_tokens_details': {'reasoning_tokens': 10}}}
        file = fixture_root() / 'replies.json'
        file.write_text(json.dumps({'direct': envelope, 'evidence': envelope}))
        return file

    def test_prepare_runs_real_capture_and_bindings_with_equal_complete_source(self):
        prepared, out = self.prepare()
        self.assertTrue(prepared['packet']['bindings'])
        self.assertTrue((out / 'prepared.json').is_file())
        requests = prepared['requests']
        direct = json.loads(requests['direct']['messages'][-1]['content'])
        evidence = json.loads(requests['evidence']['messages'][-1]['content'])
        self.assertEqual(direct, evidence)
        self.assertEqual({r['file'] for r in direct['source']}, {'entry.py', 'predicate.py'})
        self.assertEqual(requests['direct']['response_schema'], requests['evidence']['response_schema'])
        self.assertGreaterEqual(prepared['pipeline_timing_seconds']['capture_common'], 0)

    def test_offline_actual_entry_uses_converse_once_per_arm_not_real_api(self):
        prepared, _ = self.prepare()
        replies = self.replies(prepared)
        out = fixture_root() / 'run'
        code, summary = self.call('offline', out, ['--replies', str(replies)])
        self.assertEqual(code, 0, summary)
        result = json.loads((out / 'result.json').read_text())
        self.assertEqual(result['model_calls_live'], 0)
        self.assertEqual(result['fixture_model_calls'], 2)
        self.assertEqual(len(result['rows']), 2)
        self.assertTrue(all(row['status'] == 'semantic_pending' for row in result['rows']))
        self.assertTrue(all(row['response']['system_fingerprint'] == 'offline-development-only'
                            for row in result['rows']))

    def test_wrong_scope_remains_two_failed_results_and_nonzero_exit(self):
        prepared, _ = self.prepare()
        out = fixture_root() / 'failed'
        code, _ = self.call('offline', out, ['--replies', str(self.replies(prepared, wrong_scope=True))])
        self.assertEqual(code, 1)
        result = json.loads((out / 'result.json').read_text())
        self.assertEqual(len(result['rows']), 2)
        self.assertTrue(all(row['status'] == 'failed' for row in result['rows']))
        self.assertEqual(result['model_calls_live'], 0)

    def test_existing_and_inside_repo_outputs_fail_without_overwrite(self):
        out = fixture_root()
        marker = out / 'keep.txt'; marker.write_text('keep')
        code, _ = self.call('prepare', out)
        self.assertEqual(code, 2)
        self.assertEqual(marker.read_text(), 'keep')
        nested = self.repo / 'new-output'
        code, _ = self.call('prepare', nested)
        self.assertEqual(code, 2)
        self.assertFalse(nested.exists())

    def test_malformed_reply_map_fails_before_output_and_never_reads_key(self):
        file = fixture_root() / 'invalid.json'; file.write_text('{"other":"private-sentinel"}')
        out = fixture_root() / 'never-created'
        code, summary = self.call('offline', out, ['--replies', str(file)])
        self.assertEqual(code, 2)
        self.assertNotIn('private-sentinel', json.dumps(summary))
        self.assertFalse(out.exists())

    def test_protected_source_ancestry_remains_rejected(self):
        self.repo = fixture_root()
        out = fixture_root() / 'protected-source-output'
        code, summary = self.call('prepare', out)
        self.assertEqual(code, 2)
        self.assertEqual(summary['error_code'], 'forbidden_path')
        self.assertFalse(out.exists())

    def test_help_is_inert(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as end:
            command.main(['--help'])
        self.assertEqual(end.exception.code, 0)
