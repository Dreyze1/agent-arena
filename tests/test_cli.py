import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from agent_arena import __version__, cli
from agent_arena.cli import app


def test_version_flag_works_without_a_command() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"Agent Arena {__version__}" in result.stdout


def test_init_creates_starter_config(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    result = CliRunner().invoke(app, ["init", "--path", str(repository)])

    assert result.exit_code == 0, result.stdout
    assert (repository / ".agent-arena.toml").read_text(encoding="utf-8").startswith("version = 1")


def test_init_force_replaces_regular_config(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    config_path = repository / ".agent-arena.toml"
    config_path.write_text("old config\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["init", "--path", str(repository), "--force"])

    assert result.exit_code == 0, result.stdout
    assert config_path.read_text(encoding="utf-8").startswith("version = 1")


def test_init_force_does_not_follow_symlinked_config(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    external_file = tmp_path / "outside.txt"
    external_file.write_text("preserve this file\n", encoding="utf-8")
    config_path = repository / ".agent-arena.toml"
    try:
        config_path.symlink_to(external_file)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"This platform cannot create a test symlink: {error}")

    result = CliRunner().invoke(app, ["init", "--path", str(repository), "--force"])

    assert result.exit_code == 1
    assert config_path.is_symlink()
    assert external_file.read_text(encoding="utf-8") == "preserve this file\n"


def test_init_force_refuses_a_symlinked_config_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    config_path = repository / ".agent-arena.toml"
    config_path.write_text("keep this config\n", encoding="utf-8")
    is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == config_path or is_symlink(path),
    )

    result = CliRunner().invoke(app, ["init", "--path", str(repository), "--force"])

    assert result.exit_code == 1
    assert config_path.read_text(encoding="utf-8") == "keep this config\n"


def test_init_force_preserves_existing_temporary_path_on_name_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    config_path = repository / ".agent-arena.toml"
    config_path.write_text("old config\n", encoding="utf-8")
    collision_path = repository / "..agent-arena.toml.fixed.tmp"
    collision_path.write_text("keep this file\n", encoding="utf-8")
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))

    result = CliRunner().invoke(app, ["init", "--path", str(repository), "--force"])

    assert result.exit_code == 2
    assert config_path.read_text(encoding="utf-8") == "old config\n"
    assert collision_path.read_text(encoding="utf-8") == "keep this file\n"


@pytest.mark.parametrize(
    ("command", "directory_name", "artifact_id", "linked_component"),
    [
        ("report", "runs", "20261013T000000Z-12345678", "root"),
        ("report", "runs", "20261013T000000Z-12345678", "parent"),
        ("benchmark-report", "benchmarks", "20261013T000000Z-12345678", "root"),
        ("benchmark-report", "benchmarks", "20261013T000000Z-12345678", "parent"),
    ],
)
def test_report_commands_reject_linked_artifact_roots(
    command: str,
    directory_name: str,
    artifact_id: str,
    linked_component: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    artifact_root = temp_root / "agent-arena" / "repo-key" / directory_name

    monkeypatch.setattr(cli, "resolve_repository", lambda _path: repository)
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(temp_root))
    if command == "report":
        monkeypatch.setattr(cli, "run_artifacts_directory", lambda _repo: artifact_root)
    else:
        monkeypatch.setattr(cli, "benchmark_artifacts_directory", lambda _repo: artifact_root)

    linked_path = artifact_root if linked_component == "root" else artifact_root.parents[1]
    is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked_path or is_symlink(path),
    )
    monkeypatch.setattr(cli, "create_report", lambda _path: pytest.fail("report was generated"))
    monkeypatch.setattr(
        cli,
        "create_benchmark_report",
        lambda _path: pytest.fail("scorecard was generated"),
    )

    result = CliRunner().invoke(app, [command, artifact_id, "--repo", str(repository)])

    assert result.exit_code == 2
    assert "symlink" in result.stdout.lower() or "junction" in result.stdout.lower()


def test_adapters_command_lists_builtin_and_plugin_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_arena.adapters import AdapterDescriptor

    monkeypatch.setattr(
        cli,
        "available_adapter_descriptors",
        lambda: (
            AdapterDescriptor("command", "agent-arena", "0.1.0", ""),
            AdapterDescriptor("provider", "arena-provider", "1.2.3", "module:Adapter"),
        ),
    )

    result = CliRunner().invoke(app, ["adapters"])

    assert result.exit_code == 0, result.stdout
    assert "command" in result.stdout
    assert "built-in" in result.stdout
    assert "provider" in result.stdout
    assert "arena-provider" in result.stdout
    assert "1.2.3" in result.stdout


