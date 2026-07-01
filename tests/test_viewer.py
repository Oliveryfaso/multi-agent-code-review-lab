from pathlib import Path
import io
import stat
import unittest
import zipfile

from macr.viewer import TraceViewer


class ViewerTests(unittest.TestCase):
    def test_viewer_renders_latest_trace(self):
        html = TraceViewer(
            root=Path("."),
            trace_path=Path("traces/latest.json"),
            report_path=Path("reports/phase1_eval.md"),
            real_report_path=Path("reports/real_data_showcase.md"),
            patch_dir=Path("patches"),
        ).render()

        self.assertIn("Multi-Agent Code Review Lab", html)
        self.assertIn("Web Review Workbench", html)
        self.assertIn("Run Review", html)
        self.assertIn("data-review-mode", html)
        self.assertIn('data-mode-panel="ask"', html)
        self.assertIn('data-mode-panel="diff" hidden', html)
        self.assertIn("Run Observability", html)
        self.assertIn("Agent Flow Map", html)
        self.assertIn("Workflow Graph &amp; Checkpoints", html)
        self.assertIn("Checkpoints", html)
        self.assertIn("Planning", html)
        self.assertIn("Retrieval", html)
        self.assertIn("Delivery", html)
        self.assertIn("PR / Diff Review", html)
        self.assertIn("Health Signals", html)
        self.assertIn("Final Audit", html)
        self.assertIn("Human Review", html)
        self.assertIn("State Timeline", html)
        self.assertIn("Tool Calls", html)
        self.assertIn("Quality & External Eval", html)
        self.assertIn("Real Data Testing", html)
        self.assertIn("External repo eval", html)
        self.assertIn("Code Smell", html)
        self.assertIn("Eval Report", html)

    def test_viewer_rejects_unsafe_zip_paths(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("../escape.py", "print('bad')")
        data.seek(0)

        viewer = TraceViewer(root=Path("."))
        with zipfile.ZipFile(data) as archive:
            with self.assertRaises(ValueError):
                viewer._safe_extract_zip(archive, Path("/private/tmp/macr_zip_test"))

    def test_viewer_parses_multipart_without_cgi(self):
        boundary = "----macr-test-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="mode"\r\n\r\n'
            "ask\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="repo_zip"; filename="repo.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
            "zip-bytes\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

        form = TraceViewer(root=Path("."))._parse_multipart_form(io.BytesIO(body), headers, len(body))

        self.assertEqual(form["mode"].value, "ask")
        self.assertEqual(form["repo_zip"].filename, "repo.zip")
        self.assertEqual(form["repo_zip"].file.read(), b"zip-bytes")

    def test_viewer_rejects_zip_symlinks_and_zip_bombs(self):
        viewer = TraceViewer(root=Path("."))
        symlink_data = io.BytesIO()
        with zipfile.ZipFile(symlink_data, "w") as archive:
            info = zipfile.ZipInfo("repo/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
        symlink_data.seek(0)
        with zipfile.ZipFile(symlink_data) as archive:
            with self.assertRaises(ValueError):
                viewer._safe_extract_zip(archive, Path("/private/tmp/macr_zip_test"))

        size_data = io.BytesIO()
        with zipfile.ZipFile(size_data, "w") as archive:
            archive.writestr("repo/large.py", "x" * 20)
        size_data.seek(0)
        viewer.MAX_EXTRACTED_BYTES = 10
        with zipfile.ZipFile(size_data) as archive:
            with self.assertRaises(ValueError):
                viewer._safe_extract_zip(archive, Path("/private/tmp/macr_zip_test"))

    def test_viewer_renders_background_job_status(self):
        viewer = TraceViewer(root=Path("."))
        job = viewer.create_job("ask")
        html = viewer.render(job=job)

        self.assertIn("data-job-id", html)
        self.assertIn(job.job_id, html)
        self.assertIn("Background review job", html)


if __name__ == "__main__":
    unittest.main()
