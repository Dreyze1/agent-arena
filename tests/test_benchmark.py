from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agent_arena.benchmark as benchmark
from agent_arena.errors import BenchmarkCancelled


def test_cancelled_benchmark_saves_id_and_partial_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    artifacts = tmp_path / "artifacts" / "benchmarks"
    suite = benchmark.BenchmarkSuite(
        name="Example",
        repetitions=1,
        checks=None,
        tasks=(benchmark.BenchmarkTask("task-1", "prompt", None),),
        sha256="suite-sha",
    )
    agent = SimpleNamespace(inherit_env=(), adapter_name="command", container=None)
    adapter = SimpleNamespace(
        descriptor=SimpleNamespace(as_manifest=lambda: {"name": "command", "version": "1"})
    )

    monkeypatch.setattr(benchmark, "resolve_repository", lambda _: repository)
    monkeypatch.setattr(benchmark, "load_benchmark_suite", lambda _: suite)
    monkeypatch.setattr(benchmark, "file_sha256", lambda _: "config-sha")
    monkeypatch.setattr(benchmark, "_read_config", lambda _: ({"agent": agent}, None))
    monkeypatch.setattr(benchmark, "_child_environment", lambda *_: {})
    monkeypatch.setattr(benchmark, "resolve_agent_adapter", lambda _: adapter)
    monkeypatch.setattr(benchmark, "repository_head", lambda _: "a" * 40)
    monkeypatch.setattr(benchmark, "benchmark_artifacts_directory", lambda _: artifacts)

    def interrupt_benchmark(**_: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark, "run_arena", interrupt_benchmark)

    with pytest.raises(BenchmarkCancelled) as error:
        benchmark.run_benchmark(
            repository_path=repository,
            suite_path=Path("suite.toml"),
            config_path=Path(".agent-arena.toml"),
            selected_agents=None,
            timeout_seconds=10,
            keep_worktrees=False,
        )

    benchmark_id = error.value.benchmark_id
    assert error.value.manifest_path == artifacts / benchmark_id / "benchmark.json"
    manifest_path = artifacts / benchmark_id / "benchmark.json"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "cancelled"
    assert saved["error"] == "Interrupted by user."
    assert saved["runs"] == []
    assert str(error.value) == "Interrupted by user."
