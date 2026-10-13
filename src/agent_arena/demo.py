"""Offline, deterministic demo benchmark using a disposable Git fixture."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_arena.benchmark import run_benchmark
from agent_arena.benchmark_report import create_benchmark_report
from agent_arena.errors import ArenaError
from agent_arena.git_utils import git_subprocess_environment
from agent_arena.report import create_report
from agent_arena.runner import run_artifacts_directory


@dataclass(frozen=True)
class DemoResult:
    """Paths and aggregate outcomes produced by one local demo run."""

    workspace: Path
    repository: Path
    benchmark_id: str
    scorecard_path: Path
    run_report_paths: tuple[Path, ...]
    summary: tuple[dict[str, Any], ...]


_BASELINE_APP = """\
def is_even(value: int) -> bool:
    raise NotImplementedError("Implement is_even")
"""

_MOCK_AGENT = """\
from pathlib import Path
import sys

mode, worktree_arg = sys.argv[1:]
if mode not in {"pass", "check-fail"}:
    raise SystemExit("unknown demo agent mode")
worktree = Path(worktree_arg).resolve()
target = worktree / "demo_app.py"
if not worktree.is_dir() or not target.is_file():
    raise SystemExit("the demo worktree is missing its fixture")

if mode == "pass":
    implementation = "def is_even(value: int) -> bool:\\n    return value % 2 == 0\\n"
else:
    implementation = "def is_even(value: int) -> bool:\\n    return value % 2 == 1\\n"
target.write_text(implementation, encoding="utf-8")
print(f"Mock agent {mode} updated demo_app.py")
"""

_CHECK_SCRIPT = """\
import sys
from pathlib import Path

worktree = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(worktree))
from demo_app import is_even

assert is_even(2) is True, "2 should be even"
assert is_even(3) is False, "3 should not be even"
print("demo checks passed")
"""

_SUITE_PROMPT = (
    "Implement is_even(value) in demo_app.py so it returns True for even integers and False "
    "for odd integers."
)


def _write_text(path: Path, contents: str) -> None:
    try:
        path.write_text(contents, encoding="utf-8", newline="\n")
    except OSError as error:
        raise ArenaError(f"Could not create demo fixture file {path}: {error}") from error


def _run_git(repository: Path, *arguments: str) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=git_subprocess_environment(),
        )
    except FileNotFoundError as error:
        raise ArenaError("Git was not found. Install Git and make sure it is on PATH.") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ArenaError(f"Could not prepare the offline demo repository: {detail}")


def _toml_array(values: list[str]) -> str:
    """Encode a string array using JSON escapes, which are valid TOML basic strings."""
    return json.dumps(values, ensure_ascii=True)


def _prepare_fixture(workspace: Path) -> tuple[Path, Path, Path]:
    repository = workspace / "fixture-repository"
    repository.mkdir()
    git_template = workspace / "empty-git-template"
    hooks_directory = git_template / "hooks"
    hooks_directory.mkdir(parents=True)
    empty_attributes = workspace / "empty-gitattributes"
    empty_attributes.touch()

    mock_agent = workspace / "mock_agent.py"
    check_script = workspace / "check_demo.py"
    _write_text(mock_agent, _MOCK_AGENT)
    _write_text(check_script, _CHECK_SCRIPT)

    pass_command = [sys.executable, str(mock_agent), "pass", "{worktree}"]
    fail_command = [sys.executable, str(mock_agent), "check-fail", "{worktree}"]
    check_command = [sys.executable, str(check_script), "{worktree}"]
    config_contents = "\n".join(
        (
            "version = 1",
            "",
            "[agents.mock-pass]",
            f"command = {_toml_array(pass_command)}",
            "",
            "[agents.mock-check-fail]",
            f"command = {_toml_array(fail_command)}",
            "",
        )
    )
    suite_contents = "\n".join(
        (
            "version = 1",
            'name = "Agent Arena offline demo"',
            f"checks = [{_toml_array(check_command)}]",
            "",
            "[[tasks]]",
            'id = "even-check"',
            f"prompt = {json.dumps(_SUITE_PROMPT)}",
            "",
        )
    )

    _write_text(repository / "demo_app.py", _BASELINE_APP)
    _write_text(repository / ".agent-arena.toml", config_contents)
    suite_path = repository / "suite.toml"
    _write_text(suite_path, suite_contents)

    _run_git(repository, "init", "-q", f"--template={git_template}")
    _run_git(repository, "config", "core.hooksPath", str(hooks_directory))
    _run_git(repository, "config", "core.attributesFile", str(empty_attributes))
    _run_git(repository, "config", "commit.gpgSign", "false")
    _run_git(repository, "config", "user.name", "Agent Arena Demo")
    _run_git(repository, "config", "user.email", "agent-arena-demo@example.invalid")
    _run_git(repository, "add", "demo_app.py", ".agent-arena.toml", "suite.toml")
    _run_git(repository, "commit", "-q", "-m", "Create offline demo fixture")
    return repository, repository / ".agent-arena.toml", suite_path


def run_demo() -> DemoResult:
    """Run a real benchmark against a temporary fixture using only local mock commands."""
    try:
        workspace = Path(tempfile.mkdtemp(prefix="agent-arena-demo-"))
    except OSError as error:
        raise ArenaError(f"Could not create a temporary demo workspace: {error}") from error

    repository: Path | None = None
    benchmark_started = False
    try:
        repository, config_path, suite_path = _prepare_fixture(workspace)
        benchmark_started = True
        manifest_path, manifest = run_benchmark(
            repository_path=repository,
            suite_path=suite_path,
            config_path=config_path,
            selected_agents=["mock-pass", "mock-check-fail"],
            timeout_seconds=30,
            keep_worktrees=False,
        )
        scorecard_path = create_benchmark_report(manifest_path)
        report_paths: list[Path] = []
        runs_directory = run_artifacts_directory(repository)
        for run in manifest["runs"]:
            report_paths.append(create_report(runs_directory / run["run_id"]))
        return DemoResult(
            workspace=workspace,
            repository=repository,
            benchmark_id=manifest["benchmark_id"],
            scorecard_path=scorecard_path,
            run_report_paths=tuple(report_paths),
            summary=tuple(manifest["summary"]),
        )
    except Exception:
        if not benchmark_started:
            shutil.rmtree(workspace, ignore_errors=True)
        raise
