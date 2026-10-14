from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import agent_arena.runner as runner
from agent_arena.errors import ArenaError
from agent_arena.git_utils import git_subprocess_environment


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=git_subprocess_environment(),
    )
    return result.stdout.strip()


def _repository(path: Path) -> tuple[Path, Path, str]:
    repository = path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Runner Test")
    _git(repository, "config", "user.email", "runner-test@example.invalid")
    (repository / "tracked.txt").write_bytes(b"base\n")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "base")
    base_sha = _git(repository, "rev-parse", "HEAD")
    config = repository / ".agent-arena.toml"
    code = (
        "import pathlib,sys; pathlib.Path('result.txt').write_text(sys.argv[1], encoding='utf-8')"
    )
    check_code = (
        "from pathlib import Path; "
        "assert Path('result.txt').read_text(encoding='utf-8') == 'Run this task'"
    )
    command = [sys.executable, "-c", code, "{task}"]
    check = [sys.executable, "-c", check_code]
    config_text = (
        "version = 1\n"
        f"checks = [[{', '.join(json.dumps(part) for part in check)}]]\n\n"
        "[agents.mock]\n"
        f"command = [{', '.join(json.dumps(part) for part in command)}]\n"
    )
    config.write_text(config_text, encoding="utf-8")
    return repository, config, base_sha


def _run(
    repository: Path,
    *,
    config_path: Path,
    **overrides: object,
) -> tuple[Path, str, list[runner.AgentOutcome]]:
    options: dict[str, object] = {
        "repository_path": repository,
        "config_path": config_path,
        "task": "Run this task",
        "selected_agents": None,
        "timeout_seconds": 20,
        "keep_worktrees": False,
    }
    options.update(overrides)
    return runner.run_arena(**options)  # type: ignore[arg-type]


def test_run_arena_runs_agent_checks_patch_and_cleans_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, config, base_sha = _repository(tmp_path)
    artifacts = tmp_path / "runs"
    monkeypatch.setattr(runner, "run_artifacts_directory", lambda _repo: artifacts)

    run_dir, actual_base, outcomes = _run(repository, config_path=config)

    assert actual_base == base_sha
    assert run_dir.parent == artifacts
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.name == "mock"
    assert outcome.returncode == 0
    assert outcome.changed_files == 1
    assert outcome.checks[0]["returncode"] == 0
    assert outcome.workspace is None
    assert (run_dir / "agents" / "mock" / "patch.diff").is_file()
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert manifest["base_sha"] == base_sha
    assert manifest["agent_count"] == 1
    assert _git(repository, "rev-parse", "HEAD") == base_sha
    assert not (repository / "result.txt").exists()
    assert (
        sum(
            line.startswith("worktree ")
            for line in _git(repository, "worktree", "list", "--porcelain").splitlines()
        )
        == 1
    )


def test_run_arena_preserves_preexisting_container_metadata_on_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, config, _base_sha = _repository(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8") + '\n[agents.mock.container]\nimage = "mock:local"\n',
        encoding="utf-8",
    )
    artifacts = tmp_path / "runs"
    temp_root = tmp_path / "temporary-storage"
    monkeypatch.setattr(runner, "run_artifacts_directory", lambda _repo: artifacts)
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(temp_root))
    monkeypatch.setattr(
        runner,
        "resolve_local_image",
        lambda _image: "sha256:" + "a" * 64,
    )
    original_create_metadata = runner.create_container_git_metadata
    sentinel_files: list[Path] = []

    def collide_with_existing_root(
        repository: Path,
        base_sha: str,
        worktrees: dict[str, Path],
        root: Path,
    ) -> dict[str, runner.ContainerGitMetadata]:
        root.mkdir(parents=True)
        sentinel = root / "keep-user-data.txt"
        sentinel.write_text("user data\n", encoding="utf-8")
        sentinel_files.append(sentinel)
        return original_create_metadata(repository, base_sha, worktrees, root)

    monkeypatch.setattr(runner, "create_container_git_metadata", collide_with_existing_root)

    with pytest.raises(ArenaError, match="Could not prepare isolated container Git metadata"):
        _run(repository, config_path=config)

    assert sentinel_files[0].read_text(encoding="utf-8") == "user data\n"


def test_run_arena_rejects_dirty_repository_without_touching_untracked_files(
    tmp_path: Path,
) -> None:
    repository, config, base_sha = _repository(tmp_path)
    untracked = repository / "keep.txt"
    untracked.write_text("user data\n", encoding="utf-8")

    with pytest.raises(ArenaError, match="uncommitted changes"):
        _run(repository, config_path=config)

    assert untracked.read_text(encoding="utf-8") == "user data\n"
    assert _git(repository, "rev-parse", "HEAD") == base_sha


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base_revision": "stale-base"}, "HEAD changed during the benchmark"),
        ({"expected_config_sha256": "0" * 64}, "configuration changed during the benchmark"),
        ({"selected_agents": ["not-configured"]}, "Unknown agent profile"),
        ({"expected_adapters": {"mock": {"name": "stale"}}}, "adapter for 'mock' changed"),
        ({"expected_container_images": {"mock": "sha256:stale"}}, "container images changed"),
    ],
)
def test_run_arena_rejects_stale_snapshots_and_unknown_agents(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    repository, config, _base_sha = _repository(tmp_path)

    with pytest.raises(ArenaError, match=message):
        _run(repository, config_path=config, **overrides)


@pytest.mark.parametrize(
    ("task", "timeout_seconds", "checks_override", "message"),
    [
        ("  ", 20, None, "Task text cannot be empty"),
        ("task", 0, None, "Timeout must be at least one second"),
        ("task", 86_401, None, "Timeout cannot exceed"),
        ("task", 20, [[""]], "benchmark checks item 1 must be a non-empty string array"),
    ],
)
def test_run_arena_rejects_invalid_task_limits_and_checks(
    tmp_path: Path,
    task: str,
    timeout_seconds: int,
    checks_override: list[list[str]] | None,
    message: str,
) -> None:
    repository, config, _base_sha = _repository(tmp_path)
    with pytest.raises(ArenaError, match=message):
        runner.run_arena(
            repository_path=repository,
            config_path=config,
            task=task,
            selected_agents=None,
            timeout_seconds=timeout_seconds,
            keep_worktrees=False,
            checks_override=checks_override,
        )
