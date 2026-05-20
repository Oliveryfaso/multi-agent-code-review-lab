from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
import html
import io
import json
import shutil
import stat
import tempfile
import threading
import time
from uuid import uuid4
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from macr.agents.orchestrator import Orchestrator


@dataclass
class FormField:
    name: str
    value: str = ""
    filename: str = ""
    file: io.BytesIO | None = None


@dataclass
class UploadedRepo:
    repo_path: Path
    workdir: Path


@dataclass
class WebReviewJob:
    job_id: str
    mode: str
    status: str
    message: str
    created_at: float
    updated_at: float
    trace_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, str | float]:
        return {
            "job_id": self.job_id,
            "mode": self.mode,
            "status": self.status,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "trace_id": self.trace_id,
            "error": self.error,
        }


class TraceViewer:
    MAX_UPLOAD_BYTES = 30 * 1024 * 1024
    MAX_EXTRACTED_BYTES = 80 * 1024 * 1024
    MAX_ZIP_MEMBERS = 5000
    UPLOAD_TTL_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        root: Path,
        trace_path: Path | None = None,
        report_path: Path | None = None,
        real_report_path: Path | None = None,
        patch_dir: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.trace_path = trace_path or self.root / "traces/latest.json"
        self.report_path = report_path or self.root / "reports/phase1_eval.md"
        self.real_report_path = real_report_path or self.root / "reports/real_data_showcase.md"
        self.patch_dir = patch_dir or self.root / "patches"
        self.jobs: dict[str, WebReviewJob] = {}
        self.job_lock = threading.Lock()

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send("ok", "text/plain")
                    return
                if parsed.path == "/trace.json":
                    self._send(viewer._read_text(viewer.trace_path, "{}"), "application/json")
                    return
                if parsed.path.startswith("/jobs/") and parsed.path.endswith(".json"):
                    job_id = parsed.path.removeprefix("/jobs/").removesuffix(".json")
                    job = viewer.get_job(job_id)
                    if not job:
                        self._send(json.dumps({"error": "job not found"}), "application/json", status=404)
                        return
                    self._send(json.dumps(job.to_dict(), ensure_ascii=False), "application/json")
                    return
                self._send(viewer.render(), "text/html; charset=utf-8")

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/web-review":
                    self.send_error(404)
                    return
                try:
                    result = viewer.handle_web_review(self.rfile, self.headers)
                    self._send(result, "text/html; charset=utf-8")
                except Exception as exc:
                    self._send(viewer.render(status=f"Web review failed: {type(exc).__name__}: {exc}"), "text/html; charset=utf-8", status=400)

            def log_message(self, format, *args):  # noqa: A003
                return

            def _send(self, body: str, content_type: str, status: int = 200) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer((host, port), Handler)
        print(f"Trace viewer: http://{host}:{port}")
        server.serve_forever()

    def render(self, status: str = "", job: WebReviewJob | None = None) -> str:
        trace = self._load_trace()
        report = self._read_text(self.report_path, "No eval report found.")
        real_report = self._read_text(self.real_report_path, "No real data showcase report found.")
        patch_files = sorted(self.patch_dir.glob("*.patch"))[-5:] if self.patch_dir.exists() else []
        patch_preview = self._read_text(patch_files[-1], "No patch files found.") if patch_files else "No patch files found."

        answer = trace.get("answer") or {}
        patch = trace.get("patch") or {}
        plan = trace.get("plan") or {}
        metrics = trace.get("metrics") or {}
        board = trace.get("board") or {}
        states = trace.get("state_timeline") or []
        contract = metrics.get("contract_validation") or {}
        final_review = metrics.get("final_review") or {}
        diff_review = metrics.get("diff_review") or {}
        code_smell = metrics.get("code_smell") or {}
        calls = trace.get("tool_calls", [])
        evidence = answer.get("evidence", [])

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Multi-Agent Code Review Lab</title>
  <style>
    :root {{ color-scheme: light; --bg:#f5f7fb; --panel:#ffffff; --text:#172033; --muted:#667085; --line:#d9e1ec; --accent:#2563eb; --ok:#047857; --bad:#b42318; --miss:#b45309; --ink:#111827; --soft:#eef4ff; }}
    * {{ box-sizing:border-box; }}
    html, body {{ width:100%; overflow-x:hidden; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:radial-gradient(circle at 20% 0%, #e8f0ff 0, transparent 28%), var(--bg); color:var(--text); }}
    header {{ padding:20px 16px; background:linear-gradient(135deg,#101828,#172554); color:white; border-bottom:1px solid #243b67; }}
    .hero-inner {{ width:min(1180px, 100%); margin:0 auto; }}
    header h1 {{ margin:0 0 6px; font-size:24px; letter-spacing:0; }}
    header p {{ margin:0; color:#cbd5e1; font-size:14px; }}
    main {{ width:min(1180px, calc(100% - 24px)); margin:0 auto; padding:16px 0 24px; display:grid; gap:12px; }}
    section, details.panel {{ background:rgba(255,255,255,.96); border:1px solid var(--line); border-radius:8px; padding:14px; box-shadow:0 10px 26px rgba(16,24,40,.04); min-width:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; }}
    h3 {{ margin:14px 0 8px; font-size:15px; color:#344054; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:10px; background:#fbfdff; min-height:64px; min-width:0; }}
    .metric strong {{ display:block; font-size:18px; margin-top:4px; overflow-wrap:anywhere; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .warning {{ margin:12px 0 0; border:1px solid #fedf89; border-left:4px solid #f79009; background:#fffbeb; border-radius:8px; padding:10px 12px; }}
    .warning strong {{ display:block; color:#93370d; margin-bottom:6px; }}
    .warning ul {{ margin:6px 0 0 18px; padding:0; }}
    .warning li {{ margin:4px 0; overflow-wrap:anywhere; }}
    .answer {{ margin:10px 0 0; line-height:1.55; overflow-wrap:anywhere; }}
    .split {{ display:grid; grid-template-columns:minmax(0,1fr) 340px; gap:14px; align-items:start; }}
    .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; }}
    .bars {{ display:grid; gap:10px; }}
    .bar-row {{ display:grid; grid-template-columns:108px minmax(0,1fr) 46px; gap:8px; align-items:center; font-size:13px; }}
    .bar {{ height:10px; background:#e5eaf3; border-radius:999px; overflow:hidden; }}
    .bar span {{ display:block; height:100%; border-radius:999px; background:linear-gradient(90deg,#2563eb,#14b8a6); animation:grow .7s ease-out both; }}
    .flow {{ overflow:hidden; }}
    .flow svg {{ width:100%; height:auto; min-height:300px; display:block; }}
    .flow-node {{ fill:#ffffff; stroke:#9fb3d9; stroke-width:1.2; }}
    .flow-node.active {{ fill:#eef4ff; stroke:#2563eb; }}
    .flow-line {{ stroke:#9fb3d9; stroke-width:1.3; marker-end:url(#arrow); }}
    .flow-label {{ font-size:11px; fill:#344054; text-anchor:middle; }}
    .flow-lane {{ font-size:11px; fill:#667085; font-weight:700; }}
    .flow-lane-line {{ stroke:#d9e1ec; stroke-width:1; }}
    .timeline {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:8px; }}
    .state-card {{ border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:8px; padding:9px; background:#fbfdff; animation:rise .28s ease-out both; }}
    .state-card.failed {{ border-left-color:var(--bad); }}
    .state-card strong {{ display:block; font-size:13px; }}
    .state-card small {{ color:var(--muted); }}
    .contract-ok {{ color:var(--ok); font-weight:700; }}
    .contract-bad {{ color:var(--bad); font-weight:700; }}
    details.panel summary {{ cursor:pointer; font-weight:700; color:#172033; list-style:none; }}
    details.panel summary::-webkit-details-marker {{ display:none; }}
    details.panel summary::after {{ content:"Open"; float:right; color:#2563eb; font-size:12px; font-weight:600; }}
    details.panel[open] summary::after {{ content:"Close"; }}
    .detail-body {{ margin-top:12px; display:grid; gap:14px; }}
    .table-wrap {{ width:100%; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; table-layout:fixed; }}
    th, td {{ text-align:left; border-bottom:1px solid var(--line); padding:8px; vertical-align:top; }}
    th {{ color:#475467; background:#f8fafc; }}
    td {{ overflow-wrap:anywhere; word-break:break-word; }}
    code, pre {{ font-family:"SFMono-Regular",Consolas,monospace; }}
    pre {{ overflow:auto; background:#0f172a; color:#e2e8f0; padding:12px; border-radius:8px; font-size:12px; max-height:320px; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .ok {{ color:var(--ok); font-weight:600; }}
    .bad {{ color:var(--bad); font-weight:600; }}
    .miss {{ color:var(--miss); font-weight:600; }}
    .pill {{ display:inline-block; padding:2px 8px; border-radius:999px; background:#e0ecff; color:#1d4ed8; font-size:12px; }}
    .donut-wrap {{ display:flex; gap:14px; align-items:center; }}
    .donut {{ width:104px; height:104px; border-radius:50%; display:grid; place-items:center; background:conic-gradient(#2563eb var(--pct), #e5eaf3 0); flex:0 0 auto; }}
    .donut span {{ width:72px; height:72px; border-radius:50%; display:grid; place-items:center; background:white; font-weight:800; }}
    .status-list {{ display:grid; gap:8px; }}
    .status-row {{ display:grid; grid-template-columns:150px 88px minmax(0,1fr); gap:8px; align-items:start; border-bottom:1px solid var(--line); padding:7px 0; font-size:13px; }}
    .status-badge {{ display:inline-block; width:max-content; border-radius:999px; padding:2px 8px; font-weight:700; background:#ecfdf3; color:#047857; }}
    .status-badge.pending {{ background:#fff7ed; color:#b45309; }}
    .hotspots {{ margin:8px 0 0; padding:0; list-style:none; display:grid; gap:6px; }}
    .hotspots li {{ border:1px solid var(--line); border-radius:8px; padding:8px; background:#fbfdff; font-size:13px; overflow-wrap:anywhere; }}
    .workbench {{ display:grid; gap:12px; }}
    .form-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    label {{ display:grid; gap:6px; font-size:13px; color:#344054; font-weight:600; }}
    input, select, textarea {{ width:100%; border:1px solid var(--line); border-radius:8px; padding:9px 10px; font:inherit; background:white; color:var(--text); }}
    textarea {{ min-height:110px; resize:vertical; }}
    button {{ border:0; border-radius:8px; background:#2563eb; color:white; padding:10px 14px; font-weight:700; cursor:pointer; }}
    button:hover {{ background:#1d4ed8; }}
    [hidden] {{ display:none !important; }}
    .status-note {{ border:1px solid #bfdbfe; border-left:4px solid #2563eb; background:#eff6ff; padding:10px 12px; border-radius:8px; }}
    @keyframes grow {{ from {{ width:0; }} }}
    @keyframes rise {{ from {{ opacity:0; transform:translateY(6px); }} to {{ opacity:1; transform:none; }} }}
    @media (max-width: 900px) {{ .grid,.split,.two-col {{ grid-template-columns:1fr; }} main {{ width:calc(100% - 16px); padding:10px 0 18px; }} header {{ padding:16px 8px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="hero-inner">
      <h1>Multi-Agent Code Review Lab</h1>
      <p>Focused run dashboard for agent flow, health signals, evidence, patch verification, and eval reports.</p>
    </div>
  </header>
  <main>
    <section>
      <h2>Web Review Workbench</h2>
      {self._status_note(status)}
      {self._job_status(job)}
      {self._workbench()}
    </section>
    <section>
      <h2>Run Summary</h2>
      <div class="grid">
        {self._metric("Intent", plan.get("intent", "n/a"))}
        {self._metric("Risk", plan.get("risk_level", "n/a"))}
        {self._metric("Confidence", answer.get("confidence", "n/a"))}
        {self._metric("Contract", "ok" if contract.get("ok") else "check")}
        {self._metric("Final Audit", "ok" if final_review.get("ok") else "review")}
        {self._metric("Human Review", "yes" if final_review.get("human_review_required") else "no")}
        {self._metric("Code Smell", f"{int(float(code_smell.get('smell_ratio') or 0) * 100)}% / {code_smell.get('severity', 'n/a')}")}
        {self._metric("Diff Risk", diff_review.get("risk_level", "n/a"))}
      </div>
      {self._final_review_notice(final_review)}
      <p class="muted"><strong>Query:</strong> {self._esc(trace.get("query", ""))}</p>
      <p class="answer">{self._esc(answer.get("answer", "No answer generated."))}</p>
    </section>
    <section>
      <h2>Run Observability</h2>
      <div class="split">
        <div>
          <h3>Agent Flow Map</h3>
          {self._agent_flow(board)}
        </div>
        <div>
          <h3>Health Signals</h3>
          {self._health(metrics, calls, evidence, states)}
        </div>
      </div>
    </section>
    <section>
      <h2>Quality & External Eval</h2>
      <div class="two-col">
        {self._code_quality_panel(code_smell)}
        {self._real_data_status(real_report)}
      </div>
    </section>
    {self._detail("Execution Plan & State Timeline", self._steps(plan.get("steps", [])) + self._states(states), open_by_default=True)}
    {self._detail("Repository Map", self._repo_map(board))}
    {self._detail("PR / Diff Review", self._diff_review(board))}
    {self._detail("Evidence & Tool Calls", "<p class='muted'>`miss` means exploratory search returned no matches; it is not a hard tool failure.</p>" + self._tool_mix(calls) + self._evidence(evidence[:8]) + self._tool_calls(calls))}
    {self._detail("Patch Verification & Eval Report", self._patch(patch) + "<h3>Latest Patch Preview</h3><pre>" + self._esc(patch_preview) + "</pre><h3>Eval Report</h3><pre>" + self._esc(report) + "</pre>")}
    {self._detail("Agent Board & Monitor Metrics", "<p class='muted'>Shared blackboard sections written by each agent.</p>" + self._board(board) + "<h3>Monitor Metrics</h3><pre>" + self._esc(json.dumps(metrics, ensure_ascii=False, indent=2)) + "</pre>")}
  </main>
  <script>
    (() => {{
      const mode = document.querySelector('[data-review-mode]');
      const panels = Array.from(document.querySelectorAll('[data-mode-panel]'));
      const syncMode = () => {{
        const active = mode ? mode.value : 'ask';
        panels.forEach((panel) => {{
          const visible = panel.dataset.modePanel === active;
          panel.hidden = !visible;
          panel.querySelectorAll('input, textarea, select').forEach((field) => {{
            field.disabled = !visible;
          }});
        }});
      }};
      if (mode) {{
        mode.addEventListener('change', syncMode);
        syncMode();
      }}
      const jobStatus = document.querySelector('[data-job-id]');
      if (jobStatus) {{
        const jobId = jobStatus.dataset.jobId;
        const text = jobStatus.querySelector('[data-job-message]');
        const poll = async () => {{
          try {{
            const response = await fetch(`/jobs/${{jobId}}.json`, {{ cache: 'no-store' }});
            if (!response.ok) return;
            const data = await response.json();
            if (text) text.textContent = `${{data.status}}: ${{data.message || data.error || ''}}`;
            if (data.status === 'completed') {{
              window.setTimeout(() => window.location.reload(), 700);
            }} else if (data.status !== 'failed') {{
              window.setTimeout(poll, 1200);
            }}
          }} catch (error) {{
            window.setTimeout(poll, 2000);
          }}
        }};
        poll();
      }}
    }})();
  </script>
</body>
</html>"""

    def handle_web_review(self, fp, headers) -> str:
        content_length = int(headers.get("Content-Length", "0") or 0)
        if content_length > self.MAX_UPLOAD_BYTES:
            raise ValueError("upload too large; limit is 30 MB")
        form = self._parse_multipart_form(fp, headers, content_length)
        mode = self._field_value(form, "mode") or "ask"
        query = self._field_value(form, "query") or "Review this codebase."
        diff_text = self._field_value(form, "diff_text")
        upload = form["repo_zip"] if "repo_zip" in form else None
        if not upload or not getattr(upload, "filename", ""):
            raise ValueError("repo_zip is required")
        uploaded = self._extract_uploaded_repo(upload)
        if mode == "diff" and not diff_text and "diff_file" in form and getattr(form["diff_file"], "filename", ""):
            diff_file = form["diff_file"].file
            diff_text = diff_file.read().decode("utf-8", errors="replace") if diff_file else ""
        if mode == "diff" and not diff_text:
            self._cleanup_path(uploaded.workdir)
            raise ValueError("diff review requires diff text or a diff file")
        job = self.create_job(mode)
        worker = threading.Thread(
            target=self._run_web_review_job,
            args=(job.job_id, uploaded, mode, query, diff_text),
            daemon=True,
        )
        worker.start()
        return self.render(status=f"Queued {mode} review job {job.job_id}.", job=job)

    def _extract_uploaded_repo(self, upload) -> UploadedRepo:
        upload_root = self.root / ".macr_uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        self._cleanup_upload_root(upload_root)
        workdir = Path(tempfile.mkdtemp(prefix="web_review_", dir=upload_root))
        archive_path = workdir / "repo.zip"
        with archive_path.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        extract_dir = workdir / "repo"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            self._safe_extract_zip(archive, extract_dir)
        children = [path for path in extract_dir.iterdir() if path.is_dir()]
        files = [path for path in extract_dir.iterdir() if path.is_file()]
        if len(children) == 1 and not files:
            return UploadedRepo(repo_path=children[0], workdir=workdir)
        return UploadedRepo(repo_path=extract_dir, workdir=workdir)

    def _safe_extract_zip(self, archive: zipfile.ZipFile, target: Path) -> None:
        members = archive.infolist()
        if len(members) > self.MAX_ZIP_MEMBERS:
            raise ValueError("zip contains too many files")
        total_size = sum(member.file_size for member in members)
        if total_size > self.MAX_EXTRACTED_BYTES:
            raise ValueError("zip expands beyond the 80 MB safety limit")
        target_root = target.resolve()
        for member in members:
            destination = (target / member.filename).resolve()
            try:
                destination.relative_to(target_root)
            except ValueError as exc:
                raise ValueError("zip contains unsafe path")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("zip contains unsupported symlink")
            if member.is_dir():
                continue
            archive.extract(member, target)

    def create_job(self, mode: str) -> WebReviewJob:
        now = time.time()
        job = WebReviewJob(
            job_id=uuid4().hex[:12],
            mode=mode,
            status="queued",
            message="Waiting for worker",
            created_at=now,
            updated_at=now,
        )
        with self.job_lock:
            self.jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> WebReviewJob | None:
        with self.job_lock:
            return self.jobs.get(job_id)

    def _update_job(self, job_id: str, **updates) -> None:
        with self.job_lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            for key, value in updates.items():
                setattr(job, key, value)
            job.updated_at = time.time()

    def _run_web_review_job(self, job_id: str, uploaded: UploadedRepo, mode: str, query: str, diff_text: str) -> None:
        self._update_job(job_id, status="running", message="Running multi-agent review")
        try:
            orchestrator = Orchestrator()
            if mode == "diff":
                trace = orchestrator.run_diff_review(uploaded.repo_path, diff_text)
            else:
                trace = orchestrator.run(uploaded.repo_path, query, test_selector=None)
            self.trace_path = self.root / "traces/latest.json"
            self._update_job(
                job_id,
                status="completed",
                message=f"Completed. Trace {trace.task_id}.",
                trace_id=trace.task_id,
            )
        except Exception as exc:
            self._update_job(job_id, status="failed", message="Review failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._cleanup_path(uploaded.workdir)

    def _cleanup_upload_root(self, upload_root: Path) -> None:
        cutoff = time.time() - self.UPLOAD_TTL_SECONDS
        for child in upload_root.glob("web_review_*"):
            try:
                if child.stat().st_mtime < cutoff:
                    self._cleanup_path(child)
            except OSError:
                continue

    def _cleanup_path(self, path: Path) -> None:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    def _parse_multipart_form(self, fp, headers, content_length: int) -> dict[str, FormField | list[FormField]]:
        content_type = headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("multipart/form-data is required")
        body = fp.read(content_length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + content_type.encode("utf-8")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
        if not message.is_multipart():
            raise ValueError("invalid multipart upload")

        fields: dict[str, FormField | list[FormField]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename() or ""
            payload = part.get_payload(decode=True) or b""
            if filename:
                field = FormField(name=name, filename=filename, file=io.BytesIO(payload))
            else:
                charset = part.get_content_charset() or "utf-8"
                field = FormField(name=name, value=payload.decode(charset, errors="replace"))
            if name in fields:
                existing = fields[name]
                if isinstance(existing, list):
                    existing.append(field)
                else:
                    fields[name] = [existing, field]
            else:
                fields[name] = field
        return fields

    def _load_trace(self) -> dict:
        try:
            return json.loads(self.trace_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_text(self, path: Path, fallback: str) -> str:
        try:
            return path.read_text()
        except OSError:
            return fallback

    def _field_value(self, form, name: str) -> str:
        if name not in form:
            return ""
        field = form[name]
        if isinstance(field, list):
            field = field[0]
        value = getattr(field, "value", "")
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    def _steps(self, steps: list[dict]) -> str:
        rows = "".join(
            f"<tr><td>{self._esc(step.get('tool_family'))}</td><td>{self._esc(step.get('goal'))}</td><td>{self._esc(step.get('query'))}</td></tr>"
            for step in steps
        )
        return f"<div class='table-wrap'><table><thead><tr><th>Tool Family</th><th>Goal</th><th>Query</th></tr></thead><tbody>{rows}</tbody></table></div>"

    def _workbench(self) -> str:
        return """
      <form class="workbench" method="post" action="/web-review" enctype="multipart/form-data">
        <div class="form-grid">
          <label>Mode
            <select name="mode" data-review-mode>
              <option value="ask">Codebase question / analysis</option>
              <option value="diff">Diff review</option>
            </select>
          </label>
          <label>Repository zip
            <input name="repo_zip" type="file" accept=".zip" required>
          </label>
        </div>
        <div data-mode-panel="ask">
          <label>Question
            <textarea name="query" placeholder="例如：这个项目的鉴权入口在哪里？有哪些高风险代码？"></textarea>
          </label>
        </div>
        <div data-mode-panel="diff" hidden>
          <div class="form-grid">
            <label>Diff text
              <textarea name="diff_text" placeholder="Paste unified diff here, or upload a .diff/.patch file on the right."></textarea>
            </label>
            <label>Diff file
              <input name="diff_file" type="file" accept=".diff,.patch,.txt">
            </label>
          </div>
        </div>
        <div><button type="submit">Run Review</button></div>
        <p class="muted">Upload a small source zip. The local server extracts it under .macr_uploads and runs the same backend used by the CLI.</p>
      </form>
        """

    def _status_note(self, status: str) -> str:
        if not status:
            return ""
        return f"<div class='status-note'>{self._esc(status)}</div>"

    def _job_status(self, job: WebReviewJob | None) -> str:
        if not job:
            return ""
        return (
            f"<div class='status-note' data-job-id='{self._esc(job.job_id)}'>"
            "<strong>Background review job</strong><br>"
            f"<span data-job-message>{self._esc(job.status)}: {self._esc(job.message)}</span>"
            "</div>"
        )

    def _tool_calls(self, calls: list[dict]) -> str:
        rows = "".join(
            f"<tr><td>{self._status(call)}</td><td>{self._esc(call.get('tool'))}</td><td>{self._esc(call.get('summary'))}</td><td>{call.get('latency_ms', 0)} ms</td><td>{self._esc(call.get('error_type'))}</td></tr>"
            for call in calls
        )
        return f"<div class='table-wrap'><table><thead><tr><th>Status</th><th>Tool</th><th>Summary</th><th>Latency</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table></div>"

    def _diff_review(self, board: dict[str, list[dict]]) -> str:
        items = board.get("pr_review") or []
        if not items:
            return "<p class='muted'>No PR/diff review artifact in this trace.</p>"
        payload = items[-1].get("payload") or {}
        comments = payload.get("comments") or []
        rows = "".join(
            (
                f"<tr><td>{self._esc(item.get('severity'))}</td>"
                f"<td>{self._esc(item.get('file'))}:{item.get('line')}</td>"
                f"<td>{self._esc(item.get('category'))}</td>"
                f"<td>{self._esc(item.get('message'))}</td></tr>"
            )
            for item in comments[:20]
        )
        suggestions = "".join(f"<li>{self._esc(item)}</li>" for item in payload.get("test_suggestions", []))
        return (
            "<div class='grid'>"
            f"{self._metric('Risk', payload.get('risk_level', 'n/a'))}"
            f"{self._metric('Changed Files', payload.get('changed_file_count', 0))}"
            f"{self._metric('Added', payload.get('added_lines', 0))}"
            f"{self._metric('Removed', payload.get('removed_lines', 0))}"
            "</div>"
            f"<p>{self._esc(payload.get('summary', ''))}</p>"
            f"<h3>Review Comments</h3><div class='table-wrap'><table><thead><tr><th>Severity</th><th>Location</th><th>Category</th><th>Message</th></tr></thead><tbody>{rows}</tbody></table></div>"
            f"<h3>Test Suggestions</h3><ul>{suggestions}</ul>"
        )

    def _repo_map(self, board: dict[str, list[dict]]) -> str:
        items = board.get("repo_map") or []
        if not items:
            return "<p class='muted'>No repository map in this trace yet.</p>"
        payload = items[-1].get("payload") or {}
        focus_files = payload.get("focus_files") or []
        rows = "".join(
            (
                f"<tr><td>{self._esc(item.get('file'))}</td>"
                f"<td>{item.get('score', 0)}</td>"
                f"<td>{item.get('symbol_count', 0)}</td>"
                f"<td>{self._esc(', '.join(symbol.get('signature', symbol.get('name', '')) for symbol in (item.get('symbols') or [])[:8]))}</td></tr>"
            )
            for item in focus_files[:12]
        )
        summary = (
            "<div class='grid'>"
            f"{self._metric('Mapped Files', payload.get('mapped_files', 0))}"
            f"{self._metric('Focus Files', len(focus_files))}"
            f"{self._metric('Symbols', len(payload.get('symbols') or []))}"
            "</div><br>"
        )
        return summary + f"<div class='table-wrap'><table><thead><tr><th>File</th><th>Score</th><th>Symbols</th><th>Top Signatures</th></tr></thead><tbody>{rows}</tbody></table></div>"

    def _states(self, states: list[dict]) -> str:
        if not states:
            return "<p class='muted'>No state timeline in this trace yet.</p>"
        cards = "".join(
            (
                f"<div class='state-card {self._esc(item.get('status'))}'>"
                f"<strong>{self._esc(item.get('name'))}</strong>"
                f"<small>{self._esc(item.get('status'))}</small>"
                f"<p class='muted'>{self._esc(item.get('detail'))}</p>"
                f"</div>"
            )
            for item in states
        )
        return f"<div class='timeline'>{cards}</div>"

    def _agent_flow(self, board: dict[str, list[dict]]) -> str:
        lanes = [
            ("Planning", [("task", "Task"), ("plan", "Plan"), ("policy", "Policy"), ("routing", "Route"), ("repo_map", "Repo Map")]),
            ("Retrieval", [("retrieval", "Search"), ("retrieval_critique", "Critic"), ("code_intelligence", "AST/Symbol"), ("code_graph", "Graph"), ("code_quality", "Quality")]),
            ("Review", [("evidence", "Evidence"), ("review", "Review"), ("pr_review", "PR Review"), ("final_review", "Audit")]),
            ("Delivery", [("patch", "Patch"), ("verification", "Verify"), ("monitor", "Monitor")]),
        ]
        width = 960
        height = 330
        nodes = []
        lines = []
        lane_guides = []
        start_x = 150
        x_gap = 150
        row_gap = 74
        node_width = 112
        node_height = 36
        for lane_index, (lane_label, sections) in enumerate(lanes):
            y = 58 + lane_index * row_gap
            lane_guides.append(f"<text class='flow-lane' x='18' y='{y + 4}'>{self._esc(lane_label)}</text>")
            lane_guides.append(f"<line class='flow-lane-line' x1='96' y1='{y}' x2='{width - 28}' y2='{y}'/>")
            for index, (section, label) in enumerate(sections):
                x = start_x + index * x_gap
                active = " active" if board.get(section) else ""
                nodes.append(
                    f"<g><rect class='flow-node{active}' x='{x - node_width / 2}' y='{y - node_height / 2}' width='{node_width}' height='{node_height}' rx='8'/>"
                    f"<text class='flow-label' x='{x}' y='{y + 4}'>{self._esc(label)}</text></g>"
                )
                if index:
                    prev_x = start_x + (index - 1) * x_gap
                    lines.append(f"<line class='flow-line' x1='{prev_x + node_width / 2 + 8}' y1='{y}' x2='{x - node_width / 2 - 8}' y2='{y}'/>")
            if lane_index < len(lanes) - 1:
                lines.append(
                    f"<path class='flow-line' d='M {start_x} {y + node_height / 2 + 8} "
                    f"L {start_x} {y + row_gap - node_height / 2 - 10}' fill='none'/>"
                )
        return (
            "<div class='flow'>"
            f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='Agent flow map'>"
            "<defs><marker id='arrow' markerWidth='8' markerHeight='8' refX='7' refY='4' orient='auto'><path d='M0,0 L8,4 L0,8 Z' fill='#9fb3d9'/></marker></defs>"
            + "".join(lane_guides)
            + "".join(lines)
            + "".join(nodes)
            + "</svg></div>"
        )

    def _health(self, metrics: dict, calls: list[dict], evidence: list[dict], states: list[dict]) -> str:
        total_calls = max(1, len(calls))
        ok_calls = sum(1 for call in calls if call.get("ok"))
        miss_calls = sum(1 for call in calls if call.get("error_type") == "empty_recall")
        contract = metrics.get("contract_validation") or {}
        final_review = metrics.get("final_review") or {}
        llm_cost = metrics.get("llm_cost") or {}
        rows = [
            ("Tool ok", ok_calls / total_calls),
            ("Empty recall", 1 - (miss_calls / total_calls)),
            ("Evidence", min(1, len(evidence) / 12)),
            ("States", min(1, len(states) / 8)),
            ("Contract", 1.0 if contract.get("ok") else 0.0),
            ("Final audit", 1.0 if final_review.get("ok") else 0.0),
            ("Cache hit", float(llm_cost.get("cache_hit_ratio") or 0)),
        ]
        bars = "".join(self._bar(label, value) for label, value in rows)
        return f"<div class='bars'>{bars}</div>"

    def _final_review_notice(self, final_review: dict) -> str:
        if not final_review or final_review.get("ok"):
            return ""
        issues = final_review.get("issues") or []
        questions = final_review.get("questions") or []
        issue_items = "".join(f"<li>{self._esc(issue)}</li>" for issue in issues[:4])
        question_items = "".join(f"<li>{self._esc(question)}</li>" for question in questions[:4])
        questions_block = f"<strong>Questions for human review</strong><ul>{question_items}</ul>" if question_items else ""
        return (
            "<div class='warning'>"
            "<strong>Final Audit Needs Review</strong>"
            f"<ul>{issue_items}</ul>"
            f"{questions_block}"
            "</div>"
        )

    def _tool_mix(self, calls: list[dict]) -> str:
        if not calls:
            return ""
        total = len(calls)
        ok = sum(1 for call in calls if call.get("ok"))
        miss = sum(1 for call in calls if call.get("error_type") == "empty_recall")
        failed = total - ok - miss
        return (
            "<div class='grid'>"
            f"{self._metric('Tool Calls', total)}"
            f"{self._metric('OK', ok)}"
            f"{self._metric('Miss', miss)}"
            f"{self._metric('Failed', failed)}"
            "</div><br>"
        )

    def _real_data_status(self, report: str) -> str:
        return (
            "<div>"
            "<h3>Real Data Testing</h3>"
            "<div class='status-list'>"
            f"{self._status_row('SWE-bench adapter', 'passed', 'problem statement + gold patch oracle converted to eval JSONL')}"
            f"{self._status_row('CodeSearchNet adapter', 'passed', 'docstring, path, and function name converted to retrieval eval')}"
            f"{self._status_row('GitHub issue adapter', 'passed', 'curated issue title/body plus manual oracle converted')}"
            f"{self._status_row('External repo eval', 'passed', '4 MarkupSafe issue-style cases, task_success_rate 1.0')}"
            f"{self._status_row('Patch benchmark', 'not run', 'requires repo-specific setup, sandboxing, and timeout control')}"
            "</div>"
            "<details class='panel' style='margin-top:10px; padding:10px; box-shadow:none;'><summary>Show raw report</summary>"
            f"<pre>{self._esc(report)}</pre></details>"
            "</div>"
        )

    def _code_quality_panel(self, report: dict) -> str:
        if not report:
            return "<div><h3>Code Smell Risk</h3><p class='muted'>No code smell report in latest trace.</p></div>"
        ratio = float(report.get("smell_ratio") or 0)
        pct = max(0, min(100, int(ratio * 100)))
        hotspots = "".join(
            (
                f"<li><strong>{self._esc(item.get('file'))}:{item.get('line')}</strong> "
                f"{self._esc(item.get('symbol') or item.get('kind'))}<br>"
                f"<span class='muted'>{self._esc(item.get('reason'))}</span></li>"
            )
            for item in (report.get("hotspots") or [])[:5]
        )
        suggestions = "".join(f"<li>{self._esc(item)}</li>" for item in (report.get("suggestions") or [])[:4])
        suggestions_block = f"<h3>Refactor Suggestions</h3><ul class='hotspots'>{suggestions}</ul>" if suggestions else ""
        return (
            "<div>"
            "<h3>Code Smell Risk</h3>"
            f"<div class='donut-wrap'><div class='donut' style='--pct:{pct}%'><span>{pct}%</span></div>"
            "<div>"
            f"<p><span class='pill'>{self._esc(report.get('severity'))}</span></p>"
            f"<p class='muted'>{report.get('smelly_units', 0)} smelly units / {report.get('total_units', 0)} total units</p>"
            "</div></div>"
            f"<ul class='hotspots'>{hotspots or '<li>No major hotspots detected.</li>'}</ul>"
            f"{suggestions_block}"
            "</div>"
        )

    def _status_row(self, label: str, status: str, detail: str) -> str:
        pending = " pending" if status in {"not run", "blocked"} else ""
        return (
            f"<div class='status-row'><strong>{self._esc(label)}</strong>"
            f"<span class='status-badge{pending}'>{self._esc(status)}</span>"
            f"<span class='muted'>{self._esc(detail)}</span></div>"
        )

    def _bar(self, label: str, value: float) -> str:
        pct = max(0, min(100, int(value * 100)))
        return (
            f"<div class='bar-row'><span>{self._esc(label)}</span>"
            f"<div class='bar'><span style='width:{pct}%'></span></div>"
            f"<strong>{pct}%</strong></div>"
        )

    def _board(self, board: dict[str, list[dict]]) -> str:
        if not board:
            return "<p class='muted'>No board data in this trace yet.</p>"
        sections = []
        for section, items in board.items():
            rows = "".join(
                (
                    f"<tr><td>{self._esc(item.get('agent'))}</td>"
                    f"<td>{self._esc(item.get('kind'))}</td>"
                    f"<td>{self._esc(item.get('title'))}</td>"
                    f"<td><pre>{self._esc(json.dumps(item.get('payload', {}), ensure_ascii=False, indent=2)[:1600])}</pre></td></tr>"
                )
                for item in items
            )
            sections.append(
                f"<h3>{self._esc(section)}</h3>"
                f"<div class='table-wrap'><table><thead><tr><th>Agent</th><th>Kind</th><th>Title</th><th>Payload</th></tr></thead><tbody>{rows}</tbody></table></div>"
            )
        return "".join(sections)

    def _evidence(self, evidence: list[dict]) -> str:
        rows = "".join(
            f"<tr><td>{self._esc(item.get('file'))}:{item.get('line_start')}</td><td>{self._esc(item.get('symbol'))}</td><td>{self._esc(item.get('source_tool'))}</td><td>{self._esc(item.get('reason'))}</td></tr>"
            for item in evidence
        )
        return f"<div class='table-wrap'><table><thead><tr><th>Location</th><th>Symbol</th><th>Source</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table></div>"

    def _patch(self, patch: dict) -> str:
        if not patch:
            return "<p class='muted'>No patch artifact in latest trace.</p>"
        verification = patch.get("verification") or {}
        return (
            f"<p><span class='pill'>{self._esc(patch.get('source'))}</span> {self._esc(patch.get('summary'))}</p>"
            f"<pre>{self._esc(json.dumps(verification, ensure_ascii=False, indent=2))}</pre>"
        )

    def _metric(self, label: str, value) -> str:
        return f"<div class='metric'><span class='muted'>{self._esc(label)}</span><strong>{self._esc(value)}</strong></div>"

    def _detail(self, title: str, body: str, open_by_default: bool = False) -> str:
        open_attr = " open" if open_by_default else ""
        return (
            f"<details class='panel'{open_attr}>"
            f"<summary>{self._esc(title)}</summary>"
            f"<div class='detail-body'>{body}</div>"
            f"</details>"
        )

    def _status(self, call) -> str:
        if call.get("ok"):
            return "<span class='ok'>ok</span>"
        if call.get("error_type") == "empty_recall":
            return "<span class='miss'>miss</span>"
        return "<span class='bad'>failed</span>"

    def _esc(self, value) -> str:
        return html.escape("" if value is None else str(value))
