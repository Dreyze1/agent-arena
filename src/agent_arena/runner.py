"""Local multi-agent run orchestration."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArenaError(Exception):
    """An expected user-facing error while preparing or running an arena."""


@dataclass
class AgentOutcome:
    name: str
    command: str
    returncode: int | None
    timed_out: bool
    duration_seconds: float
    changed_files: int
    patch_file: str
    checks: list[dict[str, Any]]
    workspace: str | None


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as error:
        raise ArenaError("Git was not found. Install Git and make sure it is on PATH.") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ArenaError(f"git {' '.join(args)} failed: {detail}")
    return result


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not safe or safe in {".", ".."}:
        raise ArenaError(f"Invalid agent name: {value!r}")
    return safe


def _read_config(config_path: Path) -> tuple[dict[str, list[str]], list[list[str]]]:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ArenaError(
            f"Config not found: {config_path}. Run 'arena init' or pass --config."
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ArenaError(f"Could not read config {config_path}: {error}") from error

    if data.get("version") != 1:
        raise ArenaError("Config must declare version = 1.")

    raw_agents = data.get("agents")
    if not isinstance(raw_agents, dict) or not raw_agents:
        raise ArenaError("Config must contain at least one [agents.NAME] command.")

    agents: dict[str, list[str]] = {}
    safe_names: set[str] = set()
    for name, profile in raw_agents.items():
        if not isinstance(profile, dict):
            raise ArenaError(f"Agent profile {name!r} must be a TOML table.")
        command = profile.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ArenaError(f"Agent {name!r} must define command as a non-empty string array.")
        safe = _safe_name(name)
        if safe in safe_names:
            raise ArenaError("Agent names must remain unique after filename normalization.")
        safe_names.add(safe)
        agents[name] = command

    raw_checks = data.get("checks", [])
    if not isinstance(raw_checks, list):
        raise ArenaError("The top-level checks value must be an array of command arrays.")
    checks: list[list[str]] = []
    for index, command in enumerate(raw_checks, start=1):
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ArenaError(f"Check {index} must be a non-empty string array.")
        checks.append(command)

    return agents, checks


def _expand_command(
    command: list[str],
    *,
    task: str,
    repository: Path,
    worktree: Path,
    base_sha: str,
) -> list[str]:
    values = {
        "{task}": task,
        "{repo}": str(repository),
        "{worktree}": str(worktree),
        "{base_sha}": base_sha,
    }
    expanded: list[str] = []
    for part in command:
        for key, value in values.items():
            part = part.replace(key, value)
        expanded.append(part)
    return expanded


def _has_uncommitted_changes(repository: Path, allowed_untracked: Path | None) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "-z",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ArenaError(f"Could not inspect repository status: {detail}")

    allowed_relative = None
    if allowed_untracked is not None:
        try:
            allowed_relative = allowed_untracked.relative_to(repository).as_posix()
        except ValueError:
            pass

    for record in result.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not record:
            continue
        status_code = record[:2]
        path = record[3:]
        if status_code == "??" and path == allowed_relative:
            continue
        return True
    return False


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, bool, float, str, str]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return (
            result.returncode,
            False,
            round(time.perf_counter() - started, 3),
            result.stdout,
            result.stderr,
        )
    except subprocess.TimeoutExpired as error:
        return (
            124,
            True,
            round(time.perf_counter() - started, 3),
            _as_text(error.stdout),
            _as_text(error.stderr),
        )
    except FileNotFoundError as error:
        return (
            127,
            False,
            round(time.perf_counter() - started, 3),
            "",
            f"Could not start {command[0]!r}: {error}",
        )


def _run_checks(
    checks: list[list[str]],
    *,
    task: str,
    repository: Path,
    worktree: Path,
    base_sha: str,
    timeout_seconds: int,
    environment: dict[str, str],
    agent_dir: Path,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for index, template in enumerate(checks, start=1):
        command = _expand_command(
            template,
            task=task,
            repository=repository,
            worktree=worktree,
            base_sha=base_sha,
        )
        returncode, timed_out, duration, stdout, stderr = _run_command(
            command,
            cwd=worktree,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        (agent_dir / f"check-{index}.stdout.log").write_text(stdout, encoding="utf-8")
        (agent_dir / f"check-{index}.stderr.log").write_text(stderr, encoding="utf-8")
        outcomes.append(
            {
                "command": Path(command[0]).name,
                "returncode": returncode,
                "timed_out": timed_out,
                "duration_seconds": duration,
                "stdout_file": f"check-{index}.stdout.log",
                "stderr_file": f"check-{index}.stderr.log",
            }
        )
    return outcomes


def _run_agent(
    *,
    name: str,
    command_template: list[str],
    checks: list[list[str]],
    task: str,
    repository: Path,
    worktree: Path,
    base_sha: str,
    timeout_seconds: int,
    run_id: str,
    agent_dir: Path,
    keep_worktrees: bool,
) -> AgentOutcome:
    command = _expand_command(
        command_template,
        task=task,
        repository=repository,
        worktree=worktree,
        base_sha=base_sha,
    )
    environment = os.environ.copy()
    environment.update(
        {
            "ARENA_TASK": task,
            "ARENA_REPOSITORY": str(repository),
            "ARENA_WORKTREE": str(worktree),
            "ARENA_BASE_SHA": base_sha,
            "ARENA_RUN_ID": run_id,
            "ARENA_AGENT": name,
        }
    )

    returncode, timed_out, duration, stdout, stderr = _run_command(
        command,
        cwd=worktree,
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    (agent_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (agent_dir / "stderr.log").write_text(stderr, encoding="utf-8")

    check_results = _run_checks(
        checks,
        task=task,
        repository=repository,
        worktree=worktree,
        base_sha=base_sha,
        timeout_seconds=timeout_seconds,
        environment=environment,
        agent_dir=agent_dir,
    )

    _git(worktree, "add", "--intent-to-add", "--all")
    diff = _git(worktree, "diff", "--binary", base_sha, "--").stdout
    patch_path = agent_dir / "patch.diff"
    patch_path.write_text(diff, encoding="utf-8")
    changed_files = len(
        [
            line
            for line in _git(worktree, "diff", "--name-only", base_sha, "--").stdout.splitlines()
            if line
        ]
    )

    return AgentOutcome(
        name=name,
        command=Path(command[0]).name,
        returncode=returncode,
        timed_out=timed_out,
        duration_seconds=duration,
        changed_files=changed_files,
        patch_file=str(patch_path.relative_to(agent_dir.parent.parent)),
        checks=check_results,
        workspace=str(worktree) if keep_worktrees else None,
    )


def run_arena(
    *,
    repository_path: Path,
    config_path: Path,
    task: str,
    selected_agents: list[str] | None,
    timeout_seconds: int,
    keep_worktrees: bool,
) -> tuple[Path, str, list[AgentOutcome]]:
    """Run each selected agent in its own worktree and save review artifacts."""
    if not task.strip():
        raise ArenaError("Task text cannot be empty.")
    if timeout_seconds < 1:
        raise ArenaError("Timeout must be at least one second.")

    repository_input = repository_path.expanduser().resolve()
    if not repository_input.exists():
        raise ArenaError(f"Repository path does not exist: {repository_input}")
    repository = Path(_git(repository_input, "rev-parse", "--show-toplevel").stdout.strip()).resolve()

    config = config_path.expanduser()
    if not config.is_absolute():
        config = repository / config
    config = config.resolve()
    allowed_untracked_config = config if config.is_relative_to(repository) else None
    if _has_uncommitted_changes(repository, allowed_untracked_config):
        raise ArenaError(
            "The repository has uncommitted changes. Commit or stash them first; "
            "each run starts from HEAD. An untracked config file is allowed."
        )
    base_sha = _git(repository, "rev-parse", "HEAD").stdout.strip()
    agents, checks = _read_config(config)

    if selected_agents:
        missing = [name for name in selected_agents if name not in agents]
        if missing:
            raise ArenaError(f"Unknown agent profile(s): {', '.join(missing)}")
        names = list(dict.fromkeys(selected_agents))
    else:
        names = list(agents)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    repo_key = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:12]
    temp_root = Path(tempfile.gettempdir()) / "agent-arena" / repo_key
    run_dir = temp_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    workspace_root = temp_root / "worktrees" / run_id
    workspace_root.mkdir(parents=True, exist_ok=False)

    worktrees: dict[str, Path] = {}
    try:
        for name in names:
            safe = _safe_name(name)
            worktree = workspace_root / safe
            _git(repository, "worktree", "add", "--detach", str(worktree), base_sha)
            worktrees[name] = worktree

        def invoke(name: str) -> AgentOutcome:
            safe = _safe_name(name)
            agent_dir = run_dir / "agents" / safe
            agent_dir.mkdir(parents=True, exist_ok=False)
            return _run_agent(
                name=name,
                command_template=agents[name],
                checks=checks,
                task=task,
                repository=repository,
                worktree=worktrees[name],
                base_sha=base_sha,
                timeout_seconds=timeout_seconds,
                run_id=run_id,
                agent_dir=agent_dir,
                keep_worktrees=keep_worktrees,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as executor:
            outcomes = list(executor.map(invoke, names))
    finally:
        if not keep_worktrees:
            for worktree in worktrees.values():
                subprocess.run(
                    ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            subprocess.run(
                ["git", "-C", str(repository), "worktree", "prune"],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                workspace_root.rmdir()
                workspace_root.parent.rmdir()
            except OSError:
                pass

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(repository),
        "base_sha": base_sha,
        "task": task,
        "timeout_seconds": timeout_seconds,
        "agent_count": len(outcomes),
        "agents": [asdict(outcome) for outcome in outcomes],
    }
    (run_dir / "run.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return run_dir, base_sha, outcomes
