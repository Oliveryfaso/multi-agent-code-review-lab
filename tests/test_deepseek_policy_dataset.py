"""Offline metadata and export checks; fixture programs are AST-only evidence."""
import ast
from collections import Counter
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / 'evals' / 'deepseek-policy-dev-v1'


class DeepSeekPolicyDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT / 'cases.json').read_text())
        cls.gold = json.loads((ROOT / 'gold.json').read_text())
        spec = importlib.util.spec_from_file_location('policy_dev_export', ROOT / 'export_blind.py')
        cls.exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.exporter)

    def test_new_development_counts_and_balanced_strata(self):
        self.assertEqual(self.cases['version'], 'deepseek-policy-dev-v1')
        self.assertEqual(self.cases['split'], 'development')
        self.assertEqual(self.gold['version'], self.cases['version'])
        self.assertEqual(self.gold['split'], 'development')
        rows = self.cases['cases']
        self.assertEqual([row['id'] for row in rows], [f'p{i:02}' for i in range(1, 13)])
        self.assertEqual(set(self.gold['cases']), {row['id'] for row in rows})
        self.assertEqual(Counter(row['expected_verdict'] for row in self.gold['cases'].values()),
                         {'supported_defect': 4, 'no_defect_supported': 4, 'unknown': 4})
        self.assertEqual(Counter((row['stratum'], self.gold['cases'][row['id']]['expected_verdict'])
                                 for row in rows),
                         {(stratum, verdict): 2 for stratum in ('full_context', 'retrieval_required')
                          for verdict in ('supported_defect', 'no_defect_supported', 'unknown')})

    def test_physical_snapshots_are_bounded_code_only_and_gold_ranges_match(self):
        families = []
        for case in self.cases['cases']:
            root = ROOT / case['snapshot_root']
            self.assertEqual(case['snapshot_root'], f"snapshots/{case['id']}")
            self.assertEqual(root, root.resolve())
            self.assertFalse(root.is_symlink())
            files = sorted(path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file())
            self.assertEqual(files, case['authorized_files'])
            self.assertLessEqual(len(files), 2)
            text = {}
            for relative in files:
                path = root / relative
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.suffix, '.py')
                self.assertNotIn('test', path.name)
                source = path.read_text()
                text[relative] = source.splitlines()
                self.assertLessEqual(len(text[relative]), 24)
                self.assertLessEqual(len(source.encode()), 2048)
                tree = ast.parse(source, filename=relative)
                self.assertFalse(any(isinstance(node, ast.Import) for node in ast.walk(tree)))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertTrue(node.module == 'ctypes' or node.module + '.py' in files)
            gold = self.gold['cases'][case['id']]
            families.append(gold['family'])
            for ref in gold['evidence_roles']:
                self.assertIn(ref['file'], files)
                self.assertIs(type(ref['start']), int)
                self.assertIs(type(ref['end']), int)
                self.assertTrue(1 <= ref['start'] <= ref['end'] <= len(text[ref['file']]))
                self.assertEqual(ref['excerpt'], '\n'.join(text[ref['file']][ref['start'] - 1:ref['end']]))
            if gold['expected_verdict'] == 'unknown':
                self.assertIsNone(gold['mechanism'])
                self.assertIsNone(gold['trigger'])
                self.assertTrue(gold['missing_evidence'])
                self.assertEqual(len(gold['compatible_outcomes']), 2)
                for path in gold['missing_from_snapshot']:
                    self.assertFalse((root / path).exists())
            else:
                self.assertTrue(gold['mechanism'])
                self.assertTrue(gold['trigger'])
                self.assertEqual(gold['missing_evidence'], [])
                self.assertEqual(gold['missing_from_snapshot'], [])
            self.assertTrue(gold['rubric']['must_establish'])
            self.assertTrue(gold['rubric']['reject'])
        self.assertEqual(len(set(families)), 12)
        payload = [path for path in ROOT.rglob('*') if path.is_file()]
        self.assertLessEqual(sum(path.stat().st_size for path in payload), 96 * 1024)

    def test_no_old_source_rebranded(self):
        old_sources = set()
        for path in ROOT.parent.glob('diagnostic-*/**/cases.json'):
            data = json.loads(path.read_text())
            for case in data['cases'] if isinstance(data, dict) else data:
                old_sources.update(source['body'].strip() for source in case.get('source', []))
                old_sources.update(body.strip() for body in case.get('files', {}).values())
        for row in self.cases['cases']:
            for name in row['authorized_files']:
                self.assertNotIn((ROOT / row['snapshot_root'] / name).read_text().strip(), old_sources)

    def test_blind_export_has_explicit_arm_evidence_and_no_reference_fields(self):
        for arm in 'ABCD':
            export = self.exporter.export_cases(arm)
            self.assertEqual(export['version'], self.cases['version'])
            self.assertEqual(export['arm'], arm)
            for row, case in zip(export['cases'], self.cases['cases']):
                self.assertEqual(set(row), {'id', 'stratum', 'evidence_parity', 'question',
                                          'scenario', 'assumptions', 'entrypoint',
                                          'authorized_files', 'provided_files', 'source'})
                provided = case['authorized_files'] if arm == 'A' else case['initial_evidence_files']
                self.assertEqual(row['provided_files'], provided)
                self.assertEqual([item['file'] for item in row['source']], provided)
                for source in row['source']:
                    self.assertEqual(set(source), {'file', 'start', 'end', 'body'})
                    self.assertEqual(source['start'], 1)
                    self.assertEqual(source['end'], len(source['body'].splitlines()))
                expected = 'equal' if case['stratum'] == 'full_context' else 'unequal_by_design'
                self.assertEqual(row['evidence_parity'], expected)
                self.assertTrue(set(case['initial_evidence_files']) <= set(case['authorized_files']))
                if expected == 'equal':
                    self.assertEqual(case['initial_evidence_files'], case['authorized_files'])
                else:
                    self.assertLess(len(case['initial_evidence_files']), len(case['authorized_files']))
            encoded = json.dumps(export)
            for forbidden in ('expected_verdict', 'family', 'mechanism', 'rubric', 'missing_evidence',
                              'compatible_outcomes', 'gold.json', 'must_establish'):
                self.assertNotIn('"' + forbidden + '"', encoded)

    def test_source_export_works_when_gold_and_reviewer_files_are_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'cases.json').write_text((ROOT / 'cases.json').read_text())
            shutil.copytree(ROOT / 'snapshots', root / 'snapshots')
            self.assertEqual(self.exporter.export_cases('A', root=root), self.exporter.export_cases('A'))
            self.assertFalse((root / 'gold.json').exists())

    def test_export_refuses_path_escape_symlink_and_undeclared_files(self):
        with self.assertRaises(ValueError):
            self.exporter.export_cases('other')
        for mutation in ('escape', 'symlink', 'extra', 'oversize'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                snapshot = root / 'snapshots' / 'p01'
                snapshot.mkdir(parents=True)
                manifest = json.loads(json.dumps(self.cases))
                manifest['cases'] = [manifest['cases'][0]]
                (snapshot / 'logic.py').write_text('def total_units(values):\n    return 0\n')
                if mutation == 'escape':
                    manifest['cases'][0]['snapshot_root'] = '../outside'
                elif mutation == 'symlink':
                    (snapshot / 'logic.py').unlink()
                    (root / 'outside.py').write_text('raise RuntimeError("never execute")\n')
                    (snapshot / 'logic.py').symlink_to(root / 'outside.py')
                elif mutation == 'extra':
                    (snapshot / 'gold.json').write_text('{}')
                else:
                    (snapshot / 'logic.py').write_text(' ' * 2049)
                (root / 'cases.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    self.exporter.export_cases('A', root=root)

    def test_export_refuses_file_swapped_to_gold_after_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'cases.json').write_text((ROOT / 'cases.json').read_text())
            shutil.copytree(ROOT / 'snapshots', root / 'snapshots')
            gold = root / 'gold.json'
            gold.write_text('GOLD_SWAP_SENTINEL')
            first = self.cases['cases'][0]
            relative = first['snapshot_root'] + '/' + first['authorized_files'][0]
            source = root / relative
            safe_read = self.exporter.read_safe
            swapped = False

            def swap_before_open(base, name, limit):
                nonlocal swapped
                if name == relative and not swapped:
                    source.unlink()
                    source.symlink_to(gold)
                    swapped = True
                return safe_read(base, name, limit)

            with patch.object(self.exporter, 'read_safe', side_effect=swap_before_open):
                with self.assertRaises(ValueError):
                    self.exporter.export_cases('A', root=root)
            self.assertTrue(swapped)


if __name__ == '__main__':
    unittest.main()
