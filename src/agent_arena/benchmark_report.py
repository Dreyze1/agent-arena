"""Render private benchmark manifests as self-contained offline scorecards."""

from __future__ import annotations

import html
import json
import math
import os
import re
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from agent_arena.benchmark import _aggregate_results
from agent_arena.errors import ArenaError

_MAX_MANIFEST_BYTES = 16_777_216
_BENCHMARK_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_VALID_STATUSES = {"running", "complete", "failed", "cancelled"}

_STYLE = """
:root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  background: #101216; color: #edf0f4; }
* { box-sizing: border-box; }
body { margin: 0; padding: 36px 20px 64px; }
main { max-width: 1440px; margin: 0 auto; }
.topline { display: flex; gap: 12px; color: #a4acb8; font-size: .82rem;
  letter-spacing: .08em; text-transform: uppercase; }
.brand { color: #86b7ff; font-weight: 800; }
h1 { margin: 16px 0 8px; font-size: clamp(2rem, 4vw, 3.4rem); line-height: 1.05; }
.suite { margin: 0; color: #b7c0ce; font-size: 1.1rem; overflow-wrap: anywhere; }
.meta { display: flex; flex-wrap: wrap; gap: 10px 24px; margin: 22px 0 34px;
  color: #9da7b5; font-size: .9rem; }
.meta strong { color: #edf0f4; font-weight: 600; }
section { margin-top: 32px; }
h2 { margin: 0 0 14px; font-size: 1.25rem; }
.scroll { overflow-x: auto; border: 1px solid #303641; border-radius: 12px; }
table { width: 100%; border-collapse: collapse; text-align: left; }
.aggregate { min-width: 1180px; table-layout: fixed; }
caption { padding: 12px 14px; color: #aab3c0; text-align: left; }
th, td { border-bottom: 1px solid #303641; padding: 12px 14px; vertical-align: top; }
th { background: #191d24; color: #bac4d2; font-size: .78rem; letter-spacing: .04em;
  text-transform: uppercase; white-space: nowrap; }
.aggregate th { white-space: normal; overflow-wrap: anywhere; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
tbody th { color: #edf0f4; font-size: .9rem; text-transform: none; overflow-wrap: anywhere; }
.matrix { min-width: 820px; }
.cell { display: grid; gap: 5px; min-width: 170px; color: #c4ccd7; font-size: .82rem; }
.cell span { display: flex; justify-content: space-between; gap: 12px; }
.cell strong { color: #edf0f4; font-weight: 600; }
.empty { padding: 18px; color: #aab3c0; }
.status { display: inline-block; border: 1px solid #465264; border-radius: 999px;
  padding: 3px 9px; color: #c8d8f0; font-size: .78rem; }
footer { margin-top: 34px; color: #8e98a6; font-size: .85rem; }
a { color: #9cc4ff; }
:focus-visible { outline: 3px solid #86b7ff; outline-offset: 3px; }
@media (max-width: 700px) { body { padding: 24px 12px 40px; } .meta { gap: 8px 14px; } }
"""


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser()
    if manifest_path.name != "benchmark.json":
        raise ArenaError("Benchmark report input must be a benchmark.json manifest.")
    if manifest_path.is_symlink():
        raise ArenaError(f"Refusing to read a symlinked benchmark manifest: {manifest_path}")
    if manifest_path.parent.is_symlink() or not manifest_path.parent.is_dir():
        raise ArenaError(
            f"Benchmark manifest directory does not exist or is a symlink: {manifest_path.parent}"
        )

    try:
        with manifest_path.open("rb") as source:
            raw_manifest = source.read(_MAX_MANIFEST_BYTES + 1)
    except FileNotFoundError as error:
        raise ArenaError(f"Benchmark manifest not found: {manifest_path}") from error
    except OSError as error:
        raise ArenaError(f"Could not read benchmark manifest: {error}") from error

    if len(raw_manifest) > _MAX_MANIFEST_BYTES:
        raise ArenaError(f"Benchmark manifest exceeds the {_MAX_MANIFEST_BYTES:,}-byte limit.")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ArenaError("Benchmark manifest is not valid UTF-8.") from error
    except json.JSONDecodeError as error:
        raise ArenaError(f"Benchmark manifest is not valid JSON: {error}") from error
    except RecursionError as error:
        raise ArenaError("Benchmark manifest is too deeply nested.") from error
    if not isinstance(manifest, dict):
        raise ArenaError("Benchmark manifest must be a JSON object.")
    return manifest


