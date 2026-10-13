from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_arena.cli as cli
from agent_arena.benchmark import benchmark_artifacts_directory
from agent_arena.cli import app
from agent_arena.demo import DemoResult


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_demo_runs_real_offline_benchmark_without_changing_current_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "current-repository"
    repository.mkdir()
    _git("init", "-q", cwd=repository)
    _git(
        "-c",
        "user.name=Demo Test",
        "-c",
        "user.email=demo@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "base",
        cwd=repository,
    )
    marker = repository / "leave-me-alone.txt"
    marker.write_text("existing untracked file\n", encoding="utf-8")
    status_before = _git("status", "--porcelain=v1", "--untracked-files=all", cwd=repository)
    files_before = sorted(path.relative_to(repository).as_posix() for path in repository.rglob("*"))

    monkeypatch.chdir(repository)
    result = CliRunner().invoke(app, ["demo"])

    assert result.exit_code == 0, result.stdout
    assert "mock-pass" in result.stdout
    assert "mock-check-fail" in result.stdout
    assert "Verified passes" in result.stdout
    assert "check failures" in result.stdout.lower()

    workspace_match = re.search(r"Demo workspace: (.+)", result.stdout)
    assert workspace_match is not None
    workspace = Path(workspace_match.group(1).strip())
    assert workspace.is_dir()
    config_text = (workspace / "fixture-repository" / ".agent-arena.toml").read_text(
        encoding="utf-8"
    )
    assert "inherit_env" not in config_text
    assert "container" not in config_text
    assert "API_KEY" not in config_text

    scorecard_match = re.search(r"Scorecard: (.+)", result.stdout)
    assert scorecard_match is not None
    scorecard_path = Path(scorecard_match.group(1).strip())
    assert (
        scorecard_path
        == next(benchmark_artifacts_directory(workspace / "fixture-repository").glob("*"))
        / "report.html"
    )
    scorecard = scorecard_path.read_text(encoding="utf-8")
    assert "mock-pass" in scorecard
    assert "mock-check-fail" in scorecard
    assert "1/1" in scorecard

    report_matches = re.findall(r"Run report: (.+)", result.stdout)
    assert len(report_matches) == 1
    run_report = Path(report_matches[0].strip())
    assert run_report.is_file()
    assert "mock-pass" in run_report.read_text(encoding="utf-8")
    assert "mock-check-fail" in run_report.read_text(encoding="utf-8")

    assert marker.read_text(encoding="utf-8") == "existing untracked file\n"
    assert (
        _git("status", "--porcelain=v1", "--untracked-files=all", cwd=repository) == status_before
    )
    assert (
        sorted(path.relative_to(repository).as_posix() for path in repository.rglob("*"))
        == files_before
    )


def test_demo_ignores_git_directory_environment_and_preserves_that_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "git-dir-repository"
    repository.mkdir()
    _git("init", "-q", cwd=repository)
    _git(
        "-c",
        "user.name=Demo Test",
        "-c",
        "user.email=demo@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "base",
        cwd=repository,
    )
    config_path = repository / ".git" / "config"
    config_before = config_path.read_bytes()
    head_before = (repository / ".git" / "HEAD").read_bytes()

    monkeypatch.setenv("GIT_DIR", str(repository / ".git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    result = CliRunner().invoke(app, ["demo"])

    assert result.exit_code == 0, result.stdout
    assert config_path.read_bytes() == config_before
    assert (repository / ".git" / "HEAD").read_bytes() == head_before


def test_demo_ignores_git_config_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    result = CliRunner().invoke(app, ["demo"])

    assert result.exit_code == 0, result.stdout


def test_demo_open_opens_the_scorecard(tmp_path: Path, monkeypatch) -> None:
    scorecard_path = tmp_path / "scorecard.html"
    scorecard_path.write_text("demo", encoding="utf-8")
    result = DemoResult(
        workspace=tmp_path,
        repository=tmp_path / "repo",
        benchmark_id="20261013T120000Z-abcdef12",
        scorecard_path=scorecard_path,
        run_report_paths=(tmp_path / "run-report.html",),
        summary=(
            {
                "agent": "mock-pass",
                "verified_pass_rate": 1.0,
                "verified_passes": 1,
                "verified_attempts": 1,
                "agent_successes": 1,
                "attempts": 1,
                "check_failure_attempts": 0,
            },
        ),
    )
    monkeypatch.setattr(cli, "run_demo", lambda: result)
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    invocation = CliRunner().invoke(app, ["demo", "--open"])

    assert invocation.exit_code == 0, invocation.stdout
    assert opened == [scorecard_path.as_uri()]
