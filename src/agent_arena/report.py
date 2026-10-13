"""Self-contained HTML reports for completed Agent Arena runs."""

from __future__ import annotations

import html
import json
import os
import re
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from agent_arena.runner import ArenaError

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_DIFF_BYTES = 1_000_000
_MAX_LOG_BYTES = 200_000
_STYLE = """
:root {
  color-scheme: dark;
  --bg: #0b1020;
  --line: #27334b;
  --text: #edf2ff;
  --muted: #9ba9c4;
  --green: #54e0a0;
  --red: #ff7d8a;
  --yellow: #ffd166;
  --blue: #8ab4ff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: radial-gradient(ellipse at 15% 0%, #182749 0, transparent 45%), var(--bg);
  color: var(--text);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { width: min(1500px, 100%); margin: 0 auto; padding: 42px 28px 64px; }
.topline {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  color: var(--muted);
  font-size: 13px;
}
.brand { color: var(--text); font-weight: 800; letter-spacing: .04em; }
h1 { margin: 30px 0 12px; font-size: clamp(30px, 5vw, 54px); line-height: 1.05; }
h2, h3, h4, p { margin-top: 0; }
.task {
  max-width: 1000px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: #c7d2e8;
  background: #0e1729;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 18px 20px;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
  color: var(--muted);
  font-size: 13px;
  margin: 18px 0 30px;
}
.meta strong { color: var(--text); font-weight: 600; }
.agents {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 580px), 1fr));
  gap: 18px;
  align-items: start;
}
.agent-card {
  background: linear-gradient(180deg, rgba(18, 26, 45, .98), rgba(14, 22, 40, .98));
  border: 1px solid var(--line);
  border-radius: 18px;
  overflow: hidden;
  box-shadow: 0 16px 50px #0003;
}
.agent-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 22px 22px 16px;
}
.agent-head h2 { font-size: 23px; margin: 0 0 3px; }
.eyebrow {
  color: var(--blue);
  letter-spacing: .14em;
  font-size: 10px;
  font-weight: 800;
  margin: 0 0 6px;
}
.muted, .empty { color: var(--muted); }
.agent-head .muted { margin: 3px 0 0; font-size: 12px; }
.status {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
  border-radius: 999px;
  padding: 5px 10px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid currentColor;
}
.status-ok { color: var(--green); }
.status-bad { color: var(--red); }
.status-warn { color: var(--yellow); }
.status-muted { color: var(--muted); }
.metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border-block: 1px solid var(--line);
}
.metrics div { padding: 13px 18px; border-right: 1px solid var(--line); }
.metrics div:last-child { border: 0; }
.metrics strong, .metrics span { display: block; }
.metrics strong { font-size: 18px; }
.metrics span { color: var(--muted); font-size: 11px; }
.report-section { padding: 18px 20px 0; }
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.section-head h3 { margin: 0; font-size: 14px; }
.file-link, .log-actions a { color: var(--blue); text-decoration: none; font-size: 12px; }
.file-link:hover, .log-actions a:hover { text-decoration: underline; }
pre {
  margin: 0;
  white-space: pre;
  overflow: auto;
  max-height: 440px;
  background: #080d18;
  border: 1px solid #1e2940;
  border-radius: 10px;
  padding: 14px;
  color: #bdc9df;
  font: 12px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace;
}
pre.diff { max-height: 600px; }
pre span { display: block; min-height: 1.6em; }
.diff-add { color: #8be9b6; background: #12312266; }
.diff-remove { color: #ff9ba5; background: #3d1c2766; }
.diff-hunk { color: #a7c2ff; }
.diff-file { color: #e7d28a; font-weight: 700; }
.checks { list-style: none; padding: 0; margin: 0; }
.checks li {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 0;
  border-top: 1px solid #202b40;
}
.check-result { margin-left: auto; color: var(--muted); font-size: 12px; }
.checks a { color: var(--blue); text-decoration: none; font-size: 11px; }
.checks a:hover { text-decoration: underline; }
.check-dot { width: 8px; height: 8px; border-radius: 50%; }
.dot-ok { background: var(--green); }
.dot-bad { background: var(--red); }
.logs { border-top: 1px solid var(--line); margin-top: 20px; padding: 14px 20px 20px; }
.logs summary { cursor: pointer; font-weight: 700; }
.logs h4 {
  margin: 16px 0 7px;
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .1em;
}
.log-actions { display: flex; gap: 18px; margin-top: 12px; }
footer { margin-top: 28px; color: var(--muted); font-size: 12px; }
@media (max-width: 600px) {
  main { padding: 26px 14px 44px; }
  .topline { align-items: flex-start; flex-direction: column; }
  .agent-head { padding: 18px 16px 14px; }
  .report-section { padding-inline: 14px; }
  .metrics div { padding: 11px 10px; }
  .logs { padding-inline: 14px; }
}
"""


