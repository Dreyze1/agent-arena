from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

import agent_arena.runner as runner
from agent_arena.container_runtime import ContainerProfile
from agent_arena.errors import ArenaError


def test_child_environment_filters_unrequested_variables_and_adds_arena_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("OPENAI_API_KEY", "selected-secret")
    monkeypatch.setenv("UNRELATED_TOKEN", "must-not-be-forwarded")

    environment = runner._child_environment(
        ("OPENAI_API_KEY",),
        {"ARENA_TASK": "Fix parser"},
    )

    assert environment["PATH"] == "safe-path"
    assert environment["OPENAI_API_KEY"] == "selected-secret"
    assert environment["ARENA_TASK"] == "Fix parser"
    assert "UNRELATED_TOKEN" not in environment


def test_child_environment_rejects_missing_requested_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARENA_TEST_MISSING_SECRET", raising=False)

    with pytest.raises(ArenaError, match="ARENA_TEST_MISSING_SECRET"):
        runner._child_environment(("ARENA_TEST_MISSING_SECRET",), {})


def test_secret_helpers_resolve_selected_values_and_container_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "selected-secret")
    profile = runner.AgentProfile(
        adapter_name="command",
        adapter_options={"command": ("agent",)},
        inherit_env=("OPENAI_API_KEY",),
        container=ContainerProfile("agent:local", "none", "1g", 1.0, 32, None),
    )
    environment = {
        "OPENAI_API_KEY": "selected-secret",
        "ARENA_TASK": "Fix parser",
        "PATH": "safe-path",
    }

    assert runner._secret_values(profile, environment) == ("selected-secret",)
    assert runner._container_environment_names(profile, environment) == (
        "ARENA_TASK",
        "OPENAI_API_KEY",
    )


def test_write_redacted_log_redacts_secrets_across_chunk_boundaries(tmp_path: Path) -> None:
    secret = b"secret-value"
    source = io.BytesIO(b"x" * (64 * 1024 - 5) + secret + b"-tail")
    destination = tmp_path / "stdout.log"

    runner._write_redacted_log(source, destination, (secret.decode("ascii"),))

    written = destination.read_bytes()
    assert secret not in written
    assert b"[REDACTED]" in written
    assert written.endswith(b"-tail")


def test_write_redacted_log_copies_output_when_no_secrets_are_selected(tmp_path: Path) -> None:
    data = b"agent output\n" * 10_000
    destination = tmp_path / "stdout.log"

    runner._write_redacted_log(io.BytesIO(data), destination, ())

    assert destination.read_bytes() == data


def test_run_command_captures_stdout_and_redacts_secrets(tmp_path: Path) -> None:
    stdout_path = tmp_path / "logs" / "agent.stdout.log"
    stderr_path = tmp_path / "logs" / "agent.stderr.log"

    returncode, timed_out, duration = runner._run_command(
        [sys.executable, "-c", "print('before-secret-value-after')"],
        cwd=tmp_path,
        environment=os.environ.copy(),
        timeout_seconds=10,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        redact_values=("secret-value",),
    )

    assert returncode == 0
    assert not timed_out
    assert duration >= 0
    assert stdout_path.read_text(encoding="utf-8") == "before-[REDACTED]-after\n"
    assert stderr_path.read_bytes() == b""


def test_run_command_records_start_failure_as_exit_127(tmp_path: Path) -> None:
    stdout_path = tmp_path / "agent.stdout.log"
    stderr_path = tmp_path / "agent.stderr.log"

    returncode, timed_out, _duration = runner._run_command(
        ["agent-arena-executable-that-does-not-exist"],
        cwd=tmp_path,
        environment=os.environ.copy(),
        timeout_seconds=10,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert returncode == 127
    assert not timed_out
    assert "Could not start" in stderr_path.read_text(encoding="utf-8")


def test_run_command_terminates_a_timed_out_process(tmp_path: Path) -> None:
    stdout_path = tmp_path / "agent.stdout.log"
    stderr_path = tmp_path / "agent.stderr.log"

    returncode, timed_out, _duration = runner._run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        environment=os.environ.copy(),
        timeout_seconds=1,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert returncode == 124
    assert timed_out


def test_container_command_requires_isolated_git_metadata(tmp_path: Path) -> None:
    profile = ContainerProfile("agent:local", "none", "1g", 1.0, 32, None)

    with pytest.raises(ArenaError, match="isolated Git metadata"):
        runner._run_command(
            ["agent"],
            cwd=tmp_path,
            environment=os.environ.copy(),
            timeout_seconds=10,
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            container_profile=profile,
        )
