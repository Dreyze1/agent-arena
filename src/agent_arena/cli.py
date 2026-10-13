"""Command-line entry point for Agent Arena."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agent_arena import __version__
from agent_arena.runner import ArenaError, AgentOutcome, run_arena

app = typer.Typer(
    name="arena",
    help="Run coding agents on the same task and compare their patches.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

CONFIG_TEMPLATE = """\
version = 1

# Optional commands run after each agent finishes:
# checks = [["python", "-m", "pytest", "-q"], ["ruff", "check", "."]]

# Commands run directly without a shell in their own Git worktree.
# A worktree is not an operating-system security sandbox.
[agents.codex]
command = ["codex", "exec", "--full-auto", "{task}"]

[agents.claude]
command = ["claude", "-p", "{task}"]
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


@app.command()
def run(
    task: str = typer.Option(
        ...,
        "--task",
        "-t",
        help="Task prompt shared by all selected agents.",
    ),
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-C",
        help="Git repository to run against.",
        file_okay=False,
        dir_okay=True,
    ),
    config: Path = typer.Option(
        Path(".agent-arena.toml"),
        "--config",
        help="Agent Arena TOML configuration.",
        dir_okay=False,
    ),
    agent: list[str] = typer.Option(
        [],
        "--agent",
        "-a",
        help="Agent profile to run; repeat to select more than one.",
    ),
    timeout: int = typer.Option(
        1800,
        "--timeout",
        min=1,
        help="Maximum seconds per agent and check command.",
    ),
    keep_worktrees: bool = typer.Option(
        False,
        "--keep-worktrees",
        help="Keep worktrees in the system temporary directory for inspection.",
    ),
) -> None:
    """Run configured agents against the same clean Git revision."""
    try:
        run_dir, base_sha, outcomes = run_arena(
            repository_path=repo,
            config_path=config,
            task=task,
            selected_agents=agent or None,
            timeout_seconds=timeout,
            keep_worktrees=keep_worktrees,
        )
    except ArenaError as error:
        console.print(f"[red]Cannot run arena:[/red] {error}")
        raise typer.Exit(code=2) from error

    table = Table(title="Agent Arena results")
    table.add_column("Agent", style="bold")
    table.add_column("Result")
    table.add_column("Changed files", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Checks")
    table.add_column("Exit", justify="right")

    for outcome in outcomes:
        table.add_row(
            outcome.name,
            _outcome_label(outcome),
            str(outcome.changed_files),
            f"{outcome.duration_seconds:.1f}s",
            _checks_label(outcome),
            str(outcome.returncode),
        )
    console.print(table)
    console.print(f"Base commit: [cyan]{base_sha[:12]}[/cyan]")
    console.print(f"Artifacts: [cyan]{run_dir}[/cyan]")
    if keep_worktrees:
        console.print("Worktrees were kept in the system temporary directory.")
    console.print(
        "[yellow]Review patches and logs before sharing them; run artifacts can contain "
        "prompts and repository data.[/yellow]"
    )


def _outcome_label(outcome: AgentOutcome) -> str:
    if outcome.timed_out:
        return "timed out"
    return "finished" if outcome.returncode == 0 else "failed"


def _checks_label(outcome: AgentOutcome) -> str:
    if not outcome.checks:
        return "none"
    passed = sum(check["returncode"] == 0 and not check["timed_out"] for check in outcome.checks)
    return f"{passed}/{len(outcome.checks)} passed"