def _non_negative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ArenaError(f"Benchmark manifest has an invalid {field} value.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise ArenaError(f"Benchmark manifest has an invalid {field} value.") from error
    if not math.isfinite(number) or number < 0:
        raise ArenaError(f"Benchmark manifest has an invalid {field} value.")
    return number


def _validate_manifest(
    manifest: dict[str, Any],
) -> tuple[list[str], list[str], int, list[dict[str, Any]]]:
    if manifest.get("schema_version") != 1:
        raise ArenaError("Benchmark manifest has an unsupported schema version.")
    benchmark_id = manifest.get("benchmark_id")
    if not isinstance(benchmark_id, str) or not _BENCHMARK_ID.fullmatch(benchmark_id):
        raise ArenaError("Benchmark manifest has an invalid benchmark ID.")
    status = manifest.get("status")
    if not isinstance(status, str) or status not in _VALID_STATUSES:
        raise ArenaError("Benchmark manifest has an invalid status.")

    suite_name = manifest.get("suite_name")
    if not isinstance(suite_name, str) or not suite_name.strip() or len(suite_name) > 120:
        raise ArenaError("Benchmark manifest has an invalid suite name.")

    agent_names = manifest.get("agent_names")
    if (
        not isinstance(agent_names, list)
        or not agent_names
        or len(agent_names) > 16
        or any(not isinstance(name, str) or not name or len(name) > 120 for name in agent_names)
        or len(set(agent_names)) != len(agent_names)
    ):
        raise ArenaError("Benchmark manifest has an invalid agent list.")

    suite_snapshot = manifest.get("suite_snapshot")
    if not isinstance(suite_snapshot, dict):
        raise ArenaError("Benchmark manifest has no valid suite snapshot.")
    repetitions = suite_snapshot.get("repetitions")
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or not 1 <= repetitions <= 5
    ):
        raise ArenaError("Benchmark manifest has an invalid repetition count.")

    tasks = suite_snapshot.get("tasks")
    if not isinstance(tasks, list) or not tasks or len(tasks) > 100:
        raise ArenaError("Benchmark manifest has an invalid task list.")
    task_ids: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ArenaError("Benchmark manifest contains an invalid task entry.")
        task_id = task.get("id")
        if (
            not isinstance(task_id, str)
            or not _TASK_ID.fullmatch(task_id)
            or ".." in task_id
            or task_id in task_ids
        ):
            raise ArenaError("Benchmark manifest contains an invalid or duplicate task ID.")
        task_ids.append(task_id)

    runs = manifest.get("runs")
    if not isinstance(runs, list) or len(runs) > len(tasks) * repetitions:
        raise ArenaError("Benchmark manifest has an invalid run list.")
    normalized_runs: list[dict[str, Any]] = []
    seen_runs: set[tuple[str, int]] = set()
    for run in runs:
        if not isinstance(run, dict):
            raise ArenaError("Benchmark manifest contains an invalid run entry.")
        task_id = run.get("task_id")
        repetition = run.get("repetition")
        if (
            task_id not in task_ids
            or not isinstance(repetition, int)
            or isinstance(repetition, bool)
            or not 1 <= repetition <= repetitions
            or (task_id, repetition) in seen_runs
        ):
            raise ArenaError("Benchmark manifest contains an invalid or duplicate task attempt.")
        seen_runs.add((task_id, repetition))

        raw_agents = run.get("agents", [])
        if not isinstance(raw_agents, list) or len(raw_agents) > len(agent_names):
            raise ArenaError("Benchmark manifest contains an invalid agent result list.")
        normalized_agents: list[dict[str, Any]] = []
        seen_agents: set[str] = set()
        for agent in raw_agents:
            if not isinstance(agent, dict):
                raise ArenaError("Benchmark manifest contains an invalid agent result.")
            name = agent.get("name")
            if not isinstance(name, str) or name not in agent_names or name in seen_agents:
                raise ArenaError(
                    "Benchmark manifest contains an unknown or duplicate agent result."
                )
            seen_agents.add(name)

            returncode = agent.get("returncode")
            if returncode is not None and (
                not isinstance(returncode, int) or isinstance(returncode, bool)
            ):
                raise ArenaError("Benchmark manifest contains an invalid agent exit code.")
            timed_out = agent.get("timed_out", False)
            if not isinstance(timed_out, bool):
                raise ArenaError("Benchmark manifest contains an invalid timeout result.")
            duration = _non_negative_number(agent.get("duration_seconds", 0), "duration")

            raw_checks = agent.get("checks", [])
            if not isinstance(raw_checks, list):
                raise ArenaError("Benchmark manifest contains an invalid check result list.")
            checks: list[dict[str, Any]] = []
            for check in raw_checks:
                if not isinstance(check, dict):
                    raise ArenaError("Benchmark manifest contains an invalid check result.")
                check_returncode = check.get("returncode")
                if check_returncode is not None and (
                    not isinstance(check_returncode, int) or isinstance(check_returncode, bool)
                ):
                    raise ArenaError("Benchmark manifest contains an invalid check exit code.")
                check_timed_out = check.get("timed_out", False)
                if not isinstance(check_timed_out, bool):
                    raise ArenaError("Benchmark manifest contains an invalid check timeout result.")
                checks.append({"returncode": check_returncode, "timed_out": check_timed_out})

            normalized_agents.append(
                {
                    "name": name,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "duration_seconds": duration,
                    "checks": checks,
                }
            )
        normalized_runs.append(
            {
                "task_id": task_id,
                "repetition": repetition,
                "agents": normalized_agents,
            }
        )

    return task_ids, agent_names, repetitions, normalized_runs


