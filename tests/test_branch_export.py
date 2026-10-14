from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import agent_arena.branch_export as branch_export
from agent_arena.git_utils import git_subprocess_environment
from agent_arena.runner import ArenaError

RUN_ID = "20261014T120000Z-123456ab"


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=git_subprocess_environment(),
    )
    return result.stdout.strip()


def _repository(path: Path) -> tuple[Path, str, str]:
    repository = path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Agent Arena Test")
    _git(repository, "config", "user.email", "arena-test@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    (repository / "readme.txt").write_bytes(b"base\n")
    _git(repository, "add", "readme.txt")
    _git(repository, "commit", "-m", "base")
    base_sha = _git(repository, "rev-parse", "HEAD")
    (repository / "readme.txt").write_bytes(b"agent result\n")
    patch = _git(repository, "diff", "--binary")
    (repository / "readme.txt").write_bytes(b"base\n")
    return repository, base_sha, patch


def _run_artifact(
    root: Path,
    repository: Path,
    base_sha: str,
    patch: str,
    *,
    agent_name: str = "codex",
) -> Path:
    run_dir = root / RUN_ID
    patch_path = run_dir / "agents" / "codex" / "patch.diff"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_bytes((patch.rstrip("\r\n") + "\n").encode("utf-8"))
    manifest = {
        "run_id": RUN_ID,
        "repository": str(repository.resolve()),
        "base_sha": base_sha,
        "agents": [
            {
                "name": agent_name,
                "patch_file": "agents/codex/patch.diff",
                "returncode": 0,
                "timed_out": False,
                "checks": [
                    {"returncode": 0, "timed_out": False},
                    {"returncode": 1, "timed_out": False},
                ],
            }
        ],
    }
    (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


def _prepare_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repository, base_sha, patch = _repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    _run_artifact(artifacts, repository, base_sha, patch)
    monkeypatch.setattr(branch_export, "run_artifacts_directory", lambda _repo: artifacts)
    return repository, base_sha, patch, artifacts


def test_export_creates_branch_without_changing_checked_out_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base_sha, _patch, _artifacts = _prepare_export(tmp_path, monkeypatch)

    result = branch_export.export_agent_branch(
        repository_path=repository,
        run_id=RUN_ID,
        agent_name="codex",
        branch_name="arena/result",
    )

    assert result.branch_name == "arena/result"
    assert result.base_sha == base_sha
    assert result.commit_sha == _git(repository, "rev-parse", "arena/result")
    assert result.checks_passed == 1
    assert result.checks_total == 2
    assert result.cleanup_warning is None
    assert _git(repository, "rev-parse", "HEAD") == base_sha
    assert (repository / "readme.txt").read_text(encoding="utf-8") == "base\n"
    assert _git(repository, "show", "arena/result:readme.txt") == "agent result"
    worktrees = _git(repository, "worktree", "list", "--porcelain").splitlines()
    assert sum(line.startswith("worktree ") for line in worktrees) == 1


def test_export_rejects_dirty_repository_before_creating_a_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _base_sha, _patch, _artifacts = _prepare_export(tmp_path, monkeypatch)
    checked_out_head = _git(repository, "rev-parse", "HEAD")
    (repository / "untracked.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ArenaError, match="uncommitted changes"):
        branch_export.export_agent_branch(
            repository_path=repository,
            run_id=RUN_ID,
            agent_name="codex",
        )

    assert _git(repository, "rev-parse", "HEAD") == checked_out_head
    assert (repository / "untracked.txt").read_text(encoding="utf-8") == "keep me\n"


def test_export_rejects_manifest_patch_path_outside_expected_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _base_sha, _patch, artifacts = _prepare_export(tmp_path, monkeypatch)
    manifest_path = artifacts / RUN_ID / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["agents"][0]["patch_file"] = "../outside.patch"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ArenaError, match="unexpected patch path"):
        branch_export.export_agent_branch(
            repository_path=repository,
            run_id=RUN_ID,
            agent_name="codex",
        )


def test_export_rejects_a_patch_that_cannot_be_applied_and_cleans_up_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, base_sha, _patch = _repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    _run_artifact(artifacts, repository, base_sha, "not a git patch\n")
    monkeypatch.setattr(branch_export, "run_artifacts_directory", lambda _repo: artifacts)

    with pytest.raises(ArenaError, match="git apply"):
        branch_export.export_agent_branch(
            repository_path=repository,
            run_id=RUN_ID,
            agent_name="codex",
        )

    assert _git(repository, "rev-parse", "HEAD") == base_sha
    assert (
        sum(
            line.startswith("worktree ")
            for line in _git(repository, "worktree", "list", "--porcelain").splitlines()
        )
        == 1
    )
    assert _git(repository, "branch", "--list", "arena/*") == ""