def test_run_command_reports_results_and_kept_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "20261014T000000Z-12345678"
    base_sha = "a" * 40
    outcome = SimpleNamespace(
        name="mock",
        timed_out=False,
        returncode=0,
        changed_files=2,
        duration_seconds=1.25,
        checks=[{"returncode": 0, "timed_out": False}, {"returncode": 1, "timed_out": False}],
        workspace=str(tmp_path / "worktree"),
    )
    calls: list[dict[str, object]] = []

    def run_arena(**options: object):
        calls.append(options)
        return run_dir, base_sha, [outcome]

    monkeypatch.setattr(cli, "run_arena", run_arena)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--task",
            "Fix the parser",
            "--repo",
            str(tmp_path),
            "--agent",
            "mock",
            "--timeout",
            "60",
            "--keep-worktrees",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "finished" in result.stdout
    assert "1/2 passed" in result.stdout
    assert "Worktrees were kept" in result.stdout
    assert "Base commit:" in result.stdout
    assert calls[0]["selected_agents"] == ["mock"]
    assert calls[0]["timeout_seconds"] == 60
    assert calls[0]["keep_worktrees"] is True


def test_run_command_reports_arena_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_run(**_options: object):
        raise cli.ArenaError("the repository is dirty")

    monkeypatch.setattr(cli, "run_arena", fail_run)

    result = CliRunner().invoke(app, ["run", "--task", "Fix it", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "Cannot run arena" in result.stdout
    assert "repository is dirty" in result.stdout


def test_runs_command_lists_valid_manifests_and_skips_invalid_ones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    runs_dir = tmp_path / "run-history"
    valid_id = "20261014T000000Z-12345678"
    valid_run = runs_dir / valid_id
    valid_run.mkdir(parents=True)
    (valid_run / "run.json").write_text(
        json.dumps(
            {
                "created_at": "2026-10-14T09:00:00+00:00",
                "base_sha": "b" * 40,
                "agents": [{"name": "mock"}],
            }
        ),
        encoding="utf-8",
    )
    invalid_run = runs_dir / "invalid"
    invalid_run.mkdir()
    (invalid_run / "run.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(cli, "resolve_repository", lambda _path: repository)
    monkeypatch.setattr(cli, "run_artifacts_directory", lambda _repo: runs_dir)

    result = CliRunner().invoke(app, ["runs", "--repo", str(repository)])

    assert result.exit_code == 0, result.stdout
    assert valid_id in result.stdout
    assert "2026-10-14 09:00:00" in result.stdout
    assert "Open a report with" in result.stdout
    assert "invalid" not in result.stdout


def test_report_command_generates_and_opens_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    run_id = "20261014T000000Z-12345678"
    runs_dir = tmp_path / "agent-arena" / "repo-key" / "runs"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    report_path = tmp_path / "report.html"
    opened: list[str] = []
    monkeypatch.setattr(cli, "resolve_repository", lambda _path: repository)
    monkeypatch.setattr(cli, "run_artifacts_directory", lambda _repo: runs_dir)
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "create_report", lambda _run_dir: report_path)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    result = CliRunner().invoke(
        app,
        ["report", run_id, "--repo", str(repository), "--open"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Report:" in result.stdout
    assert opened == [report_path.as_uri()]


def test_demo_command_renders_scores_and_opens_the_scorecard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scorecard = tmp_path / "scorecard.html"
    demo_result = SimpleNamespace(
        summary=[
            {
                "agent": "mock-pass",
                "verified_pass_rate": 1.0,
                "verified_passes": 1,
                "verified_attempts": 1,
                "agent_successes": 1,
                "attempts": 1,
                "check_failure_attempts": 0,
            },
            {
                "agent": "mock-unchecked",
                "verified_pass_rate": None,
                "verified_passes": 0,
                "verified_attempts": 0,
                "agent_successes": 1,
                "attempts": 1,
                "check_failure_attempts": 0,
            },
        ],
        workspace=tmp_path / "workspace",
        repository=tmp_path / "repository",
        benchmark_id="20261014T000000Z-12345678",
        scorecard_path=scorecard,
        run_report_paths=[tmp_path / "run-report.html"],
    )
    opened: list[str] = []
    monkeypatch.setattr(cli, "run_demo", lambda: demo_result)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    result = CliRunner().invoke(app, ["demo", "--open"])

    assert result.exit_code == 0, result.stdout
    assert "mock-pass" in result.stdout
    assert "mock-unchecked" in result.stdout
    assert "not checked" in result.stdout
    assert "Benchmark ID:" in result.stdout
    assert "Run report:" in result.stdout
    assert opened == [scorecard.as_uri()]


def test_export_command_warns_when_exporting_an_unverified_partial_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_result = SimpleNamespace(
        timed_out=True,
        returncode=124,
        checks_total=2,
        checks_passed=1,
        branch_name="arena/example/mock",
        commit_sha="c" * 40,
        base_sha="d" * 40,
        cleanup_warning="Temporary worktree cleanup needs attention.",
    )
    calls: list[dict[str, object]] = []

    def export_agent_branch(**options: object):
        calls.append(options)
        return export_result

    monkeypatch.setattr(cli, "export_agent_branch", export_agent_branch)

    result = CliRunner().invoke(
        app,
        ["export", "20261014T000000Z-12345678", "--agent", "mock", "--repo", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert "did not finish successfully" in result.stdout
    assert "Checks: 1/2 passed" in result.stdout
    assert "Review failed checks" in result.stdout
    assert "Created branch: arena/example/mock" in result.stdout
    assert "cleanup needs attention" in result.stdout
    assert calls[0]["agent_name"] == "mock"
    assert calls[0]["branch_name"] is None