def _format_duration(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds:.1f}s"


def _summary_table(summaries: list[dict[str, Any]], expected_attempts: int) -> str:
    rows: list[str] = []
    for summary in summaries:
        attempts = summary["attempts"]
        pass_rate = summary["verified_pass_rate"]
        coverage = summary["verification_coverage"]
        rows.append(
            "<tr>"
            f'<th scope="row">{_escape(summary["agent"])}</th>'
            f"<td>{attempts}/{expected_attempts}</td>"
            f"<td>{summary['agent_successes']}</td>"
            f"<td>{summary['verified_passes']}/{summary['verified_attempts']}</td>"
            f"<td>{summary['unverified_successes']}</td>"
            f"<td>{summary['agent_failures']}</td>"
            f"<td>{summary['check_failure_attempts']}</td>"
            f"<td>{summary['timeouts']}</td>"
            f"<td>{_format_duration(summary['mean_duration_seconds'] if attempts else None)}</td>"
            f"<td>{coverage:.0%}</td>"
            f"<td>{'n/a' if pass_rate is None else f'{pass_rate:.0%}'}</td>"
            f"<td>{expected_attempts - attempts}</td>"
            "</tr>"
        )
    return "".join(rows)


def _matrix_cell(
    task_id: str,
    agent_name: str,
    repetitions: int,
    outcomes: dict[tuple[str, int, str], dict[str, Any]],
) -> str:
    verified_passes = 0
    verified_attempts = 0
    unverified_successes = 0
    agent_failures = 0
    check_failures = 0
    timeouts = 0
    incomplete = 0
    observed = 0
    duration_total = 0.0

    for repetition in range(1, repetitions + 1):
        outcome = outcomes.get((task_id, repetition, agent_name))
        if outcome is None:
            incomplete += 1
            continue
        observed += 1
        duration_total += outcome["duration_seconds"]
        checks = outcome["checks"]
        timed_out = outcome["timed_out"]
        succeeded = outcome["returncode"] == 0 and not timed_out
        if timed_out:
            timeouts += 1
        elif not succeeded:
            agent_failures += 1
        if checks:
            verified_attempts += 1
            checks_succeeded = all(
                check["returncode"] == 0 and not check["timed_out"] for check in checks
            )
            if not checks_succeeded:
                check_failures += 1
            if succeeded and checks_succeeded:
                verified_passes += 1
        elif succeeded:
            unverified_successes += 1

    mean_duration = duration_total / observed if observed else None
    items = (
        ("Verified passes", f"{verified_passes}/{verified_attempts}"),
        ("Unverified successes", str(unverified_successes)),
        ("Agent failures", str(agent_failures)),
        ("Check failure attempts", str(check_failures)),
        ("Timeouts", str(timeouts)),
        ("Incomplete", str(incomplete)),
        ("Observed", f"{observed}/{repetitions}"),
        ("Mean agent time", _format_duration(mean_duration)),
    )
    return (
        '<div class="cell">'
        + "".join(
            f"<span><strong>{_escape(label)}</strong><span>{_escape(value)}</span></span>"
            for label, value in items
        )
        + "</div>"
    )


