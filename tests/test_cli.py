from typer.testing import CliRunner

from agent_arena import __version__
from agent_arena.cli import app


def test_version_flag_works_without_a_command() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"Agent Arena {__version__}" in result.stdout
