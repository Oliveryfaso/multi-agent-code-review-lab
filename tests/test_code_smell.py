import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