def _matrix_table(
    task_ids: list[str],
    agent_names: list[str],
    repetitions: int,
    runs: list[dict[str, Any]],
) -> str:
    outcomes = {
        (run["task_id"], run["repetition"], agent["name"]): agent
        for run in runs
        for agent in run["agents"]
    }
    headings = "".join(f'<th scope="col">{_escape(name)}</th>' for name in agent_names)
    rows = []
    for task_id in task_ids:
        cells = "".join(
            f"<td>{_matrix_cell(task_id, name, repetitions, outcomes)}</td>" for name in agent_names
        )
        rows.append(f'<tr><th scope="row">{_escape(task_id)}</th>{cells}</tr>')
    return (
        '<div class="scroll" tabindex="0" role="region" aria-label="Task by agent results">'
        '<table class="matrix"><caption>Attempt outcomes per task and agent</caption>'
        f'<thead><tr><th scope="col">Task</th>{headings}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render(manifest: dict[str, Any]) -> str:
    task_ids, agent_names, repetitions, runs = _validate_manifest(manifest)
    summaries = _aggregate_results(runs, agent_names)
    expected_attempts = len(task_ids) * repetitions
    summary_rows = _summary_table(summaries, expected_attempts)
    suite_name = _escape(manifest["suite_name"])
    benchmark_id = _escape(manifest["benchmark_id"])
    status = _escape(manifest["status"])
    base_sha = manifest.get("base_sha")
    if not isinstance(base_sha, str):
        base_sha = ""
    base_sha = _escape(base_sha[:12] if base_sha else "unavailable")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str):
        created_at = "unavailable"
    else:
        created_at = created_at[:40]

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Agent Arena — benchmark scorecard</title>
  <style>{_STYLE}</style>
</head>
<body>
  <main>
    <div class="topline"><span class="brand">AGENT ARENA</span>
      <span>Local benchmark scorecard</span></div>
    <h1>Benchmark scorecard</h1>
    <p class="suite">{suite_name}</p>
    <div class="meta">
      <span>Status <strong class="status">{status}</strong></span>
      <span>Benchmark <strong>{benchmark_id}</strong></span>
      <span>Base commit <strong>{base_sha}</strong></span>
      <span>Created <strong>{_escape(created_at)}</strong></span>
      <span>Expected attempts per agent <strong>{expected_attempts}</strong></span>
    </div>
    <section aria-labelledby="aggregate-heading">
      <h2 id="aggregate-heading">Aggregate results</h2>
      <div class="scroll" tabindex="0" role="region" aria-label="Aggregate results by agent">
        <table class="aggregate">
          <caption>Metrics use recorded attempts. Missing attempts are counted as incomplete.
          </caption>
          <thead><tr>
            <th scope="col">Agent</th><th scope="col">Recorded / expected</th>
            <th scope="col">Agent successes</th><th scope="col">Verified passes</th>
            <th scope="col">Unverified successes</th><th scope="col">Agent failed attempts</th>
            <th scope="col">Check failure attempts</th><th scope="col">Timeouts</th>
            <th scope="col">Mean agent time</th><th scope="col">Check coverage</th>
            <th scope="col">Verified pass rate</th><th scope="col">Incomplete</th>
          </tr></thead>
          <tbody>{summary_rows}</tbody>
        </table>
      </div>
    </section>
    <section aria-labelledby="matrix-heading">
      <h2 id="matrix-heading">Task by agent</h2>
      {_matrix_table(task_ids, agent_names, repetitions, runs)}
    </section>
    <footer>Generated locally by Agent Arena. Prompts, repository paths, patches, logs, and check
    commands are not included.</footer>
  </main>
</body>
</html>
"""


def _atomic_write(path: Path, contents: str) -> None:
    temporary_candidate = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    created_temporary_path: Path | None = None
    try:
        with temporary_candidate.open("x", encoding="utf-8", newline="") as destination:
            created_temporary_path = temporary_candidate
            destination.write(contents)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_candidate, path)
        created_temporary_path = None
    except OSError as error:
        raise ArenaError(f"Could not write benchmark scorecard: {error}") from error
    finally:
        if created_temporary_path is not None:
            with suppress(OSError):
                created_temporary_path.unlink(missing_ok=True)


def create_benchmark_report(manifest_path: Path) -> Path:
    """Render an offline scorecard next to a benchmark JSON manifest."""
    manifest_path = Path(manifest_path).expanduser()
    manifest = _load_manifest(manifest_path)
    report_path = manifest_path.with_name("report.html")
    if report_path.is_symlink():
        raise ArenaError(f"Refusing to overwrite a symlinked benchmark scorecard: {report_path}")
    html_document = _render(manifest)
    _atomic_write(report_path, html_document)
    return report_path
