"""Docker command construction for the optional container execution backend."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_arena.errors import ArenaError
from agent_arena.git_utils import git_subprocess_environment

_IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MEMORY_LIMIT = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)?[bkmg]?\Z", re.IGNORECASE)
_NETWORK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}\Z")
_USER_SPEC = re.compile(r"[0-9]+(?::[0-9]+)?\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ContainerProfile:
    image: str
    network: str
    memory: str
    cpus: float
    pids_limit: int
    user: str | None


@dataclass(frozen=True)
class ContainerGitMetadata:
    """Per-agent Git metadata mounts that keep the host .git directory private."""

    git_dir: Path
    base_objects: Path
    git_link_file: Path
    root_identity: tuple[int, int] | None = None


def _remove_owned_directory(root: Path, identity: tuple[int, int] | None) -> None:
    """Remove a directory only while its path still names the directory we created."""
    if identity is None:
        return
    try:
        current = root.lstat()
    except OSError:
        return
    if stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == identity:
        shutil.rmtree(root, ignore_errors=True)


def cleanup_container_git_metadata(root: Path, identity: tuple[int, int] | None) -> None:
    """Remove container metadata only when the path still names our owned directory."""
    _remove_owned_directory(root, identity)


def parse_container_profile(value: Any, agent_name: str) -> ContainerProfile:
    """Validate the optional [agents.NAME.container] Docker profile table."""
    if not isinstance(value, dict):
        raise ArenaError(f"Agent {agent_name!r} container config must be a TOML table.")
    allowed = {"image", "network", "memory", "cpus", "pids_limit", "user"}
    unknown = set(value) - allowed
    if unknown:
        raise ArenaError(
            f"Unknown container setting(s) for agent {agent_name!r}: " + ", ".join(sorted(unknown))
        )

    image = value.get("image")
    if not isinstance(image, str) or not _IMAGE_REFERENCE.fullmatch(image):
        raise ArenaError(
            f"Agent {agent_name!r} container image must be a valid local Docker image reference."
        )

    network = value.get("network", "none")
    if not isinstance(network, str) or not _NETWORK_NAME.fullmatch(network):
        raise ArenaError(f"Agent {agent_name!r} container network name is invalid.")

    memory = value.get("memory", "4g")
    if not isinstance(memory, str) or not _MEMORY_LIMIT.fullmatch(memory):
        raise ArenaError(
            f"Agent {agent_name!r} container memory must be a positive Docker limit such as '4g'."
        )

    cpus = value.get("cpus", 2.0)
    if not isinstance(cpus, int | float) or isinstance(cpus, bool) or not 0 < cpus <= 64:
        raise ArenaError(f"Agent {agent_name!r} container cpus must be between 0 and 64.")

    pids_limit = value.get("pids_limit", 256)
    if (
        not isinstance(pids_limit, int)
        or isinstance(pids_limit, bool)
        or not 1 <= pids_limit <= 65_536
    ):
        raise ArenaError(f"Agent {agent_name!r} container pids_limit must be 1–65,536.")

    user = value.get("user")
    if user is not None and (not isinstance(user, str) or not _USER_SPEC.fullmatch(user)):
        raise ArenaError(f"Agent {agent_name!r} container user must be a numeric UID or UID:GID.")

    return ContainerProfile(
        image=image,
        network=network,
        memory=memory,
        cpus=float(cpus),
        pids_limit=pids_limit,
        user=user,
    )


def resolve_local_image(image: str) -> str:
    """Resolve an already available Docker image to its immutable local image ID."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except FileNotFoundError as error:
        raise ArenaError(
            "Docker was not found. Install Docker and make sure it is on PATH."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ArenaError("Docker timed out while inspecting the configured image.") from error
    if result.returncode:
        raise ArenaError(
            f"Docker could not inspect image {image!r}. Build or pull it locally first: "
            f"{result.stderr.strip()}"
        )
    image_id = result.stdout.strip()
    if not _IMAGE_ID.fullmatch(image_id):
        raise ArenaError(f"Docker returned an invalid image ID for {image!r}.")
    return image_id


def _git_command(*arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
            env=git_subprocess_environment(),
        )
    except FileNotFoundError as error:
        raise ArenaError("Git was not found while preparing container workspaces.") from error
    except subprocess.TimeoutExpired as error:
        raise ArenaError("Git timed out while preparing container workspaces.") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ArenaError(f"Could not prepare container Git metadata: {detail}")
    return result


def create_container_git_metadata(
    repository: Path,
    base_sha: str,
    worktrees: dict[str, Path],
    root: Path,
) -> dict[str, ContainerGitMetadata]:
    """Create shallow, per-agent Git dirs sharing only the run's base snapshot."""
    base_repository = root / "base.git"
    root_identity: tuple[int, int] | None = None
    try:
        root.mkdir(parents=True, exist_ok=False)
        root_stat = root.lstat()
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        _git_command("init", "--bare", str(base_repository))
        _git_command(
            f"--git-dir={base_repository}",
            "fetch",
            "--no-tags",
            "--depth=1",
            str(repository),
            base_sha,
        )
        shallow_path = base_repository / "shallow"
        shallow_content = shallow_path.read_bytes() if shallow_path.exists() else b""
        if shallow_content and base_sha.encode("ascii") not in shallow_content.splitlines():
            raise ArenaError("Git did not create the expected shallow container base snapshot.")

        metadata: dict[str, ContainerGitMetadata] = {}
        for agent_name, worktree in worktrees.items():
            git_dir = root / "agents" / worktree.name
            git_dir.parent.mkdir(parents=True, exist_ok=True)
            _git_command("init", "--bare", str(git_dir))
            alternate_path = git_dir / "objects" / "info" / "alternates"
            host_object_path = str(base_repository / "objects").replace("\\", "/")
            alternate_path.write_bytes(host_object_path.encode("utf-8") + b"\n")
            if shallow_content:
                (git_dir / "shallow").write_bytes(shallow_content)
            prefix = f"--git-dir={git_dir}"
            _git_command(prefix, "config", "core.bare", "false")
            _git_command(prefix, "config", "core.worktree", "/workspace")
            _git_command(prefix, "config", "core.filemode", "false")
            _git_command(prefix, "config", "user.name", "Agent Arena")
            _git_command(prefix, "config", "user.email", "agent-arena@localhost")
            _git_command(prefix, "update-ref", "refs/heads/agent-arena-base", base_sha)
            _git_command(prefix, "symbolic-ref", "HEAD", "refs/heads/agent-arena-base")
            _git_command(prefix, "read-tree", base_sha)
            alternate_path.write_bytes(b"/arena-base-objects\n")

            git_link_file = git_dir.parent / f"{git_dir.name}.git-link"
            git_link_file.write_bytes(b"gitdir: /arena-git\n")
            metadata[agent_name] = ContainerGitMetadata(
                git_dir=git_dir,
                base_objects=base_repository / "objects",
                git_link_file=git_link_file,
                root_identity=root_identity,
            )
        return metadata
    except (OSError, UnicodeError) as error:
        _remove_owned_directory(root, root_identity)
        raise ArenaError(f"Could not prepare isolated container Git metadata: {error}") from error
    except ArenaError:
        _remove_owned_directory(root, root_identity)
        raise


def build_docker_command(
    profile: ContainerProfile,
    *,
    worktree: Path,
    git_metadata: ContainerGitMetadata,
    command: list[str],
    environment_names: tuple[str, ...],
    container_name: str,
) -> list[str]:
    """Build a constrained one-shot Docker run with an isolated shallow Git snapshot."""
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ArenaError("Container command must be a non-empty argument array.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", container_name):
        raise ArenaError("Generated Docker container name is invalid.")

    mount_sources = (
        str(worktree.resolve()),
        str(git_metadata.git_dir.resolve()),
        str(git_metadata.base_objects.resolve()),
        str(git_metadata.git_link_file.resolve()),
    )
    if any("," in source for source in mount_sources):
        raise ArenaError(
            "A temporary container mount path contains a comma, which Docker mount syntax "
            "cannot encode."
        )

    arguments = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--pull=never",
        "--name",
        container_name,
        "--network",
        profile.network,
        "--memory",
        profile.memory,
        "--memory-swap",
        profile.memory,
        "--cpus",
        format(profile.cpus, "g"),
        "--pids-limit",
        str(profile.pids_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=1g",
        "--mount",
        f"type=bind,source={mount_sources[0]},target=/workspace",
        "--mount",
        f"type=bind,source={mount_sources[1]},target=/arena-git",
        "--mount",
        f"type=bind,source={mount_sources[2]},target=/arena-base-objects,readonly",
        "--mount",
        f"type=bind,source={mount_sources[3]},target=/workspace/.git,readonly",
        "--workdir",
        "/workspace",
    ]
    container_user = profile.user
    if container_user is None and os.name != "nt":
        container_user = f"{os.getuid()}:{os.getgid()}"
    if container_user is not None:
        arguments.extend(("--user", container_user))
    for name in dict.fromkeys(environment_names):
        if not _ENVIRONMENT_NAME.fullmatch(name):
            raise ArenaError(f"Invalid container environment variable name: {name!r}")
        arguments.extend(("--env", name))
    arguments.extend(
        (
            "--env",
            "HOME=/tmp",
            "--env",
            "GIT_CONFIG_COUNT=1",
            "--env",
            "GIT_CONFIG_KEY_0=safe.directory",
            "--env",
            "GIT_CONFIG_VALUE_0=/workspace",
        )
    )
    arguments.extend((profile.image, *command))
    return arguments


def remove_container(container_name: str, environment: dict[str, str]) -> None:
    """Force-remove a timed-out container without masking the original result."""
    try:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
