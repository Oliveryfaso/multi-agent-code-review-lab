"""Static release contracts, no build or child analysis here."""
import ast
import json
import sys
import tomllib
import unittest
from pathlib import Path

class PackagingTests(unittest.TestCase):
    def test_fixed_backend_metadata_and_schema_data(self):
        from macr.investigation import evidence_records
        root = Path(evidence_records.__file__).parents[3]
        project = tomllib.loads((root / 'pyproject.toml').read_text())
        self.assertEqual(project.get('build-system', {}).get('requires'), ['setuptools==72.1.0'])
        self.assertEqual(project['project']['version'], '0.1.0')
        self.assertEqual(project['project']['dependencies'], [])
        self.assertEqual(project['project']['scripts'], {'agent-review':'macr.cli:main'})
        self.assertEqual(project['tool']['setuptools']['package-data']['macr.investigation'], ['static_evidence.schema.json'])
        self.assertEqual(project['project']['license'], {'file':'LICENSE'})

    def test_local_schema_complete_contract_and_refs(self):
        from macr.investigation import evidence_records
        from dataclasses import fields
        schema = json.loads(Path(evidence_records.__file__).with_name('static_evidence.schema.json').read_text())
        self.assertEqual(set(schema['required']), {f.name for f in fields(evidence_records.StaticEvidenceReport)})
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['kind']['const'], 'static_evidence_locate')
        self.assertEqual(schema['properties']['schema_version']['const'], 1)
        self.assertEqual(set(schema['$defs']['profile']['properties']), {f.name for f in fields(evidence_records.EvidenceProfile)})
        def refs(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    ref = value['$ref']; self.assertTrue(ref.startswith('#/'))
                    target = schema
                    for part in ref[2:].split('/'): target = target[part]
                for child in value.values(): refs(child)
            if isinstance(value, list):
                for child in value: refs(child)
        refs(schema)

    def test_static_help_and_legacy_parsers_do_not_import_providers(self):
        import contextlib, importlib.abc, io
        from unittest.mock import patch
        from macr import cli
        denied = ('macr.agents', 'macr.providers', 'macr.evals', 'macr.execution', 'macr.viewer', 'macr.workflow')
        self.assertFalse(any(name.startswith(denied) for name in sys.modules))
        class Guard(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith(denied): raise AssertionError('Unrelated import: ' + fullname)
        guard = Guard(); sys.meta_path.insert(0, guard)
        try:
            for command in ('evidence-locate','mock-recovery','static-baseline','ask','patch','review-diff','eval','prepare-real-eval','code-smell','llm-check','view'):
                with patch.object(sys, 'argv', ['agent-review', command, '--help']), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    cli.main()
                self.assertEqual(stopped.exception.code, 0)
            self.assertIsNone(cli._provider('rules'))
        finally: sys.meta_path.remove(guard)

if __name__ == '__main__': unittest.main()
