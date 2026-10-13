"""Command-line entry point for Agent Arena."""

from pathlib import Path

import typer
from rich.console import Console

from agent_arena import __version__

app = typer.Typer(
    name="arena",
    help="Run coding agents on the same task and compare their patches.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

CONFIG_TEMPLATE = """\
version = 1

# Commands are examples for the planned runner. Agent Arena does not execute
# them yet; the current CLI only creates this starter file.
[agents.codex]
command = ["codex", "exec", "--full-auto", "{task}"]

[agents.claude]
command = ["claude", "-p", "{task}"]

# Add checks once the run engine is available:
# checks = [["python", "-m", "pytest"], ["ruff", "check", "."]]
"""


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the installed version and exit.",
    ),
) -> None:
    """Compare coding agents with repeatable local runs."""
    if version:
        console.print(f"Agent Arena {__version__}")
        raise typer.Exit()


@app.command()
def init(
    path: Path = typer.Option(
        Path("."),
        "--path",
        "-p",
        help="Repository directory where the starter config will be created.",
        file_okay=False,
        dir_okay=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing .agent-arena.toml file.",
    ),
) -> None:
    """Create an Agent Arena starter config in a repository."""
    repository = path.expanduser().resolve()
    if not repository.is_dir():
        raise typer.BadParameter(f"Directory does not exist: {repository}", param_hint="--path")

    config_path = repository / ".agent-arena.toml"
    if config_path.exists() and not force:
        console.print(
            f"[yellow]Config already exists:[/yellow] {config_path}\n"
            "Use --force to replace it."
        )
        raise typer.Exit(code=1)

    config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    console.print(f"[green]Created[/green] {config_path}")
