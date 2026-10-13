"""Repeatable benchmark suites and aggregate results for local agent runs."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_arena.runner import (
    ArenaError,
    _child_environment,
    _read_config,
    _validate_checks,
    file_sha256,
    repository_head,
    resolve_repository,
    run_arena,
    run_artifacts_directory,
)

_MAX_SUITE_BYTES = 4_000_000
_MAX_TASKS = 100
_MAX_REPETITIONS = 5
_MAX_AGENT_INVOCATIONS = 128
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_SUMMARY_FIELDS = (
    "agent",
    "attempts",
    "agent_successes",
    "verified_attempts",
    "verified_passes",
    "unverified_successes",
    "agent_failures",
    "check_failure_attempts",
    "timeouts",
    "mean_duration_seconds",
    "verification_coverage",
    "verified_pass_rate",
)


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    prompt: str
    checks: list[list[str]] | None


@dataclass(frozen=True)
class BenchmarkSuite:
    name: str
    repetitions: int
    checks: list[list[str]] | None
    tasks: tuple[BenchmarkTask, ...]
    sha256: str


def load_benchmark_suite(path: Path) -> BenchmarkSuite:
    """Load and validate one versioned TOML benchmark suite."""
    suite_path = path.expanduser()
    try:
        with suite_path.open("rb") as source:
            contents = source.read(_MAX_SUITE_BYTES + 1)
    except OSError as error:
        raise ArenaError(f"Could not read benchmark suite {suite_path}: {error}") from error
    if len(contents) > _MAX_SUITE_BYTES:
        raise ArenaError(f"Benchmark suite exceeds the {_MAX_SUITE_BYTES:,}-byte limit.")
    try:
        data = tomllib.loads(contents.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ArenaError(f"Invalid TOML benchmark suite {suite_path}: {error}") from error

    allowed_keys = {"version", "name", "repetitions", "checks", "tasks"}
    unknown_keys = set(data) - allowed_keys
    if unknown_keys:
        raise ArenaError("Unknown benchmark suite field(s): " + ", ".join(sorted(unknown_keys)))
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ArenaError("Benchmark suite must declare version = 1.")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ArenaError("Benchmark suite name must be 1–120 characters.")

    repetitions = data.get("repetitions", 1)
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or not 1 <= repetitions <= _MAX_REPETITIONS
    ):
        raise ArenaError(f"Suite repetitions must be between 1 and {_MAX_REPETITIONS}.")

    suite_checks = _validate_checks(data["checks"], "suite checks") if "checks" in data else None
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ArenaError("Benchmark suite must contain at least one [[tasks]] entry.")
    if len(raw_tasks) > _MAX_TASKS:
        raise ArenaError(f"Benchmark suite is limited to {_MAX_TASKS} tasks.")

    tasks: list[BenchmarkTask] = []
    seen_ids: set[str] = set()
    for index, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise ArenaError(f"Benchmark task {index} must be a TOML table.")
        unknown_task_keys = set(raw_task) - {"id", "prompt", "checks"}
        if unknown_task_keys:
            raise ArenaError(
                f"Unknown field(s) in benchmark task {index}: "
                + ", ".join(sorted(unknown_task_keys))
            )
        task_id = raw_task.get("id")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id) or ".." in task_id:
            raise ArenaError(
                f"Benchmark task {index} id must use 1–64 letters, numbers, dots, "
                "underscores, or hyphens and cannot contain '..'."
            )
        if task_id in seen_ids:
            raise ArenaError(f"Benchmark task id {task_id!r} is duplicated.")
        seen_ids.add(task_id)

        prompt = raw_task.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ArenaError(f"Benchmark task {task_id!r} needs a non-empty prompt.")
        if len(prompt.encode("utf-8")) > 100_000:
            raise ArenaError(f"Benchmark task {task_id!r} prompt exceeds 100,000 bytes.")
        task_checks = (
            _validate_checks(raw_task["checks"], f"checks for task {task_id!r}")
            if "checks" in raw_task
            else None
        )
        tasks.append(BenchmarkTask(task_id, prompt, task_checks))

    return BenchmarkSuite(
        name=name.strip(),
        repetitions=repetitions,
        checks=suite_checks,
        tasks=tuple(tasks),
        sha256=hashlib.sha256(contents).hexdigest(),
    )


def benchmark_artifacts_directory(repository: Path) -> Path:
    """Return the private per-repository benchmark history directory."""
    return run_artifacts_directory(repository).parent / "benchmarks"


def _atomic_write(path: Path, contents: str) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("x", encoding="utf-8", newline="") as destination:
            destination.write(contents)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _aggregate_results(runs: list[dict[str, Any]], agent_names: list[str]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for name in agent_names:
        attempts = 0
        agent_successes = 0
        verified_attempts = 0
        verified_passes = 0
        unverified_successes = 0
        agent_failures = 0
        check_failure_attempts = 0
        timeouts = 0
        duration_total = 0.0

        for run in runs:
            for agent in run.get("agents", []):
                if agent.get("name") != name:
                    continue
                attempts += 1
                duration_total += float(agent.get("duration_seconds", 0))
                checks = agent.get("checks", [])
                if not isinstance(checks, list):
                    checks = []
                agent_timed_out = bool(agent.get("timed_out"))
                agent_succeeded = agent.get("returncode") == 0 and not agent_timed_out
                if agent_timed_out:
                    timeouts += 1
                elif not agent_succeeded:
                    agent_failures += 1
                if agent_succeeded:
                    agent_successes += 1

                if checks:
                    verified_attempts += 1
                    checks_succeeded = all(
                        isinstance(check, dict)
                        and check.get("returncode") == 0
                        and not check.get("timed_out")
                        for check in checks
                    )
                    if not checks_succeeded:
                        check_failure_attempts += 1
                    if agent_succeeded and checks_succeeded:
                        verified_passes += 1
                elif agent_succeeded:
                    unverified_successes += 1

        summaries.append(
            {
                "agent": name,
                "attempts": attempts,
                "agent_successes": agent_successes,
                "verified_attempts": verified_attempts,
                "verified_passes": verified_passes,
                "unverified_successes": unverified_successes,
                "agent_failures": agent_failures,
                "check_failure_attempts": check_failure_attempts,
                "timeouts": timeouts,
                "mean_duration_seconds": round(duration_total / attempts, 3) if attempts else 0,
                "verification_coverage": round(verified_attempts / attempts, 4) if attempts else 0,
                "verified_pass_rate": round(verified_passes / verified_attempts, 4)
                if verified_attempts
                else None,
            }
        )
    return summaries


def _save_benchmark(manifest_path: Path, manifest: dict[str, Any]) -> None:
    manifest["summary"] = _aggregate_results(manifest["runs"], manifest["agent_names"])
    _atomic_write(
        manifest_path,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_SUMMARY_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(manifest["summary"])
    _atomic_write(manifest_path.with_name("summary.csv"), output.getvalue())


def run_benchmark(
    *,
    repository_path: Path,
    suite_path: Path,
    config_path: Path,
    selected_agents: list[str] | None,
    timeout_seconds: int,
    keep_worktrees: bool,
) -> tuple[Path, dict[str, Any]]:
    """Execute a suite with a fixed repository revision and save aggregate results."""
    if not 1 <= timeout_seconds <= 86_400:
        raise ArenaError("Timeout must be between one second and 24 hours.")

    repository = resolve_repository(repository_path)
    suite_file = suite_path.expanduser()
    if not suite_file.is_absolute():
        suite_file = repository / suite_file
    suite_file = suite_file.resolve()
    suite = load_benchmark_suite(suite_file)

    config_file = config_path.expanduser()
    if not config_file.is_absolute():
        config_file = repository / config_file
    config_file = config_file.resolve()
    config_sha256_before = file_sha256(config_file)
    agents, _ = _read_config(config_file)
    config_sha256 = file_sha256(config_file)
    if config_sha256 != config_sha256_before:
        raise ArenaError("The agent configuration changed while preparing the benchmark.")
    if selected_agents:
        missing = [name for name in selected_agents if name not in agents]
        if missing:
            raise ArenaError(f"Unknown agent profile(s): {', '.join(missing)}")
        agent_names = list(dict.fromkeys(selected_agents))
    else:
        agent_names = list(agents)
    if len(agent_names) > 16:
        raise ArenaError("A single benchmark is limited to 16 agents.")
    if not agent_names:
        raise ArenaError("Select at least one configured agent.")
    for name in agent_names:
        _child_environment(agents[name].inherit_env, {})

    invocation_count = len(suite.tasks) * suite.repetitions * len(agent_names)
    if invocation_count > _MAX_AGENT_INVOCATIONS:
        raise ArenaError(
            f"This suite schedules {invocation_count} agent invocations; "
            f"the per-benchmark limit is {_MAX_AGENT_INVOCATIONS}. "
            "Reduce tasks, repetitions, or selected agents."
        )

    base_sha = repository_head(repository)
    started = datetime.now(UTC)
    benchmark_id = started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    artifacts_root = benchmark_artifacts_directory(repository)
    try:
        artifacts_root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            artifacts_root.parent.chmod(0o700)
        benchmark_dir = artifacts_root / benchmark_id
        benchmark_dir.mkdir(exist_ok=False)
    except OSError as error:
        raise ArenaError(
            f"Could not create benchmark artifacts in {artifacts_root}: {error}"
        ) from error

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "status": "running",
        "suite_name": suite.name,
        "suite_sha256": suite.sha256,
        "agent_config_sha256": config_sha256,
        "suite_snapshot": {
            "repetitions": suite.repetitions,
            "checks": suite.checks,
            "tasks": [
                {"id": task.task_id, "prompt": task.prompt, "checks": task.checks}
                for task in suite.tasks
            ],
        },
        "repository": str(repository),
        "base_sha": base_sha,
        "agent_names": agent_names,
        "created_at": started.isoformat(),
        "completed_at": None,
        "runs": [],
        "summary": [],
    }
    manifest_path = benchmark_dir / "benchmark.json"
    _save_benchmark(manifest_path, manifest)

    try:
        for task_index, task in enumerate(suite.tasks, start=1):
            effective_checks = task.checks if task.checks is not None else suite.checks
            for repetition in range(1, suite.repetitions + 1):
                run_dir, run_base_sha, outcomes = run_arena(
                    repository_path=repository,
                    config_path=config_file,
                    task=task.prompt,
                    selected_agents=agent_names,
                    timeout_seconds=timeout_seconds,
                    keep_worktrees=keep_worktrees,
                    checks_override=effective_checks,
                    base_revision=base_sha,
                    allowed_untracked_paths=(suite_file,),
                    expected_config_sha256=config_sha256,
                )
                manifest["runs"].append(
                    {
                        "task_id": task.task_id,
                        "task_index": task_index,
                        "repetition": repetition,
                        "run_id": run_dir.name,
                        "base_sha": run_base_sha,
                        "agents": [
                            {
                                "name": outcome.name,
                                "returncode": outcome.returncode,
                                "timed_out": outcome.timed_out,
                                "duration_seconds": outcome.duration_seconds,
                                "checks": outcome.checks,
                            }
                            for outcome in outcomes
                        ],
                    }
                )
                _save_benchmark(manifest_path, manifest)

        manifest["status"] = "complete"
    except KeyboardInterrupt:
        manifest["status"] = "cancelled"
        manifest["error"] = "Interrupted by user."
        manifest["completed_at"] = datetime.now(UTC).isoformat()
        _save_benchmark(manifest_path, manifest)
        raise
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        manifest["completed_at"] = datetime.now(UTC).isoformat()
        _save_benchmark(manifest_path, manifest)
        if isinstance(error, ArenaError):
            raise ArenaError(
                f"Benchmark {benchmark_id} stopped; partial results are at {manifest_path}. {error}"
            ) from error
        raise ArenaError(
            f"Benchmark {benchmark_id} stopped; partial results are at {manifest_path}: {error}"
        ) from error

    manifest["completed_at"] = datetime.now(UTC).isoformat()
    _save_benchmark(manifest_path, manifest)
    return manifest_path, manifest
