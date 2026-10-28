"""Static tools never evaluate the authorized programs or inspect answer material."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_tools import (
    MAX_FILE_BYTES, MAX_FILES, MAX_OUTPUT_BYTES, MAX_RESULTS, MAX_TERM_BYTES,
    StaticEvidenceError, StaticEvidenceTools,
)


DATASET = Path(__file__).resolve().parents[1] / 'evals' / 'deepseek-policy-dev-v1'


class StaticEvidenceToolsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.case = copy.deepcopy(json.loads((DATASET / 'cases.json').read_text())['cases'][1])
        self.snapshot = self.root / self.case['snapshot_root']
        self.snapshot.mkdir(parents=True)
        self.entry = ('raise AssertionError("target must never execute")\r\n'
                      '\r\n'
                      'def describe(values):\r\n'
                      '    return stats(values)\r\n'
                      '\r\n'
                      'def wrapper(obj):\r\n'
                      '    return obj.stats(\r\n'
                      '        [1, 2])\r\n')
        (self.snapshot / 'entry.py').write_bytes(self.entry.encode())
        (self.snapshot / 'metrics.py').write_text(
            'def stats(values):\n    return len(values), sum(values)\n')
        (self.root / 'gold.json').write_text('{"secret":"ANSWER_ONLY_SENTINEL"}')
        self.manifest = prepare_policy_manifest(self.case, dataset_root=self.root,
            out=self.root / 'frozen', model={'model_id': 'deepseek-chat'})
        self.tools = StaticEvidenceTools(self.manifest, self.root)

    def test_source_preserves_physical_lines_and_only_requested_authorized_files(self):
        source = self.tools.source(['entry.py'])
        self.assertEqual(source, [{'file': 'entry.py', 'start': 1, 'end': 8,
                                  'body': self.entry}])
        self.assertNotIn('ANSWER_ONLY_SENTINEL', json.dumps(source))
        self.assertNotIn('metrics.py', json.dumps(source))

    def test_search_is_literal_bounded_and_citations_use_physical_lines(self):
        result = self.tools.execute('search', {'query': '[1, 2]'})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['snippets']), 1)
        row = result['snippets'][0]
        self.assertEqual((row['file'], row['start'], row['end']), ('entry.py', 6, 8))
        self.assertIn('        [1, 2])\r\n', row['body'])
        self.assertEqual(self.tools.execute('search', {'query': '.*'})['snippets'], [])
        missing = self.tools.execute('read_caller', {'symbol': 'no_matching_symbol'})
        self.assertEqual((missing['status'], missing['snippets'], missing['relations']),
                         ('ok', [], []))
        self.assertEqual(missing['resolution'], 'unresolved_static_relation')

    def test_read_symbol_preserves_original_definition_lines_without_execution(self):
        result = self.tools.execute('read_symbol', {'file': 'entry.py', 'symbol': 'describe'})
        self.assertEqual(result['snippets'], [{'file': 'entry.py', 'start': 3, 'end': 4,
            'body': 'def describe(values):\r\n    return stats(values)\r\n'}])
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['truncated'])

    def test_read_caller_is_syntax_only_and_every_relation_is_unresolved(self):
        result = self.tools.execute('read_caller', {'symbol': 'stats'})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['resolution'], 'unresolved_static_relation')
        self.assertEqual([(row['caller'], row['callee']) for row in result['relations']],
                         [('describe', 'stats'), ('wrapper', 'obj.stats')])
        self.assertTrue(all(row['resolution'] == 'unresolved_static_relation'
                            for row in result['relations']))
        self.assertEqual([(s['start'], s['end']) for s in result['snippets']], [(4, 4), (7, 8)])
        qualified = self.tools.execute('read_caller', {'symbol': 'obj.stats'})
        self.assertEqual(len(qualified['relations']), 1)
        self.assertEqual(qualified['relations'][0]['caller'], 'wrapper')

    def test_invalid_arguments_and_nonread_tools_return_no_source(self):
        requests = [('search', {'query': 'x' * (MAX_TERM_BYTES + 1)}),
                    ('search', {'query': '\n'}), ('search', {'query': ''}),
                    ('search', {'query': 1}), ('search', {'query': 'stats', 'file': 'gold.json'}),
                    ('read_caller', {'symbol': 'stats()'}),
                    ('read_symbol', {'file': 'entry.py', 'symbol': 'x' * (MAX_TERM_BYTES + 1)}),
                    ('execute', {'code': 'print(1)'}), ('shell', {'command': 'cat gold.json'}),
                    ('finish', {}), ('search', []), (None, {})]
        for name, arguments in requests:
            with self.subTest(name=name, arguments=arguments):
                result = self.tools.execute(name, arguments)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['snippets'], [])
                self.assertNotIn('ANSWER_ONLY_SENTINEL', json.dumps(result))

    def test_traversal_gold_unlisted_and_symlink_are_never_evidence(self):
        for relative in ('../gold.json', 'gold.json', '/etc/passwd', 'missing.py',
                         'nested/../entry.py', 'entry.py/../../gold.json'):
            with self.subTest(relative=relative):
                with self.assertRaises(StaticEvidenceError):
                    self.tools.source([relative])
                result = self.tools.execute('read_symbol', {'file': relative, 'symbol': 'stats'})
                self.assertEqual((result['status'], result['snippets']), ('error', []))
        (self.snapshot / 'entry.py').unlink()
        (self.snapshot / 'entry.py').symlink_to(self.root / 'gold.json')
        for name, arguments in [('search', {'query': 'ANSWER_ONLY_SENTINEL'}),
                                ('read_caller', {'symbol': 'stats'}),
                                ('read_symbol', {'file': 'entry.py', 'symbol': 'stats'})]:
            result = self.tools.execute(name, arguments)
            self.assertEqual((result['status'], result['snippets']), ('error', []))
            self.assertNotIn('ANSWER_ONLY_SENTINEL', json.dumps(result))

    def test_content_hash_is_rechecked_after_initial_source_read(self):
        self.tools.source(['entry.py'])
        (self.snapshot / 'metrics.py').write_text('ANSWER_ONLY_SENTINEL\n')
        result = self.tools.execute('search', {'query': 'stats'})
        self.assertEqual((result['status'], result['snippets']), ('error', []))
        self.assertNotIn('ANSWER_ONLY_SENTINEL', json.dumps(result))

    def test_result_count_and_encoded_output_are_capped(self):
        with patch('macr.evals.deepseek_policy_tools.read_snapshot_file',
                   return_value='needle\n' * 200):
            result = self.tools.execute('search', {'query': 'needle'})
        self.assertEqual(len(result['snippets']), MAX_RESULTS)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), MAX_OUTPUT_BYTES)

    def test_decorated_nested_symbol_and_form_feed_keep_physical_ranges(self):
        source = ('# one\fphysical line\nclass Box:\n    @factory()\n'
                  '    def stats(self):\n        return 1\n')
        with patch('macr.evals.deepseek_policy_tools.read_snapshot_file', return_value=source):
            result = self.tools.execute('read_symbol', {'file': 'entry.py', 'symbol': 'Box.stats'})
            rows = self.tools.source(['entry.py'])
        self.assertEqual((result['snippets'][0]['start'], result['snippets'][0]['end']), (3, 5))
        self.assertEqual(result['snippets'][0]['body'],
                         '    @factory()\n    def stats(self):\n        return 1\n')
        self.assertEqual(rows[0]['end'], 5)

    def test_file_count_file_bytes_and_json_expansion_are_bounded(self):
        invalid = copy.deepcopy(self.manifest)
        invalid['snapshot']['files'] = {f'f{i}.py': {} for i in range(MAX_FILES + 1)}
        with self.assertRaises(StaticEvidenceError):
            StaticEvidenceTools(invalid, self.root)
        with patch('macr.evals.deepseek_policy_tools.read_snapshot_file',
                   return_value='x' * (MAX_FILE_BYTES + 1)):
            with self.assertRaises(StaticEvidenceError):
                self.tools.source(['entry.py'])
            self.assertEqual(self.tools.execute('search', {'query': 'x'})['snippets'], [])
        with patch('macr.evals.deepseek_policy_tools.read_snapshot_file',
                   return_value='needle' + '\x00' * 20000 + '\n'):
            result = self.tools.execute('search', {'query': 'needle'})
        self.assertEqual(result['snippets'], [])
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result).encode()), MAX_OUTPUT_BYTES)

    def test_ast_reads_reject_nonpython_and_invalid_python(self):
        with patch('macr.evals.deepseek_policy_tools.read_snapshot_file',
                   return_value='def broken(:\n'):
            result = self.tools.execute('read_symbol', {'file': 'entry.py', 'symbol': 'broken'})
        self.assertEqual((result['status'], result['snippets']), ('error', []))
        self.tools.files = ('notes.txt',)
        with patch('macr.evals.deepseek_policy_tools.read_snapshot_file', return_value='def f(): pass'):
            result = self.tools.execute('read_symbol', {'file': 'notes.txt', 'symbol': 'f'})
        self.assertEqual(result['error_code'], 'python_file_required')
        self.assertEqual(result['snippets'], [])


if __name__ == '__main__':
    unittest.main()