def _safe_name(value: str) -> str:
    name = _SAFE_NAME.sub("-", value).strip("-.")
    if not name or name in {".", ".."}:
        raise ArenaError(f"Invalid agent name in run manifest: {value!r}")
    return name


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _read_bounded(path: Path, limit: int) -> tuple[str, bool]:
    if path.is_symlink():
        return "[A symlinked artifact was not opened.]", False
    try:
        with path.open("rb") as artifact:
            raw = artifact.read(limit + 1)
    except OSError:
        return "", False
    truncated = len(raw) > limit
    content = raw[:limit].decode("utf-8", errors="replace")
    if truncated:
        content += (
            "\n\n[Output truncated in this report. Open the linked file for the full content.]"
        )
    return content, truncated


def _render_diff(diff: str) -> str:
    if not diff:
        return '<p class="empty">No patch was produced.</p>'

    rendered: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            style = "diff-file"
        elif line.startswith("+"):
            style = "diff-add"
        elif line.startswith("-"):
            style = "diff-remove"
        elif line.startswith("@@"):
            style = "diff-hunk"
        else:
            style = ""
        rendered.append(f'<span class="{style}">{_escape(line)}</span>')
    return '<pre class="diff">' + "\n".join(rendered) + "</pre>"


def _status(agent: dict[str, Any]) -> tuple[str, str]:
    if agent.get("timed_out"):
        return "Timed out", "status-warn"
    if agent.get("returncode") == 0:
        return "Finished", "status-ok"
    return "Failed", "status-bad"


def _artifact_link(
    run_dir: Path,
    agent_name: str,
    filename: str,
    label: str,
    *,
    download: bool = False,
) -> str:
    artifact = run_dir / "agents" / agent_name / filename
    try:
        artifact.resolve().relative_to(run_dir.resolve())
    except (OSError, ValueError):
        return '<span class="muted">unavailable</span>'
    if artifact.is_symlink() or not artifact.is_file():
        return '<span class="muted">unavailable</span>'
    download_attribute = " download" if download else ""
    href = f"agents/{_escape(agent_name)}/{_escape(filename)}"
    return f'<a class="file-link" href="{href}"{download_attribute}>{_escape(label)}</a>'


def _check_summary(checks: Any) -> tuple[str, str]:
    if not isinstance(checks, list) or not checks:
        return "No checks configured", "status-muted"
    passed = sum(
        check.get("returncode") == 0 and not check.get("timed_out")
        for check in checks
        if isinstance(check, dict)
    )
    total = len(checks)
    if passed == total:
        return f"{passed}/{total} checks passed", "status-ok"
    return f"{passed}/{total} checks passed", "status-warn"


def _render_checks(checks: Any, run_dir: Path, agent_name: str) -> str:
    if not isinstance(checks, list) or not checks:
        return '<p class="empty">No checks configured.</p>'
    rows: list[str] = []
    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            continue
        ok = check.get("returncode") == 0 and not check.get("timed_out")
        status = "passed" if ok else ("timed out" if check.get("timed_out") else "failed")
        elapsed = check.get("duration_seconds", 0)
        stdout_link = _artifact_link(run_dir, agent_name, f"check-{index}.stdout.log", "stdout")
        stderr_link = _artifact_link(run_dir, agent_name, f"check-{index}.stderr.log", "stderr")
        rows.append(
            "<li>"
            f'<span class="check-dot {"dot-ok" if ok else "dot-bad"}"></span>'
            f"<strong>{_escape(check.get('command', 'check'))}</strong>"
            f'<span class="check-result">{_escape(status)} · {_escape(elapsed)}s</span>'
            f"{stdout_link}{stderr_link}"
            "</li>"
        )
    return '<ul class="checks">' + "".join(rows) + "</ul>"


