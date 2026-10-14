from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import agent_arena.container_runtime as container_runtime
from agent_arena.container_runtime import (
    ContainerGitMetadata,
    build_docker_command,
    cleanup_container_git_metadata,
    create_container_git_metadata,
    parse_container_profile,
    remove_container,
    resolve_local_image,
)
from agent_arena.errors import ArenaError


def test_parse_container_profile_applies_safe_defaults() -> None:
    profile = parse_container_profile({"image": "arena-agent:stable"}, "codex")

    assert profile.image == "arena-agent:stable"
    assert profile.network == "none"
    assert profile.memory == "4g"
    assert profile.cpus == 2.0
    assert profile.pids_limit == 256
    assert profile.user is None


def test_parse_container_profile_accepts_valid_custom_limits() -> None:
    profile = parse_container_profile(
        {
            "image": "registry.example/arena@sha256:abc123",
            "network": "isolated-net",
            "memory": "1.5g",
            "cpus": 3,
            "pids_limit": 512,
            "user": "1000:1000",
        },
        "codex",
    )

    assert profile.image == "registry.example/arena@sha256:abc123"
    assert profile.network == "isolated-net"
    assert profile.memory == "1.5g"
    assert profile.cpus == 3.0
    assert profile.pids_limit == 512
    assert profile.user == "1000:1000"


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (None, "must be a TOML table"),
        ({"image": "bad image"}, "valid local Docker image"),
        ({"image": "arena", "network": "bad network"}, "network name is invalid"),
        ({"image": "arena", "memory": "0g"}, "positive Docker limit"),
        ({"image": "arena", "cpus": True}, "cpus must be between"),
        ({"image": "arena", "cpus": 0}, "cpus must be between"),
        ({"image": "arena", "cpus": 65}, "cpus must be between"),
        ({"image": "arena", "pids_limit": True}, "pids_limit must be"),
        ({"image": "arena", "pids_limit": 0}, "pids_limit must be"),
        ({"image": "arena", "user": "root"}, "numeric UID or UID:GID"),
        ({"image": "arena", "unexpected": True}, "Unknown container setting"),
    ],
)
def test_parse_container_profile_rejects_invalid_settings(settings, message: str) -> None:
    with pytest.raises(ArenaError, match=message):
        parse_container_profile(settings, "codex")


def test_resolve_local_image_returns_immutable_id(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "sha256:" + "a" * 64 + "\n", "")

    monkeypatch.setattr(container_runtime.subprocess, "run", fake_run)

    image_id = resolve_local_image("arena:latest")

    assert image_id == "sha256:" + "a" * 64
    assert observed["command"] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        "arena:latest",
    ]
    assert observed["kwargs"]["timeout"] == 30


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError(), "Docker was not found"),
        (subprocess.TimeoutExpired("docker", 30), "Docker timed out"),
    ],
)
def test_resolve_local_image_reports_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    message: str,
) -> None:
    def fake_run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(container_runtime.subprocess, "run", fake_run)

    with pytest.raises(ArenaError, match=message):
        resolve_local_image("arena:latest")


def test_resolve_local_image_rejects_missing_and_malformed_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "not found"),
    )
    with pytest.raises(ArenaError, match="Build or pull it locally first"):
        resolve_local_image("arena:missing")

    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "not-an-id", ""),
    )
    with pytest.raises(ArenaError, match="invalid image ID"):
        resolve_local_image("arena:latest")


def test_build_docker_command_applies_isolation_and_environment_limits(tmp_path: Path) -> None:
    profile = parse_container_profile({"image": "sha256:" + "b" * 64, "user": "1000:1000"}, "codex")
    metadata = ContainerGitMetadata(
        git_dir=tmp_path / "agent.git",
        base_objects=tmp_path / "base-objects",
        git_link_file=tmp_path / "agent.git-link",
    )

    command = build_docker_command(
        profile,
        worktree=tmp_path / "worktree",
        git_metadata=metadata,
        command=["agent-cli", "Fix parser"],
        environment_names=("API_KEY", "API_KEY", "MODEL"),
        container_name="arena-test-1",
    )

    assert command[:2] == ["docker", "run"]
    for required in (
        "--rm",
        "--init",
        "--pull=never",
        "--network",
        "none",
        "--memory-swap",
        "4g",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--user",
        "1000:1000",
        "--env",
        "API_KEY",
        "MODEL",
        "GIT_CONFIG_VALUE_0=/workspace",
        "sha256:" + "b" * 64,
        "Fix parser",
    ):
        assert required in command
    assert command.count("API_KEY") == 1
    assert (
        "type=bind,source="
        + str(metadata.base_objects.resolve())
        + ",target=/arena-base-objects,readonly"
        in command
    )
    assert (
        "type=bind,source="
        + str(metadata.git_link_file.resolve())
        + ",target=/workspace/.git,readonly"
        in command
    )


