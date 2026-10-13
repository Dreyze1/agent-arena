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
