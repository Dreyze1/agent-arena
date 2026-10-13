"""Command-line entry point for Agent Arena."""

import json
import os
import re
import shlex
import stat
import tempfile
import uuid
import webbrowser
from contextlib import suppress
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent_arena import __version__
from agent_arena.adapters import available_adapter_descriptors
from agent_arena.benchmark import benchmark_artifacts_directory, run_benchmark
from agent_arena.benchmark_report import create_benchmark_report
from agent_arena.branch_export import export_agent_branch
from agent_arena.demo import run_demo
from agent_arena.errors import ArenaError, BenchmarkCancelled
from agent_arena.report import create_report
from agent_arena.runner import (
    AgentOutcome,
    resolve_repository,
    run_arena,
    run_artifacts_directory,
)

app = typer.Typer(
    name="arena",
    help="Run coding agents on the same task and compare their patches.",
    invoke_without_command=True,
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _quote_cli_path(path: Path) -> str:
    """Quote a path for the shell used by the current platform."""
    value = str(path)
    return f'"{value}"' if os.name == "nt" else shlex.quote(value)


def _is_symlink_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        path_stat = path.lstat()
    except OSError:
        return False
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_point and getattr(path_stat, "st_file_attributes", 0) & reparse_point)


def _resolve_artifact_root(path: Path) -> Path:
    """Reject linked artifact directories before resolving any of their components."""
    temp_root = Path(tempfile.gettempdir()).absolute()
    artifact_root = path.expanduser().absolute()
    try:
        components = artifact_root.relative_to(temp_root).parts
    except ValueError as error:
        raise ArenaError("Artifact history directory is outside temporary storage.") from error

    current = temp_root
    for component in components:
        current = current / component
        if _is_symlink_or_junction(current):
            raise ArenaError(f"Artifact history path contains a symlink or junction: {current}")
    return artifact_root.resolve()