@pytest.mark.parametrize(
    ("command", "environment_names", "container_name", "message"),
    [
        ([], (), "arena-valid", "non-empty argument array"),
        (["agent"], (), "Bad_Name", "container name is invalid"),
        (["agent"], ("BAD-NAME",), "arena-valid", "Invalid container environment"),
    ],
)
def test_build_docker_command_rejects_invalid_inputs(
    tmp_path: Path,
    command: list[str],
    environment_names: tuple[str, ...],
    container_name: str,
    message: str,
) -> None:
    profile = parse_container_profile({"image": "arena"}, "codex")
    metadata = ContainerGitMetadata(
        git_dir=tmp_path / "agent.git",
        base_objects=tmp_path / "base-objects",
        git_link_file=tmp_path / "agent.git-link",
    )

    with pytest.raises(ArenaError, match=message):
        build_docker_command(
            profile,
            worktree=tmp_path / "worktree",
            git_metadata=metadata,
            command=command,
            environment_names=environment_names,
            container_name=container_name,
        )


def test_build_docker_command_rejects_unrepresentable_mount_path(tmp_path: Path) -> None:
    profile = parse_container_profile({"image": "arena"}, "codex")
    metadata = ContainerGitMetadata(
        git_dir=tmp_path / "agent,git",
        base_objects=tmp_path / "base-objects",
        git_link_file=tmp_path / "agent.git-link",
    )

    with pytest.raises(ArenaError, match="contains a comma"):
        build_docker_command(
            profile,
            worktree=tmp_path / "worktree",
            git_metadata=metadata,
            command=["agent"],
            environment_names=(),
            container_name="arena-valid",
        )


def test_remove_container_suppresses_cleanup_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired("docker rm", 20)

    monkeypatch.setattr(container_runtime.subprocess, "run", fake_run)

    remove_container("arena-timeout", {"DOCKER_HOST": "local"})

    assert calls[0][0][0] == ["docker", "rm", "--force", "arena-timeout"]
    assert calls[0][1]["env"] == {"DOCKER_HOST": "local"}
    assert calls[0][1]["timeout"] == 20


def test_container_metadata_preserves_preexisting_root_and_contents(tmp_path: Path) -> None:
    root = tmp_path / "existing-container-data"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("user data\n", encoding="utf-8")

    with pytest.raises(ArenaError, match="Could not prepare isolated container Git metadata"):
        create_container_git_metadata(
            repository=tmp_path / "unused-repository",
            base_sha="unused-sha",
            worktrees={},
            root=root,
        )

    assert sentinel.read_text(encoding="utf-8") == "user data\n"


def test_container_metadata_removes_only_its_partial_root_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "new-container-data"

    def fail_git(*_arguments: str) -> subprocess.CompletedProcess[str]:
        raise ArenaError("synthetic Git setup failure")

    monkeypatch.setattr(container_runtime, "_git_command", fail_git)

    with pytest.raises(ArenaError, match="synthetic Git setup failure"):
        create_container_git_metadata(
            repository=tmp_path / "unused-repository",
            base_sha="unused-sha",
            worktrees={},
            root=root,
        )

    assert not root.exists()


def test_container_metadata_creates_isolated_shallow_git_dirs(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Container Test")
    git("config", "user.email", "container-test@example.invalid")
    (repository / "source.txt").write_bytes(b"source\n")
    git("add", "source.txt")
    git("commit", "-m", "base")
    base_sha = git("rev-parse", "HEAD")
    root = tmp_path / "container-data"
    workspace = tmp_path / "worktrees" / "agent-one"

    metadata = create_container_git_metadata(
        repository=repository,
        base_sha=base_sha,
        worktrees={"agent-one": workspace},
        root=root,
    )

    agent_metadata = metadata["agent-one"]
    assert agent_metadata.git_dir.is_dir()
    assert agent_metadata.base_objects.is_dir()
    assert agent_metadata.git_link_file.read_bytes() == b"gitdir: /arena-git\n"
    assert (agent_metadata.git_dir / "objects" / "info" / "alternates").read_bytes() == (
        b"/arena-base-objects\n"
    )
    assert (agent_metadata.git_dir / "shallow").read_text(encoding="ascii").strip() == base_sha
    assert (
        subprocess.run(
            ["git", f"--git-dir={agent_metadata.git_dir}", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        == base_sha
    )


def test_container_metadata_cleanup_preserves_replaced_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "container-data"
    replacement = tmp_path / "original-owned-root"

    def replace_root_then_fail(*_arguments: str) -> subprocess.CompletedProcess[str]:
        root.replace(replacement)
        root.mkdir()
        (root / "user-file.txt").write_text("preserve me\n", encoding="utf-8")
        raise ArenaError("synthetic Git setup failure")

    monkeypatch.setattr(container_runtime, "_git_command", replace_root_then_fail)

    with pytest.raises(ArenaError, match="synthetic Git setup failure"):
        create_container_git_metadata(
            repository=tmp_path / "unused-repository",
            base_sha="unused-sha",
            worktrees={},
            root=root,
        )

    assert (root / "user-file.txt").read_text(encoding="utf-8") == "preserve me\n"


def test_container_metadata_cleanup_preserves_replacement_after_success(tmp_path: Path) -> None:
    root = tmp_path / "container-data"
    root.mkdir()
    root_stat = root.lstat()
    root_identity = (root_stat.st_dev, root_stat.st_ino)
    root.rename(tmp_path / "owned-container-data")
    root.mkdir()
    sentinel = root / "keep-user-data.txt"
    sentinel.write_text("user data\n", encoding="utf-8")

    cleanup_container_git_metadata(root, root_identity)

    assert sentinel.read_text(encoding="utf-8") == "user data\n"