def _render_agent(run_dir: Path, agent: dict[str, Any], index: int) -> str:
    name_value = str(agent.get("name", f"agent-{index + 1}"))
    safe = _safe_name(name_value)
    agent_dir = run_dir / "agents" / safe
    try:
        agent_dir.resolve().relative_to(run_dir.resolve())
    except (OSError, ValueError) as error:
        raise ArenaError(f"Agent artifacts escape the run directory: {agent_dir}") from error
    if agent_dir.is_symlink():
        raise ArenaError(f"Agent artifact directory is a symlink: {agent_dir}")
    patch_path = agent_dir / "patch.diff"
    stdout_path = agent_dir / "stdout.log"
    stderr_path = agent_dir / "stderr.log"
    diff, diff_truncated = _read_bounded(patch_path, _MAX_DIFF_BYTES)
    stdout, stdout_truncated = _read_bounded(stdout_path, _MAX_LOG_BYTES)
    stderr, stderr_truncated = _read_bounded(stderr_path, _MAX_LOG_BYTES)

    status_label, status_class = _status(agent)
    check_label, check_class = _check_summary(agent.get("checks"))
    elapsed = agent.get("duration_seconds", 0)
    changed = agent.get("changed_files", 0)
    returncode = agent.get("returncode", "unknown")
    truncation_note = (
        '<p class="muted">One or more log previews were truncated.</p>'
        if stdout_truncated or stderr_truncated
        else ""
    )

    patch_link = _artifact_link(run_dir, safe, "patch.diff", "Download patch", download=True)
    if diff_truncated:
        patch_link += ' <span class="muted">(preview truncated)</span>'

    check_rows = _render_checks(agent.get("checks"), run_dir, safe)
    stdout_link = _artifact_link(run_dir, safe, "stdout.log", "Full stdout log")
    stderr_link = _artifact_link(run_dir, safe, "stderr.log", "Full stderr log")
    return f"""
      <article class="agent-card">
        <header class="agent-head">
          <div>
            <p class="eyebrow">AGENT {index + 1}</p>
            <h2>{_escape(name_value)}</h2>
            <p class="muted">Command: {_escape(agent.get("command", "unknown"))}</p>
          </div>
          <span class="status {status_class}">{_escape(status_label)}</span>
        </header>
        <div class="metrics">
          <div><strong>{_escape(changed)}</strong><span>changed files</span></div>
          <div><strong>{_escape(elapsed)}s</strong><span>agent runtime</span></div>
          <div><strong>{_escape(returncode)}</strong><span>exit code</span></div>
        </div>
        <section class="report-section">
          <div class="section-head"><h3>Patch</h3>{patch_link}</div>
          {_render_diff(diff)}
        </section>
        <section class="report-section">
          <div class="section-head">
            <h3>Checks</h3>
            <span class="status {check_class}">{_escape(check_label)}</span>
          </div>
          {check_rows}
        </section>
        <details class="logs">
          <summary>Agent output</summary>
          <div class="log-actions">
            {stdout_link}
            {stderr_link}
          </div>
          <h4>stdout</h4>
          <pre>{_escape(stdout) if stdout else "(empty)"}</pre>
          <h4>stderr</h4>
          <pre>{_escape(stderr) if stderr else "(empty)"}</pre>
          {truncation_note}
        </details>
      </article>
    """


def _atomic_write(path: Path, contents: str) -> None:
    temporary_candidate = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    created_temporary_path: Path | None = None
    try:
        with temporary_candidate.open("x", encoding="utf-8", newline="\n") as destination:
            created_temporary_path = temporary_candidate
            destination.write(contents)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_candidate, path)
        created_temporary_path = None
    except OSError as error:
        raise ArenaError(f"Could not write report to {path}: {error}") from error
    finally:
        if created_temporary_path is not None:
            with suppress(OSError):
                created_temporary_path.unlink(missing_ok=True)


def create_report(run_dir: Path) -> Path:
    """Render a local, offline HTML report from a run's manifest and artifacts."""
    run_dir = run_dir.expanduser()
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ArenaError(f"Run directory does not exist or is a symlink: {run_dir}")
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "run.json"
    if manifest_path.is_symlink():
        raise ArenaError(f"Run manifest is a symlink: {manifest_path}")
    try:
        with manifest_path.open("rb") as source:
            raw_manifest = source.read(16_777_217)
        if len(raw_manifest) > 16_777_216:
            raise ArenaError("Run manifest is too large to render safely.")
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except FileNotFoundError as error:
        raise ArenaError(f"Run manifest not found: {manifest_path}") from error
    except UnicodeDecodeError as error:
        raise ArenaError("Run manifest is not valid UTF-8.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ArenaError(f"Could not read run manifest: {error}") from error

    if not isinstance(manifest, dict):
        raise ArenaError("Run manifest must be a JSON object.")
    agents = manifest.get("agents")
    if not isinstance(agents, list):
        raise ArenaError("Run manifest does not contain an agents list.")

    agent_cards = "".join(
        _render_agent(run_dir, agent, index)
        for index, agent in enumerate(agents)
        if isinstance(agent, dict)
    )
    if not agent_cards:
        agent_cards = '<p class="empty">This run has no agent results.</p>'

    title = "Agent Arena — run report"
    task = _escape(manifest.get("task", ""))
    run_id = _escape(manifest.get("run_id", run_dir.name))
    repository = _escape(manifest.get("repository", ""))
    base_sha = _escape(str(manifest.get("base_sha", ""))[:12])
    created_at = _escape(manifest.get("created_at", ""))
    html_document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{_escape(title)}</title>
  <style>{_STYLE}</style>
</head>
<body>
  <main>
    <div class="topline"><span class="brand">AGENT ARENA</span><span>Local run report</span></div>
    <h1>One task. Multiple outcomes.</h1>
    <p class="task">{task}</p>
    <div class="meta">
      <span>Run <strong>{run_id}</strong></span>
      <span>Base <strong>{base_sha}</strong></span>
      <span>Repository <strong>{repository}</strong></span>
      <span>Created <strong>{created_at}</strong></span>
    </div>
    <section class="agents">{agent_cards}</section>
    <footer>
      Generated locally by Agent Arena. Review patches and logs before sharing this report.
    </footer>
  </main>
</body>
</html>
"""
    report_path = run_dir / "report.html"
    if report_path.is_symlink():
        raise ArenaError(f"Refusing to overwrite a symlink: {report_path}")
    _atomic_write(report_path, html_document)
    return report_path
