"""Local multi-agent run orchestration."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_arena.adapters import (
    AdapterContext,
    ResolvedAgentAdapter,
    resolve_agent_adapter,
    validate_adapter_name,
)
from agent_arena.container_runtime import (
    ContainerGitMetadata,
    ContainerProfile,
    build_docker_command,
    cleanup_container_git_metadata,
    create_container_git_metadata,
    parse_container_profile,
    remove_container,
    resolve_local_image,
)
from agent_arena.errors import ArenaError
from agent_arena.git_utils import git_subprocess_environment

_MAX_AGENTS_PER_RUN = 16


@dataclass
class AgentOutcome:
    name: str
    adapter_name: str
    adapter_distribution: str
    adapter_version: str
    command: str
    returncode: int | None
    timed_out: bool
    duration_seconds: float
    changed_files: int
    patch_file: str
    checks: list[dict[str, Any]]
    workspace: str | None


@dataclass(frozen=True)
class AgentProfile:
    adapter_name: str
    adapter_options: Mapping[str, Any]
    inherit_env: tuple[str, ...]
    container: ContainerProfile | None = None


def resolve_repository(path: Path) -> Path:
    """Return the top-level path of a Git worktree."""
    repository = path.expanduser().resolve()
    if not repository.exists():
        raise ArenaError(f"Repository path does not exist: {repository}")
    return Path(_git(repository, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def repository_head(repository: Path) -> str:
    """Return the current commit of a repository or worktree."""
    return _git(repository, "rev-parse", "HEAD").stdout.strip()


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of a configuration or fixture file."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ArenaError(f"Could not read file {path}: {error}") from error


def run_artifacts_directory(repository: Path) -> Path:
    """Return the local, per-repository run history directory."""
    repository_key = hashlib.sha256(str(repository.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "agent-arena" / repository_key / "runs"


def _git(
    repository: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=git_subprocess_environment() if env is None else env,
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


def _read_config(config_path: Path) -> tuple[dict[str, AgentProfile], list[list[str]]]:
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

    agents: dict[str, AgentProfile] = {}
    safe_names: set[str] = set()
    for name, profile in raw_agents.items():
        if not isinstance(profile, dict):
            raise ArenaError(f"Agent profile {name!r} must be a TOML table.")
        adapter_name = validate_adapter_name(profile.get("adapter", "command"))
        raw_options = profile.get("options", {})
        if not isinstance(raw_options, dict):
            raise ArenaError(f"Agent {name!r} options must be a TOML table.")
        if adapter_name == "command":
            command = profile.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
            ):
                raise ArenaError(f"Agent {name!r} must define command as a non-empty string array.")
            if raw_options:
                raise ArenaError(
                    f"Agent {name!r} must use command = [...] instead of adapter options."
                )
            adapter_options: Mapping[str, Any] = {"command": tuple(command)}
        else:
            if "command" in profile:
                raise ArenaError(
                    f"Agent {name!r} cannot define command when adapter = {adapter_name!r}."
                )
            adapter_options = dict(raw_options)
        inherit_env = profile.get("inherit_env", [])
        if not isinstance(inherit_env, list) or not all(
            isinstance(variable, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable)
            for variable in inherit_env
        ):
            raise ArenaError(f"Agent {name!r} inherit_env must be an array of variable names.")
        safe = _safe_name(name)
        if safe in safe_names:
            raise ArenaError("Agent names must remain unique after filename normalization.")
        safe_names.add(safe)
        raw_container = profile.get("container")
        container = (
            parse_container_profile(raw_container, name) if raw_container is not None else None
        )
        agents[name] = AgentProfile(adapter_name, adapter_options, tuple(inherit_env), container)

    checks = _validate_checks(data.get("checks", []), "top-level checks")

    return agents, checks


def _validate_checks(raw_checks: Any, label: str) -> list[list[str]]:
    if not isinstance(raw_checks, list):
        raise ArenaError(f"{label} must be an array of command arrays.")
    checks: list[list[str]] = []
    for index, command in enumerate(raw_checks, start=1):
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ArenaError(f"{label} item {index} must be a non-empty string array.")
        checks.append(command)
    return checks


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


def _has_uncommitted_changes(repository: Path, allowed_untracked: set[Path]) -> bool:
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
        env=git_subprocess_environment(),
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ArenaError(f"Could not inspect repository status: {detail}")

    allowed_relatives: set[str] = set()
    for allowed_path in allowed_untracked:
        try:
            allowed_relatives.add(allowed_path.relative_to(repository).as_posix())
        except ValueError:
            pass

    for record in result.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not record:
            continue
        status_code = record[:2]
        path = record[3:]
        if status_code == "??" and path in allowed_relatives:
            continue
        return True
    return False


_BASE_ENVIRONMENT = {
    "APPDATA",
    "COMSPEC",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "HOMEDRIVE",
    "HOMEPATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "LOGNAME",
    "PATHEXT",
    "PATH",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USER",
    "USERPROFILE",
    "USERNAME",
    "VIRTUAL_ENV",
    "WINDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
}


def _child_environment(
    extra_variables: tuple[str, ...],
    arena_values: dict[str, str],
) -> dict[str, str]:
    current_keys = {key.upper(): key for key in os.environ}
    allowed = _BASE_ENVIRONMENT | {variable.upper() for variable in extra_variables}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    missing = [name for name in extra_variables if name.upper() not in current_keys]
    if missing:
        raise ArenaError("Requested environment variable(s) are not set: " + ", ".join(missing))
    for name in extra_variables:
        current_name = current_keys[name.upper()]
        environment[current_name] = os.environ[current_name]
    environment.update(arena_values)
    return environment


def _secret_values(profile: AgentProfile, environment: dict[str, str]) -> tuple[str, ...]:
    values = {name.upper(): value for name, value in environment.items()}
    return tuple(values[name.upper()] for name in profile.inherit_env if values.get(name.upper()))


def _container_environment_names(
    profile: AgentProfile,
    environment: dict[str, str],
) -> tuple[str, ...]:
    names = {name for name in environment if name.startswith("ARENA_")}
    current_keys = {key.upper(): key for key in environment}
    names.update(
        current_keys[name.upper()] for name in profile.inherit_env if name.upper() in current_keys
    )
    return tuple(sorted(names))


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _write_redacted_log(source: Any, destination: Path, secrets: tuple[str, ...]) -> None:
    patterns = tuple(dict.fromkeys(secret.encode("utf-8") for secret in secrets if secret))
    source.seek(0)
    with destination.open("wb") as output:
        if not patterns:
            shutil.copyfileobj(source, output, length=64 * 1024)
            return

        overlap = max(map(len, patterns)) - 1
        pending = b""
        while True:
            chunk = source.read(64 * 1024)
            pending += chunk
            for pattern in patterns:
                pending = pending.replace(pattern, b"[REDACTED]")
            safe_length = len(pending) if not chunk else max(0, len(pending) - overlap)
            if safe_length:
                output.write(pending[:safe_length])
                pending = pending[safe_length:]
            if not chunk:
                break


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
    redact_values: tuple[str, ...] = (),
    container_profile: ContainerProfile | None = None,
    container_git_metadata: ContainerGitMetadata | None = None,
    container_environment_names: tuple[str, ...] = (),
    container_client_environment: dict[str, str] | None = None,
) -> tuple[int, bool, float]:
    started = time.perf_counter()
    container_cleanup: Callable[[], None] | None = None
    if container_profile is not None:
        if container_git_metadata is None:
            raise ArenaError("Container execution is missing its isolated Git metadata.")
        container_name = "arena-" + uuid.uuid4().hex
        command = build_docker_command(
            container_profile,
            worktree=cwd,
            git_metadata=container_git_metadata,
            command=command,
            environment_names=container_environment_names,
            container_name=container_name,
        )
        if container_client_environment is not None:
            environment = container_client_environment

        def cleanup() -> None:
            remove_container(container_name, environment)

        container_cleanup = cleanup
    try:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            tempfile.TemporaryFile(mode="w+b", dir=stdout_path.parent) as stdout_raw,
            tempfile.TemporaryFile(mode="w+b", dir=stderr_path.parent) as stderr_raw,
        ):
            try:
                process_options: dict[str, Any] = {}
                if os.name == "nt":
                    process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    process_options["start_new_session"] = True
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_raw,
                    stderr=stderr_raw,
                    **process_options,
                )
            except OSError as error:
                stderr_raw.write(f"Could not start {command[0]!r}: {error}\n".encode())
                returncode, timed_out = 127, False
            else:
                try:
                    returncode = process.wait(timeout=timeout_seconds)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process)
                    if container_cleanup is not None:
                        container_cleanup()
                    returncode, timed_out = 124, True
                except KeyboardInterrupt:
                    _terminate_process_tree(process)
                    if container_cleanup is not None:
                        container_cleanup()
                    raise
            _write_redacted_log(stdout_raw, stdout_path, redact_values)
            _write_redacted_log(stderr_raw, stderr_path, redact_values)
        return (
            returncode,
            timed_out,
            round(time.perf_counter() - started, 3),
        )
    except OSError as error:
        message = f"Could not write command logs under {stdout_path.parent}: {error}"
        raise ArenaError(message) from error


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
    container_profile: ContainerProfile | None,
    container_git_metadata: ContainerGitMetadata | None,
    container_environment_names: tuple[str, ...],
    container_client_environment: dict[str, str] | None,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for index, template in enumerate(checks, start=1):
        execution_repository = Path("/workspace") if container_profile is not None else repository
        execution_worktree = Path("/workspace") if container_profile is not None else worktree
        command = _expand_command(
            template,
            task=task,
            repository=execution_repository,
            worktree=execution_worktree,
            base_sha=base_sha,
        )
        stdout_path = agent_dir / f"check-{index}.stdout.log"
        stderr_path = agent_dir / f"check-{index}.stderr.log"
        returncode, timed_out, duration = _run_command(
            command,
            cwd=worktree,
            environment=environment,
            timeout_seconds=timeout_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            container_profile=container_profile,
            container_git_metadata=container_git_metadata,
            container_environment_names=container_environment_names,
            container_client_environment=container_client_environment,
        )
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
    profile: AgentProfile,
    adapter: ResolvedAgentAdapter,
    checks: list[list[str]],
    task: str,
    repository: Path,
    worktree: Path,
    base_sha: str,
    timeout_seconds: int,
    run_id: str,
    agent_dir: Path,
    keep_worktrees: bool,
    container_git_metadata: ContainerGitMetadata | None,
) -> AgentOutcome:
    execution_repository = Path("/workspace") if profile.container is not None else repository
    execution_worktree = Path("/workspace") if profile.container is not None else worktree
    invocation = adapter.build_invocation(
        AdapterContext(
            agent_name=name,
            task=task,
            repository=execution_repository,
            worktree=execution_worktree,
            base_sha=base_sha,
            run_id=run_id,
        ),
        profile.adapter_options,
    )
    command = list(invocation.argv)
    arena_values = {
        "ARENA_TASK": task,
        "ARENA_REPOSITORY": str(execution_repository),
        "ARENA_WORKTREE": str(execution_worktree),
        "ARENA_BASE_SHA": base_sha,
        "ARENA_RUN_ID": run_id,
        "ARENA_AGENT": name,
    }
    environment = _child_environment(profile.inherit_env, arena_values)
    check_environment = _child_environment((), arena_values)
    container_environment_names = (
        _container_environment_names(profile, environment) if profile.container is not None else ()
    )
    check_container_environment_names = tuple(
        name for name in check_environment if name.startswith("ARENA_")
    )
    container_client_environment = None
    check_container_client_environment = None
    if profile.container is not None:
        container_client_environment = os.environ.copy()
        container_client_environment.update(environment)
        check_container_client_environment = os.environ.copy()
        check_container_client_environment.update(check_environment)

    stdout_path = agent_dir / "stdout.log"
    stderr_path = agent_dir / "stderr.log"
    returncode, timed_out, duration = _run_command(
        command,
        cwd=worktree,
        environment=environment,
        timeout_seconds=timeout_seconds,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        redact_values=_secret_values(profile, environment),
        container_profile=profile.container,
        container_git_metadata=container_git_metadata,
        container_environment_names=container_environment_names,
        container_client_environment=container_client_environment,
    )

    check_results = _run_checks(
        checks,
        task=task,
        repository=repository,
        worktree=worktree,
        base_sha=base_sha,
        timeout_seconds=timeout_seconds,
        environment=check_environment,
        agent_dir=agent_dir,
        container_profile=profile.container,
        container_git_metadata=container_git_metadata,
        container_environment_names=check_container_environment_names,
        container_client_environment=check_container_client_environment,
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
        adapter_name=adapter.descriptor.name,
        adapter_distribution=adapter.descriptor.distribution,
        adapter_version=adapter.descriptor.version,
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
    checks_override: list[list[str]] | None = None,
    base_revision: str | None = None,
    allowed_untracked_paths: tuple[Path, ...] = (),
    expected_config_sha256: str | None = None,
    expected_container_images: dict[str, str] | None = None,
    expected_adapters: dict[str, dict[str, str]] | None = None,
) -> tuple[Path, str, list[AgentOutcome]]:
    """Run each selected agent in its own worktree and save review artifacts."""
    if not task.strip():
        raise ArenaError("Task text cannot be empty.")
    if timeout_seconds < 1:
        raise ArenaError("Timeout must be at least one second.")
    if timeout_seconds > 86_400:
        raise ArenaError("Timeout cannot exceed 86,400 seconds (24 hours).")

    repository = resolve_repository(repository_path)

    config = config_path.expanduser()
    if not config.is_absolute():
        config = repository / config
    config = config.resolve()
    allowed_untracked = {
        path.resolve()
        for path in (config, *allowed_untracked_paths)
        if path.resolve().is_relative_to(repository)
    }
    if _has_uncommitted_changes(repository, allowed_untracked):
        raise ArenaError(
            "The repository has uncommitted changes. Commit or stash them first; "
            "each run starts from HEAD. Only explicitly selected untracked config files "
            "are allowed."
        )
    base_sha = repository_head(repository)
    if base_revision is not None and base_sha != base_revision:
        raise ArenaError(
            "The repository HEAD changed during the benchmark; "
            f"expected {base_revision[:12]}, found {base_sha[:12]}."
        )
    config_sha_before = file_sha256(config) if expected_config_sha256 is not None else None
    agents, checks = _read_config(config)
    if expected_config_sha256 is not None and (
        config_sha_before != expected_config_sha256 or file_sha256(config) != expected_config_sha256
    ):
        raise ArenaError("The agent configuration changed during the benchmark.")
    if checks_override is not None:
        checks = _validate_checks(checks_override, "benchmark checks")

    if selected_agents:
        missing = [name for name in selected_agents if name not in agents]
        if missing:
            raise ArenaError(f"Unknown agent profile(s): {', '.join(missing)}")
        names = list(dict.fromkeys(selected_agents))
    else:
        names = list(agents)
    if len(names) > _MAX_AGENTS_PER_RUN:
        raise ArenaError(f"A single run is limited to {_MAX_AGENTS_PER_RUN} agents.")
    execution_profiles: dict[str, AgentProfile] = {}
    resolved_adapters: dict[str, ResolvedAgentAdapter] = {}
    adapter_snapshots: dict[str, dict[str, str]] = {}
    container_image_ids: dict[str, str] = {}
    for name in names:
        _child_environment(agents[name].inherit_env, {})
        profile = agents[name]
        adapter = resolve_agent_adapter(profile.adapter_name)
        snapshot = adapter.descriptor.as_manifest()
        if expected_adapters is not None and expected_adapters.get(name) != snapshot:
            raise ArenaError(f"Agent adapter for {name!r} changed during the benchmark.")
        adapter_snapshots[name] = snapshot
        resolved_adapters[name] = adapter
        if profile.container is not None:
            image_id = resolve_local_image(profile.container.image)
            if (
                expected_container_images is not None
                and expected_container_images.get(name) != image_id
            ):
                raise ArenaError(f"Docker image for agent {name!r} changed during the benchmark.")
            container_image_ids[name] = image_id
            profile = replace(profile, container=replace(profile.container, image=image_id))
        execution_profiles[name] = profile
    if expected_container_images is not None and container_image_ids != expected_container_images:
        raise ArenaError("The configured container images changed during the benchmark.")
    if expected_adapters is not None and adapter_snapshots != expected_adapters:
        raise ArenaError("The configured agent adapters changed during the benchmark.")

    run_started_at = datetime.now(UTC)
    run_id = run_started_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    repo_key = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:12]
    temp_root = Path(tempfile.gettempdir()) / "agent-arena" / repo_key
    run_dir = run_artifacts_directory(repository) / run_id
    workspace_root = temp_root / "worktrees" / run_id
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            temp_root.chmod(0o700)
        run_dir.mkdir(parents=True, exist_ok=False)
        workspace_root.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise ArenaError(f"Could not create run storage in {temp_root}: {error}") from error

    worktrees: dict[str, Path] = {}
    container_git_root = temp_root / "container-git" / run_id
    container_git: dict[str, ContainerGitMetadata] = {}
    container_git_root_identity: tuple[int, int] | None = None
    try:
        for name in names:
            safe = _safe_name(name)
            worktree = workspace_root / safe
            _git(repository, "worktree", "add", "--detach", str(worktree), base_sha)
            worktrees[name] = worktree

        if container_image_ids:
            container_git = create_container_git_metadata(
                repository,
                base_sha,
                {name: worktrees[name] for name in container_image_ids},
                container_git_root,
            )
            if container_git:
                container_git_root_identity = next(iter(container_git.values())).root_identity

        def invoke(name: str) -> AgentOutcome:
            safe = _safe_name(name)
            agent_dir = run_dir / "agents" / safe
            try:
                agent_dir.mkdir(parents=True, exist_ok=False)
                return _run_agent(
                    name=name,
                    profile=execution_profiles[name],
                    adapter=resolved_adapters[name],
                    checks=checks,
                    task=task,
                    repository=repository,
                    worktree=worktrees[name],
                    base_sha=base_sha,
                    timeout_seconds=timeout_seconds,
                    run_id=run_id,
                    agent_dir=agent_dir,
                    keep_worktrees=keep_worktrees,
                    container_git_metadata=container_git.get(name),
                )
            except OSError as error:
                raise ArenaError(
                    f"Could not store artifacts for agent {name!r}: {error}"
                ) from error

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as executor:
            outcomes = list(executor.map(invoke, names))
    finally:
        cleanup_container_git_metadata(container_git_root, container_git_root_identity)
        if not keep_worktrees:
            for worktree in worktrees.values():
                subprocess.run(
                    ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=git_subprocess_environment(),
                )
            subprocess.run(
                ["git", "-C", str(repository), "worktree", "prune"],
                capture_output=True,
                text=True,
                check=False,
                env=git_subprocess_environment(),
            )
            try:
                workspace_root.rmdir()
                workspace_root.parent.rmdir()
            except OSError:
                pass

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": run_started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "repository": str(repository),
        "base_sha": base_sha,
        "task": task,
        "timeout_seconds": timeout_seconds,
        "agent_count": len(outcomes),
        "adapters": adapter_snapshots,
        "container_images": container_image_ids,
        "agents": [asdict(outcome) for outcome in outcomes],
    }
    try:
        (run_dir / "run.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise ArenaError(f"Could not write run manifest in {run_dir}: {error}") from error
    return run_dir, base_sha, outcomes
