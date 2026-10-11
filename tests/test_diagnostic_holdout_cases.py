"""Fixed synthetic fixture checks, never an arbitrary-code execution interface."""
import ast
from collections import Counter
import json
from pathlib import Path
import unittest

from macr.evals.diagnostic_compare import _parse
from macr.investigation.validation import validate_identifier


ROOT = Path(__file__).resolve().parents[1] / 'evals' / 'diagnostic-holdout-v1'
PROBE_SOURCES = {
    'c01': 'def contains(value, lower, upper):\n    return lower <= value < upper\n\n\ndef demo():\n    return contains(8, 2, 8)\n',
    'c02': 'def last_or_none(items):\n    if not items:\n        return None\n    return items[-1]\n\n\ndef demo():\n    return [last_or_none([]), last_or_none([4])]\n',
    'c04': 'def snapshot(cart):\n    return {"items": cart["items"]}\n\n\ndef demo():\n    cart = {"items": ["pen"]}\n    saved = snapshot(cart)\n    cart["items"].append("book")\n    return saved["items"]\n',
    'c05': 'def duplicate_row(row):\n    first = dict(row)\n    second = dict(row)\n    return [first, second]\n\n\ndef demo():\n    original = {"count": 4}\n    copies = duplicate_row(original)\n    copies[0]["count"] = 9\n    return [copies[1]["count"], original["count"]]\n',
    'c07': 'def collect(label, labels=[]):\n    labels.append(label)\n    return list(labels)\n\n\ndef demo():\n    first = collect("red")\n    second = collect("blue")\n    return [first, second]\n',
    'c08': 'def attach(item, items=None):\n    result = [] if items is None else list(items)\n    result.append(item)\n    return result\n\n\ndef demo():\n    original = ["x"]\n    first = attach("a")\n    second = attach("b")\n    supplied = attach("c", original)\n    return [first, second, supplied, original]\n',
    'c10': 'def discount(total, percent=10):\n    return total - percent\n\n\ndef demo():\n    return discount(80, percent=25)\n',
    'c11': 'def join_pair(left, right, *, separator=":"):\n    return left + separator + right\n\n\ndef demo():\n    return join_pair("left", "right", separator="")\n',
}
PROBE_RETURNS = {
    'c01': False, 'c02': [None, 4], 'c04': ['pen', 'book'], 'c05': [4, 4],
    'c07': [['red'], ['red', 'blue']], 'c08': [['a'], ['b'], ['x', 'c'], ['x']],
    'c10': 55, 'c11': 'leftright',
}
EVIDENCE = {
    'c01': [(1, 2, 'comparison', 'def contains(value, lower, upper):\n    return lower <= value < upper'),
            (5, 6, 'trigger', 'def demo():\n    return contains(8, 2, 8)')],
    'c02': [(1, 4, 'guard-and-access', 'def last_or_none(items):\n    if not items:\n        return None\n    return items[-1]'),
            (7, 8, 'scope-witness', 'def demo():\n    return [last_or_none([]), last_or_none([4])]')],
    'c03': [(1, 2, 'dependency-call', 'def encode_record(record, codec):\n    return codec.encode(record)')],
    'c04': [(1, 2, 'shared-list', 'def snapshot(cart):\n    return {"items": cart["items"]}'),
            (6, 9, 'later-mutation', '    cart = {"items": ["pen"]}\n    saved = snapshot(cart)\n    cart["items"].append("book")\n    return saved["items"]')],
    'c05': [(1, 4, 'separate-copies', 'def duplicate_row(row):\n    first = dict(row)\n    second = dict(row)\n    return [first, second]'),
            (8, 11, 'entry-assignment', '    original = {"count": 4}\n    copies = duplicate_row(original)\n    copies[0]["count"] = 9\n    return [copies[1]["count"], original["count"]]')],
    'c06': [(1, 2, 'callback-selection', 'def selected(values, predicate):\n    return list(filter(predicate, values))')],
    'c07': [(1, 3, 'default-mutation', 'def collect(label, labels=[]):\n    labels.append(label)\n    return list(labels)'),
            (7, 9, 'consecutive-calls', '    first = collect("red")\n    second = collect("blue")\n    return [first, second]')],
    'c08': [(1, 4, 'fresh-result', 'def attach(item, items=None):\n    result = [] if items is None else list(items)\n    result.append(item)\n    return result'),
            (8, 12, 'call-boundaries', '    original = ["x"]\n    first = attach("a")\n    second = attach("b")\n    supplied = attach("c", original)\n    return [first, second, supplied, original]')],
    'c09': [(1, 2, 'unit-conversion', 'def ttl_seconds(config):\n    return config["ttl"] * 60')],
    'c10': [(1, 2, 'amount-calculation', 'def discount(total, percent=10):\n    return total - percent'),
            (5, 6, 'nondefault-call', 'def demo():\n    return discount(80, percent=25)')],
    'c11': [(1, 2, 'parameter-use', 'def join_pair(left, right, *, separator=":"):\n    return left + separator + right'),
            (5, 6, 'explicit-empty', 'def demo():\n    return join_pair("left", "right", separator="")')],
    'c12': [(1, 2, 'host-index', 'def active_region(regions):\n    return regions[ACTIVE_REGION]')],
}


class DiagnosticHoldoutCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads((ROOT / 'cases.json').read_text(encoding='utf-8'))
        cls.reference = json.loads((ROOT / 'reference.json').read_text(encoding='utf-8'))
        cls.development = json.loads((ROOT / 'development' / 'cases.json').read_text(encoding='utf-8'))
        cls.dev_reference = json.loads((ROOT / 'development' / 'reference.json').read_text(encoding='utf-8'))

    def test_retained_balance_neutral_ids_and_separate_development(self):
        self.assertEqual(set(self.dataset), {'version', 'prompt_version', 'cases'})
        self.assertEqual(self.dataset['version'], 'diagnostic-holdout-v1')
        self.assertEqual(self.dataset['prompt_version'], 'diagnostic-holdout-v1')
        ids = [case['id'] for case in self.dataset['cases']]
        self.assertEqual(ids, [f'c{i:02}' for i in range(1, 13)])
        self.assertEqual(set(self.reference), {'version', 'cases'})
        self.assertEqual(self.reference['version'], self.dataset['version'])
        self.assertEqual(set(self.reference['cases']), set(ids))
        self.assertEqual(Counter(ref['category'] for ref in self.reference['cases'].values()),
                         {'supported_defect': 4, 'no_defect_supported': 4, 'unknown': 4})
        self.assertEqual(self.development['version'], 'diagnostic-development-v1')
        self.assertEqual(self.dev_reference['version'], self.development['version'])
        self.assertEqual(self.development['prompt_version'], self.dataset['prompt_version'])
        dev_ids = [case['id'] for case in self.development['cases']]
        self.assertEqual(dev_ids, ['d01', 'd02', 'd03'])
        self.assertEqual(set(self.dev_reference['cases']), set(dev_ids))
        self.assertEqual(Counter(ref['category'] for ref in self.dev_reference['cases'].values()),
                         {'supported_defect': 1, 'no_defect_supported': 1, 'unknown': 1})
        self.assertTrue(set(ids).isdisjoint(dev_ids))
        retained = {s['body'] for c in self.dataset['cases'] for s in c['source']}
        development = {s['body'] for c in self.development['cases'] for s in c['source']}
        self.assertTrue(retained.isdisjoint(development))

    def test_inputs_exclude_scoring_and_source_is_complete_consecutive_text(self):
        forbidden = ('category', 'expected', 'answer', 'rubric', 'split', 'mechanism',
                     'trigger', 'safe_probe', 'reference', 'supported_defect', 'no_defect_supported')
        for dataset in (self.dataset, self.development):
            for case in dataset['cases']:
                with self.subTest(case=case['id']):
                    self.assertEqual(set(case), {'id', 'source', 'question', 'scenario', 'assumptions'})
                    self.assertTrue(case['question'].strip() and case['scenario'].strip())
                    self.assertTrue(case['assumptions'])
                    payload = json.dumps({k: v for k, v in case.items() if k != 'id'})
                    self.assertLessEqual(len(payload.encode('utf-8')), 4096)
                    for word in forbidden:
                        self.assertNotIn('"' + word + '"', payload)
                    self.assertEqual(len(case['source']), 1)
                    source = case['source'][0]
                    self.assertEqual(set(source), {'file', 'start', 'end', 'body'})
                    self.assertEqual(source['file'], 'logic.py')
                    self.assertEqual(source['start'], 1)
                    self.assertEqual(source['end'], len(source['body'].splitlines()))
                    self.assertTrue(source['body'].endswith('\n'))
                    tree = ast.parse(source['body'])
                    self.assertTrue(all(isinstance(node, ast.FunctionDef) for node in tree.body))
                    for node in ast.walk(tree):
                        self.assertNotIsInstance(node, (ast.Import, ast.ImportFrom, ast.For, ast.AsyncFor,
                            ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.With, ast.AsyncWith))
                        if isinstance(node, ast.Attribute):
                            self.assertFalse(node.attr.startswith('_'))
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                            self.assertNotIn(node.func.id, ('open', 'exec', 'eval', 'compile', '__import__',
                                'input', 'getattr', 'setattr', 'globals', 'locals', 'breakpoint'))

    def test_exact_evidence_ranges_and_witness_contracts(self):
        for case in self.dataset['cases']:
            with self.subTest(case=case['id']):
                reference = self.reference['cases'][case['id']]
                self.assertEqual(set(reference), {'category', 'topic', 'mechanism', 'trigger',
                    'missing_evidence', 'evidence_roles', 'accepted_shape_witness', 'reject_examples', 'safe_probe'})
                validate_identifier(reference['topic'])
                expected_roles = [dict(file='logic.py', start=a, end=b, role=role)
                                  for a, b, role, _ in EVIDENCE[case['id']]]
                self.assertEqual(reference['evidence_roles'], expected_roles)
                lines = case['source'][0]['body'].splitlines()
                for start, end, _, text in EVIDENCE[case['id']]:
                    self.assertEqual('\n'.join(lines[start - 1:end]), text)
                witness = reference['accepted_shape_witness']
                self.assertEqual(_parse(json.dumps(witness), case['source']), witness)
                self.assertEqual(witness['verdict'], reference['category'])
                self.assertEqual(witness['refs'], [{k: v for k, v in r.items() if k != 'role'}
                                                   for r in expected_roles])
                unknown = reference['category'] == 'unknown'
                self.assertEqual(reference['mechanism'] is None, unknown)
                self.assertEqual(reference['trigger'] is None, unknown)
                self.assertEqual(bool(reference['missing_evidence']), unknown)
                self.assertTrue(reference['reject_examples'])
                for rejected in reference['reject_examples']:
                    self.assertEqual(set(rejected), {'reason', 'answer'})
                    self.assertTrue(rejected['reason'].strip())
                    self.assertEqual(_parse(json.dumps(rejected['answer']), case['source']), rejected['answer'])

    def test_development_references_are_valid_without_execution(self):
        for case in self.development['cases']:
            with self.subTest(case=case['id']):
                reference = self.dev_reference['cases'][case['id']]
                validate_identifier(reference['topic'])
                self.assertEqual(reference['evidence_roles'],
                    [{'file': 'logic.py', 'start': 1, 'end': 2,
                      'role': {'d01': 'prefix-operation', 'd02': 'ordering-copy',
                               'd03': 'mapping-access'}[case['id']]}])
                self.assertEqual(reference['accepted_shape_witness']['verdict'], reference['category'])
                for witness in [reference['accepted_shape_witness'],
                                *[r['answer'] for r in reference['reject_examples']]]:
                    self.assertEqual(_parse(json.dumps(witness), case['source']), witness)
                unknown = reference['category'] == 'unknown'
                self.assertEqual(reference['mechanism'] is None, unknown)
                self.assertEqual(reference['trigger'] is None, unknown)
                self.assertEqual(bool(reference['missing_evidence']), unknown)
                self.assertIsNone(reference['safe_probe'])

    def test_only_eight_fixed_repository_owned_probes_execute(self):
        self.assertEqual(set(PROBE_SOURCES), set(PROBE_RETURNS))
        self.assertEqual(len(PROBE_SOURCES), 8)
        allowed_calls = {'dict', 'list', 'demo', 'contains', 'last_or_none', 'snapshot',
                         'duplicate_row', 'collect', 'attach', 'discount', 'join_pair'}
        for case in self.dataset['cases']:
            identifier = case['id']
            reference = self.reference['cases'][identifier]
            with self.subTest(case=identifier):
                if identifier not in PROBE_SOURCES:
                    self.assertEqual(reference['category'], 'unknown')
                    self.assertIsNone(reference['safe_probe'])
                    continue  # These sources are only AST-parsed by the input test.
                body = case['source'][0]['body']
                self.assertEqual(body, PROBE_SOURCES[identifier])
                self.assertEqual(reference['safe_probe'], {'function': 'demo', 'args': [],
                                                         'expected_return': PROBE_RETURNS[identifier]})
                tree = ast.parse(body)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            self.assertIn(node.func.id, allowed_calls)
                        else:
                            self.assertIsInstance(node.func, ast.Attribute)
                            self.assertEqual(node.func.attr, 'append')
                    if isinstance(node, ast.FunctionDef):
                        self.assertFalse(any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                            and child.func.id == node.name for child in ast.walk(node)))
                namespace = {'__builtins__': {'dict': dict, 'list': list}}
                exec(compile(tree, '<fixed-synthetic-probe>', 'exec'), namespace)
                self.assertEqual(namespace['demo'](), PROBE_RETURNS[identifier])
        self.assertTrue(all(ref['safe_probe'] is None for ref in self.dev_reference['cases'].values()))

    def test_small_fixed_payload_budget(self):
        files = [path for path in ROOT.rglob('*') if path.is_file() and not path.name.startswith('._')]
        self.assertEqual({path.relative_to(ROOT).as_posix() for path in files},
            {'README.md', 'cases.json', 'reference.json', 'development/cases.json', 'development/reference.json'})
        self.assertTrue(all(not path.is_symlink() for path in files))
        self.assertLessEqual(sum(path.stat().st_size for path in files), 96 * 1024)
        self.assertLessEqual(Path(__file__).stat().st_size, 24 * 1024)


if __name__ == '__main__':
    unittest.main()
