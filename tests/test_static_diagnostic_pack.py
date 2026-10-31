"""Strict neutral inputs; source bytes are read as data, never executed."""
import copy
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files

try:
    pack_module = importlib.import_module('macr.evals.static_diagnostic_pack')
except ModuleNotFoundError:
    pack_module = None

PROJECT = Path(__file__).resolve().parents[1]


def quality_pack():
    tasks = []
    for number in range(9):
        tasks.append({'id': f'n{number + 1:02d}', 'project_id': f'project-{number // 3}',
            'snapshot_root': f'snapshots/n{number + 1:02d}',
            'question': 'Explain the captured code property.', 'scenario': 'Ordinary Python values and unchanged module bindings.',
            'assumptions': [], 'authorized_files': ['entry.py', 'helper.py'],
            'initial_evidence_files': ['entry.py', 'helper.py'],
            'entrypoint': {'file': 'entry.py', 'symbol': 'start'}, 'entries': ['entry.py:start'],
            'license': {'spdx': 'MIT', 'provenance': 'Self-authored structural loader fixture, not a quality task.'}})
    return {'schema': 'macr.static-diagnostic.pack.v1', 'pack_id': 'structural-fixture-v1', 'tasks': tasks}


class StaticDiagnosticPackTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pack_module, 'new static diagnostic pack loader missing')
        for target in ('subprocess.Popen', 'socket.create_connection', 'urllib.request.build_opener'):
            guard = patch(target, side_effect=AssertionError('pack loading must remain data-only'))
            guard.start(); self.addCleanup(guard.stop)

    def root(self, data=None, *, source=None):
        data = quality_pack() if data is None else data
        files = {'pack.json': json.dumps(data)}
        for task in data['tasks']:
            for relative in task['authorized_files']:
                files[task['snapshot_root'] + '/' + relative] = (
                    "raise RuntimeError('captured fixture must never execute')\n"
                    'def start(value=None):\n    return value\n') if source is None else source
        return write_files(fixture_root(), files)

    def test_quality_exact_fields_full_source_and_raw_dict_are_preserved(self):
        data = quality_pack();root = self.root(data)
        observed = pack_module.load_pack(root)
        self.assertEqual(observed, data)
        self.assertEqual(pack_module.PACK_SCHEMA, data['schema'])
        self.assertEqual(set(observed['tasks'][0]), pack_module.TASK_FIELDS)
        self.assertEqual(len(observed['tasks']), 9)

    def test_grid_and_quality_source_counts_are_exact(self):
        for change in (lambda p: p['tasks'].pop(),
                       lambda p: p['tasks'][0].update(project_id='fourth-project'),
                       lambda p: p['tasks'][0].update(id=p['tasks'][1]['id']),
                       lambda p: p['tasks'][0].update(snapshot_root=p['tasks'][1]['snapshot_root']),
                       lambda p: p['tasks'][0].update(authorized_files=['entry.py'], initial_evidence_files=['entry.py'])):
            data = quality_pack();change(data)
            with self.subTest(change=change.__code__.co_firstlineno), self.assertRaises(ValueError):
                pack_module.load_pack(self.root(data))

    def test_unknown_or_answer_fields_and_legacy_schema_are_rejected(self):
        for change in (lambda p: p.update(schema='macr.deepseek.study.pack.v1'),
                       lambda p: p.update(gold='forbidden'),
                       lambda p: p['tasks'][0].update(expected_verdict='unsupported-label'),
                       lambda p: p['tasks'][0].update(source='invented-top-field'),
                       lambda p: p['tasks'][0].update(provenance='invented-top-field'),
                       lambda p: p['tasks'][0].update(license={'spdx': 'MIT'})):
            data = quality_pack();change(data)
            with self.subTest(change=change.__code__.co_firstlineno), self.assertRaises(ValueError):
                pack_module.load_pack(self.root(data))

    def test_entries_are_unique_safe_authorized_and_match_first_entrypoint(self):
        for entries, entrypoint in [([], {'file': 'entry.py', 'symbol': 'start'}),
            (['entry.py:start'] * 2, {'file': 'entry.py', 'symbol': 'start'}),
            (['entry.py:start'] * 7, {'file': 'entry.py', 'symbol': 'start'}),
            (['other.py:start'], {'file': 'entry.py', 'symbol': 'start'}),
            (['../entry.py:start'], {'file': 'entry.py', 'symbol': 'start'}),
            (['entry.py:invalid-symbol'], {'file': 'entry.py', 'symbol': 'start'}),
            (['entry.py:start'], {'file': 'entry.py', 'symbol': 'different'}),
            (['entry.py:start'], {'file': 'entry.py', 'symbol': 'start', 'extra': True})]:
            data = quality_pack();data['tasks'][0].update(entries=entries, entrypoint=entrypoint)
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                pack_module.load_pack(self.root(data))

    def test_partial_initial_source_and_private_paths_fail_before_extra_source_read(self):
        for change in (lambda p: p['tasks'][0].update(initial_evidence_files=['entry.py']),
                       lambda p: p['tasks'][0].update(authorized_files=['entry.py', '.env.py']),
                       lambda p: p['tasks'][0].update(authorized_files=['entry.py', 'test_helper.py'])):
            data=quality_pack();change(data)
            with self.subTest(change=change.__code__.co_firstlineno), self.assertRaises(ValueError):
                pack_module.load_pack(self.root(data))

    def test_source_pack_and_task_text_limits_are_not_silently_truncated(self):
        for body in ('x' * 4097, ''):
            with self.subTest(source_bytes=len(body)), self.assertRaises(ValueError):
                pack_module.load_pack(self.root(source=body))
        data=quality_pack();data['tasks'][0]['question']='é' * 2049
        with self.assertRaises(ValueError):pack_module.load_pack(self.root(data))
        root=self.root();raw=(root/'pack.json').read_text();(root/'pack.json').write_text(raw + ' ' * 65536)
        with self.assertRaises(ValueError):pack_module.load_pack(root)

    def test_extra_files_directories_readme_and_symlink_are_rejected_for_quality(self):
        for extra in ('README.md', 'unlisted.py', 'rubric.json', 'snapshots/n01/extra.py'):
            root=self.root();(root/extra).write_text('not allowed')
            with self.subTest(extra=extra), self.assertRaises(ValueError):pack_module.load_pack(root)
        root=self.root();(root/'empty-extra').mkdir()
        with self.assertRaises(ValueError):pack_module.load_pack(root)
        root=self.root();link=root/'link.py'
        link.symlink_to(root/'snapshots/n01/entry.py')
        try:
            with self.assertRaises(ValueError):pack_module.load_pack(root)
        finally:
            link.unlink()

    def test_duplicate_json_nonfinite_and_bad_source_encoding_are_rejected(self):
        for body in ('{"schema":"one","schema":"two"}', '{"x":NaN}'):
            root=self.root();(root/'pack.json').write_text(body)
            with self.subTest(body=body), self.assertRaises(ValueError):pack_module.load_pack(root)
        root=self.root();(root/'snapshots/n01/entry.py').write_bytes(b'\xff\xfeinvalid')
        with self.assertRaises(ValueError):pack_module.load_pack(root)

    def test_published_development_pack_uses_existing_sources_and_only_readme_exception(self):
        root=PROJECT/'examples/static-diagnostic-development'
        data=pack_module.load_pack(root, development=True)
        self.assertEqual({t['id'] for t in data['tasks']}, {'dev-defect','dev-healthy','dev-dynamic'})
        self.assertEqual({t['snapshot_root'] for t in data['tasks']}, {'keyword-defect','keyword-healthy','dynamic'})
        self.assertEqual(len({t['project_id'] for t in data['tasks']}), 3)
        self.assertTrue(all(t['license']['spdx']=='MIT' for t in data['tasks']))
        self.assertEqual(len(next(t for t in data['tasks'] if t['id']=='dev-dynamic')['authorized_files']), 1)
        with self.assertRaises(ValueError):pack_module.load_pack(root)
        copied=self.root(data)
        (copied/'README.md').write_text('Only this development metadata file is allowed.')
        self.assertEqual(pack_module.load_pack(copied, development=True), data)
        (copied/'notes.md').write_text('unexpected')
        with self.assertRaises(ValueError):pack_module.load_pack(copied, development=True)
