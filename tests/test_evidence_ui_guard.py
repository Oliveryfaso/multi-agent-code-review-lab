import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from run_evidence_ui_tests import physical_usage


class FixtureAllocationTests(unittest.TestCase):
    def test_directory_swap_never_counts_symlink_destination(self):
        with tempfile.TemporaryDirectory(prefix='macr-ui-guard-', dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            base = Path(temporary)
            root, outside = base / 'scan', base / 'outside'
            root.mkdir()
            outside.mkdir()
            (outside / 'unowned-by-scan.bin').write_bytes(b'x' * 8192)
            victim = root / 'victim'
            victim.mkdir()
            identity = (victim.stat().st_dev, victim.stat().st_ino)
            expected = (root.stat().st_blocks + victim.stat().st_blocks) * 512
            original = os.scandir
            swapped = []

            def swap(argument):
                if threading.current_thread() is threading.main_thread() and not swapped:
                    match = argument == victim
                    if isinstance(argument, int):
                        info = os.fstat(argument)
                        match = (info.st_dev, info.st_ino) == identity
                    if match:
                        victim.rename(base / 'moved')
                        victim.symlink_to(outside, target_is_directory=True)
                        swapped.append(True)
                return original(argument)

            with patch('os.scandir', swap):
                observed = physical_usage((root,), time.monotonic() + 2)
            self.assertEqual(len(swapped), 1)
            self.assertEqual(observed, expected)