CONFIG_TEMPLATE = """\
version = 1

# Optional commands run after each agent finishes:
# checks = [["python", "-m", "pytest", "-q"], ["ruff", "check", "."]]

# Commands run directly without a shell in their own Git worktree.
# A worktree is not an operating-system security sandbox.
[agents.codex]
command = ["codex", "exec", "--full-auto", "{task}"]
# Set variable names here only when this provider CLI needs an environment key:
# inherit_env = ["OPENAI_API_KEY"]

[agents.claude]
command = ["claude", "-p", "{task}"]
# inherit_env = ["ANTHROPIC_API_KEY"]

# Installed provider adapters can replace command templates. Run `arena adapters` to list them.
# [agents.custom]
# adapter = "provider-name"
# inherit_env = ["PROVIDER_API_KEY"]
# [agents.custom.options]
# model = "model-name"

# Optional Docker execution. The image must already exist locally and contain the agent CLI.
# Network is disabled by default; use "bridge" only when the agent needs internet access.
# [agents.codex.container]
# image = "ghcr.io/your-org/agent-arena-codex:latest"
# network = "none"
# memory = "4g"
# cpus = 2.0
# pids_limit = 256
# user = "1000:1000"
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


@app.command("adapters")
def list_adapters() -> None:
    """List the built-in command adapter and installed provider plugins."""
    table = Table(title="Agent Arena adapters")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Package")
    table.add_column("Version")
    for descriptor in available_adapter_descriptors():
        table.add_row(
            descriptor.name,
            "built-in" if descriptor.name == "command" else "plugin",
            descriptor.distribution,
            descriptor.version,
        )
    console.print(table)


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
    if config_path.is_symlink():
        console.print(
            f"[red]Refusing to write through a symlinked config path:[/red] {config_path}"
        )
        raise typer.Exit(code=1)
    if config_path.exists() and not force:
        console.print(
            f"[yellow]Config already exists:[/yellow] {config_path}\nUse --force to replace it."
        )
        raise typer.Exit(code=1)

    temporary_path: Path | None = None
    try:
        if force:
            temporary_candidate = config_path.with_name(
                f".{config_path.name}.{uuid.uuid4().hex}.tmp"
            )
            with temporary_candidate.open("x", encoding="utf-8", newline="\n") as destination:
                temporary_path = temporary_candidate
                destination.write(CONFIG_TEMPLATE)
            os.replace(temporary_path, config_path)
            temporary_path = None
        else:
            with config_path.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(CONFIG_TEMPLATE)
    except FileExistsError as error:
        if config_path.is_symlink():
            console.print(
                f"[red]Refusing to write through a symlinked config path:[/red] {config_path}"
            )
            raise typer.Exit(code=1) from error
        if config_path.exists() and not force:
            console.print(
                f"[yellow]Config already exists:[/yellow] {config_path}\nUse --force to replace it."
            )
            raise typer.Exit(code=1) from error
        console.print(f"[red]Could not create temporary config file:[/red] {error}")
        raise typer.Exit(code=2) from error
    except OSError as error:
        console.print(f"[red]Could not create config:[/red] {error}")
        raise typer.Exit(code=2) from error
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
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
        max=86400,
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
    console.print(f"Run ID: [cyan]{run_dir.name}[/cyan]")
    console.print(f"Base commit: [cyan]{base_sha[:12]}[/cyan]")
    console.print(f"Artifacts: [cyan]{run_dir}[/cyan]")
    if keep_worktrees:
        console.print("Worktrees were kept in the system temporary directory.")
        for outcome in outcomes:
            if outcome.workspace:
                console.print(f"  {outcome.name}: {outcome.workspace}")
    console.print(
        "[yellow]Review patches and logs before sharing them; run artifacts can contain "
        "prompts and repository data.[/yellow]"
    )


@app.command()
def runs(
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-C",
        help="Git repository whose local run history to list.",
        file_okay=False,
        dir_okay=True,
    ),
) -> None:
    """List local Agent Arena runs for a repository."""
    try:
        repository = resolve_repository(repo)
    except ArenaError as error:
        console.print(f"[red]Cannot list runs:[/red] {error}")
        raise typer.Exit(code=2) from error

    runs_dir = run_artifacts_directory(repository)
    if not runs_dir.is_dir():
        console.print("No runs found. Start one with arena run.")
        return

    table = Table(title=f"Agent Arena runs · {repository.name}")
    table.add_column("Run ID", style="cyan")
    table.add_column("Created")
    table.add_column("Agents", justify="right")
    table.add_column("Base commit")
    found = 0
    for run_dir in sorted(runs_dir.iterdir(), key=lambda item: item.name, reverse=True):
        if run_dir.is_symlink() or not run_dir.is_dir():
            continue
        manifest_path = run_dir / "run.json"
        if manifest_path.is_symlink():
            continue
        try:
            with manifest_path.open("rb") as source:
                raw_manifest = source.read(1_000_001)
            if len(raw_manifest) > 1_000_000:
                continue
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        agents = manifest.get("agents", [])
        if not isinstance(agents, list):
            agents = []
        created = str(manifest.get("created_at", "")).replace("T", " ")[:19]
        base_sha = str(manifest.get("base_sha", ""))[:12]
        table.add_row(run_dir.name, created, str(len(agents)), base_sha)
        found += 1

    if not found:
        console.print("No complete runs found. Start one with arena run.")
        return
    console.print(table)
    console.print("Open a report with: arena report RUN_ID --repo PATH --open")


@app.command()
def report(
    run_id: str = typer.Argument(..., help="Run ID shown by arena run or arena runs."),
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-C",
        help="Git repository that owns the run.",
        file_okay=False,
        dir_okay=True,
    ),
    open_report: bool = typer.Option(
        False,
        "--open",
        help="Open the generated report in your default browser.",
    ),
) -> None:
    """Generate an offline side-by-side HTML report for a run."""
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", run_id):
        console.print("[red]Invalid run ID format.[/red]")
        raise typer.Exit(code=2)

    try:
        repository = resolve_repository(repo)
        runs_dir = _resolve_artifact_root(run_artifacts_directory(repository))
        run_dir = runs_dir / run_id
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise ArenaError(f"Run not found: {run_id}")
        run_dir.resolve().relative_to(runs_dir)
        report_path = create_report(run_dir)
    except (ArenaError, ValueError) as error:
        console.print(f"[red]Cannot create report:[/red] {error}")
        raise typer.Exit(code=2) from error

    console.print(f"Report: [cyan]{report_path}[/cyan]")
    if open_report:
        webbrowser.open(report_path.as_uri())


@app.command()
def benchmark_report(
    benchmark_id: str = typer.Argument(..., help="Benchmark ID shown by arena benchmark."),
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-C",
        help="Git repository that owns the benchmark history.",
        file_okay=False,
        dir_okay=True,
    ),
    open_report: bool = typer.Option(
        False,
        "--open",
        help="Open the generated scorecard in your default browser.",
    ),
) -> None:
    """Generate an offline scorecard for a saved benchmark, including partial results."""
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", benchmark_id):
        console.print("[red]Invalid benchmark ID format.[/red]")
        raise typer.Exit(code=2)

    try:
        repository = resolve_repository(repo)
        benchmarks_dir = _resolve_artifact_root(benchmark_artifacts_directory(repository))
        benchmark_dir = benchmarks_dir / benchmark_id
        if benchmark_dir.is_symlink() or not benchmark_dir.is_dir():
            raise ArenaError(f"Benchmark not found: {benchmark_id}")
        benchmark_dir.resolve().relative_to(benchmarks_dir)
        manifest_path = benchmark_dir / "benchmark.json"
        if manifest_path.is_symlink():
            raise ArenaError(f"Benchmark manifest is a symlink: {manifest_path}")
        scorecard_path = create_benchmark_report(manifest_path)
    except (ArenaError, ValueError) as error:
        console.print(f"[red]Cannot create benchmark scorecard:[/red] {error}")
        raise typer.Exit(code=2) from error

    console.print(f"Scorecard: [cyan]{scorecard_path}[/cyan]")
    if open_report:
        webbrowser.open(scorecard_path.as_uri())


@app.command()
def benchmark(
    suite: Path = typer.Option(
        ...,
        "--suite",
        help="Versioned TOML benchmark suite, relative to the target repository by default.",
        dir_okay=False,
    ),
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-C",
        help="Git repository to benchmark.",
        file_okay=False,
        dir_okay=True,
    ),
    config: Path = typer.Option(
        Path(".agent-arena.toml"),
        "--config",
        help="Agent profile TOML configuration.",
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
        max=86400,
        help="Maximum seconds per agent and check command.",
    ),
    keep_worktrees: bool = typer.Option(
        False,
        "--keep-worktrees",
        help="Keep every benchmark worktree for inspection.",
    ),
) -> None:
    """Run a repeatable task suite against selected coding agents."""
    try:
        manifest_path, manifest = run_benchmark(
            repository_path=repo,
            suite_path=suite,
            config_path=config,
            selected_agents=agent or None,
            timeout_seconds=timeout,
            keep_worktrees=keep_worktrees,
        )
    except BenchmarkCancelled as error:
        console.print(
            Text(
                f"Benchmark cancelled. Partial results are at {error.manifest_path}.",
                no_wrap=True,
            ),
            soft_wrap=True,
        )
        console.print(
            Text(
                "Create a scorecard for the saved partial results with: "
                f"arena benchmark-report {error.benchmark_id} --repo {_quote_cli_path(repo)}",
                no_wrap=True,
            ),
            soft_wrap=True,
        )
        raise typer.Exit(code=130) from error
    except ArenaError as error:
        console.print(f"[red]Cannot run benchmark:[/red] {error}")
        partial_id = re.search(
            r"Benchmark (\d{8}T\d{6}Z-[0-9a-f]{8}) stopped",
            str(error),
        )
        if partial_id:
            console.print(
                Text(
                    "Create a scorecard for the saved partial results with: "
                    f"arena benchmark-report {partial_id.group(1)} --repo {_quote_cli_path(repo)}",
                    no_wrap=True,
                ),
                soft_wrap=True,
            )
        raise typer.Exit(code=2) from error

    try:
        scorecard_path = create_benchmark_report(manifest_path)
    except ArenaError as error:
        console.print(
            f"[red]Benchmark finished, but its scorecard could not be created:[/red] {error}"
        )
        console.print(
            Text(
                f"Results remain at {manifest_path}. Retry with: "
                f"arena benchmark-report {manifest['benchmark_id']} --repo {_quote_cli_path(repo)}",
                no_wrap=True,
            ),
            soft_wrap=True,
        )
        raise typer.Exit(code=2) from error

    table = Table(title=f"Benchmark · {manifest['suite_name']}")
    table.add_column("Agent", style="bold")
    table.add_column("Verified passes")
    table.add_column("Agent success")
    table.add_column("Check coverage")
    table.add_column("Avg. time", justify="right")
    for summary in manifest["summary"]:
        pass_rate = summary["verified_pass_rate"]
        verified = (
            "not checked"
            if pass_rate is None
            else f"{summary['verified_passes']}/{summary['verified_attempts']} ({pass_rate:.0%})"
        )
        table.add_row(
            summary["agent"],
            verified,
            f"{summary['agent_successes']}/{summary['attempts']}",
            f"{summary['verification_coverage']:.0%}",
            f"{summary['mean_duration_seconds']:.1f}s",
        )
    console.print(table)
    console.print(f"Status: [cyan]{manifest['status']}[/cyan]")
    console.print(f"Benchmark ID: [cyan]{manifest['benchmark_id']}[/cyan]")
    console.print(f"Base commit: [cyan]{manifest['base_sha'][:12]}[/cyan]")
    console.print(f"Results: [cyan]{manifest_path}[/cyan]")
    console.print(f"CSV summary: [cyan]{manifest_path.with_name('summary.csv')}[/cyan]")
    console.print(f"HTML scorecard: [cyan]{scorecard_path}[/cyan]")
    console.print(
        "[yellow]A verified pass requires a successful agent command and all configured checks. "
        "Runs without checks are reported as unverified.[/yellow]"
    )


@app.command()
def demo(
    open_report: bool = typer.Option(
        False,
        "--open",
        help="Open the generated offline scorecard in your default browser.",
    ),
) -> None:
    """Run a deterministic, offline benchmark using two built-in mock agents."""
    try:
        result = run_demo()
    except ArenaError as error:
        console.print(f"[red]Cannot run offline demo:[/red] {error}")
        raise typer.Exit(code=2) from error

    table = Table(title="Agent Arena demo - local mock agents")
    table.add_column("Agent", style="bold")
    table.add_column("Verified passes")
    table.add_column("Agent successes")
    table.add_column("Check failures")
    for summary in result.summary:
        pass_rate = summary["verified_pass_rate"]
        verified = (
            "not checked"
            if pass_rate is None
            else f"{summary['verified_passes']}/{summary['verified_attempts']}"
        )
        table.add_row(
            str(summary["agent"]),
            verified,
            f"{summary['agent_successes']}/{summary['attempts']}",
            str(summary["check_failure_attempts"]),
        )
    console.print(table)
    console.print(Text(f"Demo workspace: {result.workspace}", no_wrap=True), soft_wrap=True)
    console.print(Text(f"Temporary repository: {result.repository}", no_wrap=True), soft_wrap=True)
    console.print(f"Benchmark ID: [cyan]{result.benchmark_id}[/cyan]")
    console.print(Text(f"Scorecard: {result.scorecard_path}", no_wrap=True), soft_wrap=True)
    for report_path in result.run_report_paths:
        console.print(Text(f"Run report: {report_path}", no_wrap=True), soft_wrap=True)
    if open_report:
        webbrowser.open(result.scorecard_path.as_uri())


@app.command()
def export(
    run_id: str = typer.Argument(..., help="Run ID shown by arena run or arena runs."),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent whose patch to export."),
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-C",
        help="Git repository that owns the run.",
        file_okay=False,
        dir_okay=True,
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Local branch name; defaults to arena/RUN_ID/AGENT.",
    ),
) -> None:
    """Export one saved agent patch as a local branch commit."""
    try:
        result = export_agent_branch(
            repository_path=repo,
            run_id=run_id,
            agent_name=agent,
            branch_name=branch,
        )
    except ArenaError as error:
        console.print(f"[red]Cannot export patch:[/red] {error}")
        raise typer.Exit(code=2) from error

    if result.timed_out or result.returncode != 0:
        console.print(
            "[yellow]The selected agent did not finish successfully; "
            "its partial patch was exported as requested.[/yellow]"
        )
    if result.checks_total:
        console.print(f"Checks: {result.checks_passed}/{result.checks_total} passed")
        if result.checks_passed != result.checks_total:
            console.print("[yellow]Review failed checks before using this branch.[/yellow]")
    else:
        console.print("[yellow]No checks were recorded for this result.[/yellow]")
    console.print(f"Created branch: [cyan]{result.branch_name}[/cyan]")
    console.print(f"Commit: [cyan]{result.commit_sha}[/cyan]")
    console.print(f"Based on: [cyan]{result.base_sha[:12]}[/cyan]")
    console.print("The current checkout was left unchanged. Push the branch when you are ready.")
    if result.cleanup_warning:
        console.print(f"[yellow]{result.cleanup_warning}[/yellow]")


def _outcome_label(outcome: AgentOutcome) -> str:
    if outcome.timed_out:
        return "timed out"
    return "finished" if outcome.returncode == 0 else "failed"


def _checks_label(outcome: AgentOutcome) -> str:
    if not outcome.checks:
        return "none"
    passed = sum(check["returncode"] == 0 and not check["timed_out"] for check in outcome.checks)
    return f"{passed}/{len(outcome.checks)} passed"
