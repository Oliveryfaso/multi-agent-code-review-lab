import errno
import json
import sqlite3
import unittest
from copy import deepcopy
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from macr.investigation import mock_recovery as recovery


class LangGraphMockRecoveryTests(unittest.TestCase):
    def setUp(self):
        from debugger_fixtures import fixture_root
        self.base = fixture_root()
        root_guard = patch.object(recovery, 'RUNS_ROOT', self.base)
        root_guard.start()
        self.addCleanup(root_guard.stop)
        self.root = self.new_root()
        self.counters = {'evidence': 0, 'report': 0}
        for target in ('socket.create_connection', 'socket.socket.connect', 'subprocess.Popen'):
            guard = patch(target, side_effect=AssertionError('external action forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def new_root(self):
        root = self.base / ('test-' + uuid4().hex)
        self.assertFalse(root.exists())
        self.assertFalse(root.with_name('._' + root.name).exists())
        return root

    def operation(self, operation, *, root=None, counters=None):
        with recovery.MockRecovery(
                root or self.root,
                counters=self.counters if counters is None else counters) as run:
            return getattr(run, operation)()

    def start(self, scenario='normal', *, payload=None):
        with recovery.MockRecovery(self.root, counters=self.counters) as run:
            return run.start(recovery.build_input(scenario=scenario)
                             if payload is None else payload)

    def assert_report(self, report, status, completed, executed):
        self.assertEqual(report['status'], status)
        self.assertIs(report['dispatch_permitted'], False)
        self.assertEqual(report['completed_mock_steps'], completed)
        self.assertEqual(report['executed_mock_steps_this_call'], executed)

    def test_normal_start_completes_and_status_does_not_call_tasks(self):
        report = self.start()
        self.assert_report(report, 'completed', {'evidence': 1, 'report': 1},
                           {'evidence': 1, 'report': 1})
        self.assertIsInstance(report['result'], dict)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 1})
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        status = self.operation('status')
        self.assert_report(status, 'completed', {'evidence': 1, 'report': 1},
                           {'evidence': 0, 'report': 0})
        self.assertEqual(status['result'], report['result'])
        self.assertEqual(self.counters, {'evidence': 1, 'report': 1})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_paused_run_resumes_in_new_instance_without_repeating_evidence(self):
        report = self.start('pause')
        self.assert_report(report, 'paused', {'evidence': 1, 'report': 0},
                           {'evidence': 1, 'report': 0})
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        status = self.operation('status')
        self.assert_report(status, 'paused', {'evidence': 1, 'report': 0},
                           {'evidence': 0, 'report': 0})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)
        resumed = self.operation('resume')
        self.assert_report(resumed, 'completed', {'evidence': 1, 'report': 1},
                           {'evidence': 0, 'report': 1})
        self.assertEqual(self.counters, {'evidence': 1, 'report': 1})

    def test_native_saved_results_survive_new_task_counters(self):
        self.start('pause')
        new_counters = {'evidence': 0, 'report': 0}
        resumed = self.operation('resume', counters=new_counters)
        self.assert_report(resumed, 'completed', {'evidence': 1, 'report': 1},
                           {'evidence': 0, 'report': 1})
        self.assertEqual(new_counters, {'evidence': 0, 'report': 1})
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})

    def test_completed_resume_is_cached_and_does_not_call_tasks(self):
        first = self.start('pause')
        self.assert_report(first, 'paused', {'evidence': 1, 'report': 0},
                           {'evidence': 1, 'report': 0})
        completed = self.operation('resume')
        self.assert_report(completed, 'completed', {'evidence': 1, 'report': 1},
                           {'evidence': 0, 'report': 1})
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        fresh = {'evidence': 0, 'report': 0}
        for _ in range(2):
            report = self.operation('resume', counters=fresh)
            self.assert_report(report, 'completed', {'evidence': 1, 'report': 1},
                               {'evidence': 0, 'report': 0})
            self.assertIs(report['cached_result'], True)
            self.assertEqual(report['result'], completed['result'])
        self.assertEqual(fresh, {'evidence': 0, 'report': 0})
        self.assertEqual(self.counters, {'evidence': 1, 'report': 1})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_unknown_outcome_blocks_resume_without_running_report(self):
        report = self.start('unknown')
        self.assert_report(report, 'blocked', {'evidence': 1, 'report': 0},
                           {'evidence': 1, 'report': 0})
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        self.assert_report(self.operation('status'), 'blocked',
                           {'evidence': 1, 'report': 0}, {'evidence': 0, 'report': 0})
        with self.assertRaises(recovery.RecoveryError):
            self.operation('resume')
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_existing_root_cannot_be_started_again(self):
        self.start()
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        with self.assertRaises(recovery.RecoveryError):
            self.start()
        self.assertEqual(self.counters, {'evidence': 1, 'report': 1})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_status_and_resume_do_not_initialize_missing_root(self):
        for operation in ('status', 'resume'):
            with self.subTest(operation=operation):
                with self.assertRaises(recovery.RecoveryError):
                    self.operation(operation)
                self.assertFalse(self.root.exists())
        self.assertEqual(self.counters, {'evidence': 0, 'report': 0})

    def test_empty_existing_root_is_not_initialized_by_inspection(self):
        self.root.mkdir()
        for operation in ('status', 'resume'):
            with self.assertRaises(recovery.RecoveryError):
                self.operation(operation)
            self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.counters, {'evidence': 0, 'report': 0})

    def test_missing_database_or_lock_is_not_recreated(self):
        for name in ('checkpoints.sqlite3', 'writer.lock'):
            with self.subTest(name=name):
                root = self.new_root()
                counters = {'evidence': 0, 'report': 0}
                with recovery.MockRecovery(root, counters=counters) as run:
                    run.start(recovery.build_input(scenario='pause'))
                remaining = ('writer.lock' if name == 'checkpoints.sqlite3'
                             else 'checkpoints.sqlite3')
                before = (root / remaining).read_bytes()
                (root / name).unlink()
                for operation in ('status', 'resume'):
                    with self.assertRaises(recovery.RecoveryError):
                        self.operation(operation, root=root, counters=counters)
                    self.assertFalse((root / name).exists())
                    self.assertEqual((root / remaining).read_bytes(), before)
                self.assertEqual(counters, {'evidence': 1, 'report': 0})

    def test_invalid_inputs_are_rejected_before_creating_state(self):
        good = recovery.build_input()
        invalid = []
        for field, value in (
                ('schema_version', True), ('schema_version', 2),
                ('workflow_version', 'other-version'), ('scenario', 'retry'),
                ('text', ''), ('text', None), ('text', 1),
                ('text', {'endpoint': 'https://example.invalid'})):
            payload = deepcopy(good)
            payload[field] = value
            invalid.append(payload)
        invalid.append(dict(good, endpoint='https://example.invalid'))
        invalid.append(dict(good, model_profile='unapproved'))
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(recovery.RecoveryError):
                    self.start(payload=payload)
                self.assertFalse(self.root.exists())
        self.assertEqual(self.counters, {'evidence': 0, 'report': 0})

    def test_oversized_text_is_rejected_before_creating_state(self):
        for text in ('x' * (recovery.MAX_TEXT_BYTES + 1),
                     '界' * (recovery.MAX_TEXT_BYTES // 3 + 1)):
            with self.subTest(utf8_bytes=len(text.encode('utf-8'))):
                with self.assertRaises(recovery.RecoveryError):
                    self.start(payload=recovery.build_input(text=text))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.counters, {'evidence': 0, 'report': 0})

    def test_exact_text_byte_limit_is_accepted(self):
        report = self.start(payload=recovery.build_input(text='x' * recovery.MAX_TEXT_BYTES))
        self.assert_report(report, 'completed', {'evidence': 1, 'report': 1},
                           {'evidence': 1, 'report': 1})

    def test_changed_workflow_version_refuses_resume_without_writes(self):
        self.start('pause')
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        with patch.object(recovery, 'WORKFLOW_VERSION', 'mock-incompatible'):
            for operation in ('status', 'resume'):
                with self.subTest(operation=operation):
                    with self.assertRaises(recovery.RecoveryError):
                        self.operation(operation)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_truncated_and_corrupt_databases_refuse_reads_without_repair(self):
        self.start('pause')
        database = self.root / 'checkpoints.sqlite3'
        valid = database.read_bytes()
        for invalid in (b'', valid[:80], b'not a sqlite database\n' * 10):
            with self.subTest(size=len(invalid)):
                database.write_bytes(invalid)
                for operation in ('status', 'resume'):
                    with self.assertRaises(recovery.RecoveryError):
                        self.operation(operation)
                    self.assertEqual(database.read_bytes(), invalid)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})

    def test_missing_native_table_is_rejected_without_repair(self):
        self.start('pause')
        database = self.root / 'checkpoints.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('DROP TABLE writes')
            connection.commit()
        before = database.read_bytes()
        for operation in ('status', 'resume'):
            with self.assertRaises(recovery.RecoveryError):
                self.operation(operation)
            self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})

    def test_missing_saved_task_result_refuses_replay_without_writes(self):
        self.start('pause')
        database = self.root / 'checkpoints.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                'SELECT checkpoint_id FROM checkpoints '
                'WHERE thread_id=? AND checkpoint_ns=? '
                'ORDER BY checkpoint_id DESC LIMIT 1',
                ('mock-recovery', '')).fetchone()
            self.assertIsNotNone(row)
            removed = connection.execute(
                'DELETE FROM writes WHERE thread_id=? AND checkpoint_ns=? '
                'AND checkpoint_id=? AND channel=?',
                ('mock-recovery', '', row[0], '__return__'))
            self.assertEqual(removed.rowcount, 1)
            connection.commit()
        before = database.read_bytes()
        for operation in ('status', 'resume'):
            with self.assertRaises(recovery.RecoveryError):
                self.operation(operation)
            self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})

    def test_wrong_saved_task_id_refuses_cache_miss_before_execution(self):
        self.start('pause')
        database = self.root / 'checkpoints.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                'SELECT checkpoint_id FROM checkpoints '
                'WHERE thread_id=? AND checkpoint_ns=? '
                'ORDER BY checkpoint_id DESC LIMIT 1',
                ('mock-recovery', '')).fetchone()
            self.assertIsNotNone(row)
            changed = connection.execute(
                'UPDATE writes SET task_id=? WHERE thread_id=? AND checkpoint_ns=? '
                'AND checkpoint_id=? AND channel=?',
                (str(uuid4()), 'mock-recovery', '', row[0], '__return__'))
            self.assertEqual(changed.rowcount, 1)
            connection.commit()
        fresh = {'evidence': 0, 'report': 0}
        with self.assertRaises(recovery.RecoveryError):
            self.operation('resume', counters=fresh)
        self.assertEqual(fresh, {'evidence': 0, 'report': 0})
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})
        self.assertTrue(database.is_file())

    def test_saved_metadata_version_input_and_identity_are_validated(self):
        for changed in ('version', 'input', 'missing_identity'):
            with self.subTest(changed=changed):
                root = self.new_root()
                counters = {'evidence': 0, 'report': 0}
                with recovery.MockRecovery(root, counters=counters) as run:
                    run.start(recovery.build_input(scenario='pause'))
                database = root / 'checkpoints.sqlite3'
                with closing(sqlite3.connect(database)) as connection:
                    row = connection.execute(
                        'SELECT checkpoint_id, metadata FROM checkpoints '
                        'WHERE thread_id=? AND checkpoint_ns=? '
                        'ORDER BY checkpoint_id DESC LIMIT 1',
                        ('mock-recovery', '')).fetchone()
                    self.assertIsNotNone(row)
                    checkpoint_id, metadata_bytes = row
                    metadata = json.loads(metadata_bytes)
                    identity = json.loads(metadata['mock_recovery'])
                    self.assertEqual(identity['packages']['langgraph-checkpoint'], '4.2.0')
                    if changed == 'version':
                        identity['workflow_version'] = 'mock-incompatible'
                    elif changed == 'input':
                        identity['input']['text'] = 'tampered synthetic input'
                    else:
                        metadata.pop('mock_recovery')
                    if changed != 'missing_identity':
                        metadata['mock_recovery'] = json.dumps(
                            identity, sort_keys=True, separators=(',', ':'))
                    connection.execute(
                        'UPDATE checkpoints SET metadata=? '
                        'WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?',
                        (json.dumps(metadata, separators=(',', ':')).encode('utf-8'),
                         'mock-recovery', '', checkpoint_id))
                    connection.commit()
                before = database.read_bytes()
                for operation in ('status', 'resume'):
                    with self.assertRaises(recovery.RecoveryError):
                        self.operation(operation, root=root, counters=counters)
                    self.assertEqual(database.read_bytes(), before)
                self.assertEqual(counters, {'evidence': 1, 'report': 0})

    def test_database_over_limit_is_rejected_without_writes(self):
        self.start('pause')
        database = self.root / 'checkpoints.sqlite3'
        before = database.read_bytes()
        with patch.object(recovery, 'MAX_BYTES', len(before) - 1):
            for operation in ('status', 'resume'):
                with self.assertRaises(recovery.RecoveryError):
                    self.operation(operation)
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})

    def test_nested_and_symlink_roots_are_rejected(self):
        with self.assertRaises(recovery.RecoveryError):
            with recovery.MockRecovery(self.root / 'nested', counters=self.counters) as run:
                run.start(recovery.build_input())
        self.assertFalse(self.root.exists())
        self.start('pause')
        link = self.new_root()
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError as error:
            if error.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
                raise
            self.skipTest('filesystem does not support symlinks')
        self.addCleanup(link.unlink)
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        for operation in ('status', 'resume'):
            with self.assertRaises(recovery.RecoveryError):
                self.operation(operation, root=link)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_database_symlink_is_rejected_without_following_it(self):
        self.start('pause')
        destination = self.new_root()
        destination.mkdir()
        database = self.root / 'checkpoints.sqlite3'
        moved = destination / 'checkpoints.sqlite3'
        database.rename(moved)
        try:
            database.symlink_to(moved)
        except OSError as error:
            if error.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
                raise
            self.skipTest('filesystem does not support symlinks')
        self.addCleanup(database.unlink)
        before = moved.read_bytes()
        for operation in ('status', 'resume'):
            with self.assertRaises(recovery.RecoveryError):
                self.operation(operation)
            self.assertEqual(moved.read_bytes(), before)
        self.assertEqual(self.counters, {'evidence': 1, 'report': 0})


if __name__ == '__main__':
    unittest.main()
