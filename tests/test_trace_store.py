"""Synthetic persistence failures; no retained trace or checkpoint is opened."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.investigation.validation import ContractError
from macr.memory.trace_store import TraceStore
from macr.schemas import Trace


class TraceStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root() / 'traces'
        self.store = TraceStore(self.root)

    def trace(self, query):
        return Trace(query, 'synthetic-source')

    def test_saved_trace_and_latest_keep_the_existing_json_format(self):
        trace = self.trace('synthetic question')
        path = self.store.save(trace)
        self.assertEqual(path, self.root / (trace.task_id + '.json'))
        self.assertEqual(path.read_bytes(), (self.root / 'latest.json').read_bytes())
        self.assertEqual(json.loads(path.read_text()), trace.to_dict())
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_failed_latest_replace_keeps_previous_complete_latest(self):
        previous = self.trace('previous synthetic question')
        self.store.save(previous)
        before = (self.root / 'latest.json').read_bytes()
        next_trace = self.trace('next synthetic question')
        import os
        replace = os.replace

        def fail_latest(source, target):
            if Path(target).name == 'latest.json':
                raise OSError('synthetic latest replacement failure')
            return replace(source, target)

        with patch('os.replace', side_effect=fail_latest):
            with self.assertRaises(OSError):
                self.store.save(next_trace)
        self.assertEqual((self.root / 'latest.json').read_bytes(), before)
        self.assertEqual(json.loads((self.root / (next_trace.task_id + '.json')).read_text()), next_trace.to_dict())
        self.assertTrue(list(self.root.glob('*.tmp')), 'failed write evidence must remain')

    def test_failed_flush_keeps_existing_trace_and_latest(self):
        trace = self.trace('original synthetic question')
        path = self.store.save(trace)
        before = path.read_bytes()
        trace.query = 'replacement synthetic question'
        with patch('os.fsync', side_effect=OSError('synthetic flush failure')):
            with self.assertRaises(OSError):
                self.store.save(trace)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((self.root / 'latest.json').read_bytes(), before)

    def test_invalid_identifier_and_nonfinite_json_fail_before_write(self):
        trace = self.trace('synthetic question')
        trace.task_id = '../escape'
        with self.assertRaises(ContractError):
            self.store.save(trace)
        trace.task_id = 'valid-id'
        trace.metrics['bad'] = float('nan')
        with self.assertRaises(ValueError):
            self.store.save(trace)
        self.assertFalse(list(self.root.iterdir()))
        self.assertFalse((self.root.parent / 'escape.json').exists())
