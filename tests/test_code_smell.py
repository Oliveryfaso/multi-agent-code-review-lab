import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files

from macr.agents.code_smell import CodeSmellAgent


class CodeSmellAgentTests(unittest.TestCase):
    def test_reports_low_smell_for_small_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.py").write_text("def add(a, b):\n    return a + b\n")

            report = CodeSmellAgent().analyze(root)

        self.assertEqual(report["severity"], "low")
        self.assertEqual(report["smell_ratio"], 0.0)

    def test_detects_high_branch_function_hotspot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            branches = "\n".join(f"    if value == {index}:\n        return {index}" for index in range(14))
            (root / "bad.py").write_text(f"def route(value, a, b, c, d, e, f, g):\n{branches}\n    return None\n")

            report = CodeSmellAgent().analyze(root)

        self.assertGreater(report["smell_ratio"], 0)
        self.assertTrue(report["hotspots"])
        self.assertEqual(report["hotspots"][0]["symbol"], "route")


    def test_appledouble_files_and_subtrees_are_filtered_before_read(self):
        root = write_files(fixture_root(), {'ok.py': 'def add(a, b):\n    return a + b\n'})
        leaf = root / '._injected.py'
        leaf.write_bytes(b'\x00AppleDouble-fixture')
        nested = root / '._metadata' / 'nested.py'
        nested.parent.mkdir()
        nested.write_text('invalid metadata (:')
        original_read = Path.read_text

        def read_source(path, *args, **kwargs):
            if any(part.startswith('._') for part in path.relative_to(root).parts):
                raise AssertionError('metadata must be excluded before source reads')
            return original_read(path, *args, **kwargs)

        with patch.object(Path, 'read_text', read_source):
            report = CodeSmellAgent().analyze(root)
        self.assertEqual(report['python_file_count'], 1)
        self.assertEqual(report['severity'], 'low')
        self.assertEqual(report['smell_ratio'], 0.0)
        self.assertEqual(report['hotspots'], [])
        self.assertEqual(leaf.read_bytes(), b'\x00AppleDouble-fixture')
        self.assertEqual(nested.read_text(), 'invalid metadata (:')

    def test_real_python_syntax_error_is_still_reported(self):
        root = write_files(fixture_root(), {'broken.py': 'def broken(:\n'})
        report = CodeSmellAgent().analyze(root)
        self.assertEqual(report['python_file_count'], 1)
        self.assertEqual(report['severity'], 'high')
        self.assertEqual(report['hotspots'][0]['file'], 'broken.py')
        self.assertEqual(report['hotspots'][0]['kind'], 'syntax_error')


if __name__ == "__main__":
    unittest.main()
