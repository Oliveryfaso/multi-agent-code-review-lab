from contextlib import redirect_stdout, redirect_stderr
import io
import unittest
from unittest.mock import patch

from macr.cli import main


class WorkbenchCLITests(unittest.TestCase):
    def test_help_is_static_and_does_not_bind_or_create_data(self):
        output = io.StringIO()
        with patch('sys.argv', ['agent-review', 'evidence-ui', '--help']), patch('socket.socket.bind', side_effect=AssertionError('help must not listen')):
            with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('--data-root', output.getvalue())

    def test_missing_or_unsupported_data_root_is_sanitized(self):
        error = io.StringIO()
        with redirect_stderr(error), patch('sys.argv', ['agent-review', 'evidence-ui', '--data-root', '/dev/null/unsupported']):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 4)
        self.assertNotIn('/dev/null', error.getvalue())
