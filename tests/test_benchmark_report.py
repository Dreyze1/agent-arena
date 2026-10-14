from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

import agent_arena.cli as cli
from agent_arena.errors import ArenaError, BenchmarkCancelled

_BENCHMARK_ID = "20261013T120000Z-abcdef12"
_MANIFEST_LIMIT = 16 * 1024 * 1024


def _check(returncode: int = 0, *, timed_out: bool = False) -> dict[str, Any]:
    return {
        "command": ["pytest", "-q"],
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": 0.25,
    }


def _outcome(
    name: str,
    *,
    returncode: int = 0,
    timed_out: bool = False,
    duration: float = 1.0,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "checks": checks or [],
    }


def _manifest(
    *,
    status: str = "complete",
    tasks: list[dict[str, Any]] | None = None,
    agent_names: list[str] | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task_rows = tasks or [
        {"id": "alpha", "prompt": "PRIVATE_PROMPT_ALPHA", "checks": [["pytest"]]},
        {"id": "beta", "prompt": "PRIVATE_PROMPT_BETA", "checks": [["pytest"]]},
    ]
    names = agent_names or ["codex", "claude"]
    return {
        "schema_version": 1,
        "benchmark_id": _BENCHMARK_ID,
        "status": status,
        "suite_name": "Example suite",
        "suite_sha256": "a" * 64,
        "repository": "PRIVATE_REPOSITORY_PATH",
        "base_sha": "b" * 40,
        "agent_names": names,
        "created_at": "2026-10-13T12:00:00+00:00",
        "completed_at": "2026-10-13T12:01:00+00:00" if status == "complete" else None,
        "suite_snapshot": {"repetitions": 1, "checks": None, "tasks": task_rows},
        "runs": runs or [],
        "summary": [],
    }


def _save_manifest(tmp_path: Path, data: dict[str, Any]) -> Path:
    benchmark_dir = tmp_path / "benchmarks" / _BENCHMARK_ID
    benchmark_dir.mkdir(parents=True)
    path = benchmark_dir / "benchmark.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_create_benchmark_report_renders_aggregate_and_task_by_agent_matrix(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    data = _manifest(
        tasks=[
            {"id": "alpha", "prompt": "PRIVATE_PROMPT_ALPHA", "checks": [["pytest"]]},
            {"id": "beta", "prompt": "PRIVATE_PROMPT_BETA", "checks": [["pytest"]]},
        ],
        runs=[
            {
                "task_id": "alpha",
                "task_index": 1,
                "repetition": 1,
                "agents": [
                    _outcome("codex", checks=[_check()]),
                    _outcome("claude", checks=[_check()]),
                ],
            },
            {
                "task_id": "beta",
                "task_index": 2,
                "repetition": 1,
                "agents": [
                    _outcome("codex", returncode=1, checks=[_check(1)]),
                    _outcome("claude", checks=[_check()]),
                ],
            },
        ],
    )
    manifest_path = _save_manifest(tmp_path, data)

    report_path = create_benchmark_report(manifest_path)
    rendered = report_path.read_text(encoding="utf-8")

    assert report_path == manifest_path.with_name("report.html")
    assert "Example suite" in rendered
    assert "alpha" in rendered and "beta" in rendered
    assert "codex" in rendered and "claude" in rendered
    assert "<table" in rendered and "<th" in rendered
    assert "verified" in rendered.lower()
    assert "failed" in rendered.lower()


def test_create_benchmark_report_counts_repeated_attempts_and_distinguishes_unverified(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    data = _manifest(
        tasks=[{"id": "repeated", "prompt": "PRIVATE_PROMPT", "checks": None}],
        agent_names=["codex"],
        runs=[
            {
                "task_id": "repeated",
                "task_index": 1,
                "repetition": repetition,
                "agents": [
                    _outcome(
                        "codex",
                        duration=duration,
                        checks=[] if repetition == 3 else [_check(1 if repetition == 2 else 0)],
                    )
                ],
            }
            for repetition, duration in ((1, 1.0), (2, 2.0), (3, 3.0))
        ],
    )
    data["suite_snapshot"]["repetitions"] = 3
    manifest_path = _save_manifest(tmp_path, data)

    report_path = create_benchmark_report(manifest_path)
    rendered = report_path.read_text(encoding="utf-8").lower()

    assert "repeated" in rendered
    assert "3" in rendered
    assert "unverified" in rendered
    assert "check" in rendered and "fail" in rendered
    assert "2.0" in rendered or "2s" in rendered


def test_create_benchmark_report_marks_timeout_and_missing_outcomes_incomplete(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    data = _manifest(
        status="running",
        tasks=[
            {"id": "slow", "prompt": "PRIVATE_PROMPT", "checks": [["pytest"]]},
            {"id": "not-started", "prompt": "PRIVATE_PROMPT", "checks": [["pytest"]]},
        ],
        agent_names=["codex", "claude"],
        runs=[
            {
                "task_id": "slow",
                "task_index": 1,
                "repetition": 1,
                "agents": [
                    _outcome("codex", returncode=124, timed_out=True, checks=[_check()]),
                ],
            }
        ],
    )
    manifest_path = _save_manifest(tmp_path, data)

    report_path = create_benchmark_report(manifest_path)
    rendered = report_path.read_text(encoding="utf-8").lower()

    assert "timeout" in rendered or "timed out" in rendered
    assert "incomplete" in rendered
    assert "not-started" in rendered


def test_create_benchmark_report_escapes_html_and_omits_private_manifest_data(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    unsafe_name = 'codex<img src=x onerror="alert(1)">'
    data = _manifest(
        tasks=[
            {
                "id": "alpha",
                "prompt": "PRIVATE_PROMPT_DO_NOT_RENDER",
                "checks": [["PRIVATE_CHECK_COMMAND_DO_NOT_RENDER"]],
            }
        ],
        agent_names=[unsafe_name],
        runs=[
            {
                "task_id": "alpha",
                "task_index": 1,
                "repetition": 1,
                "agents": [
                    {
                        **_outcome(unsafe_name, checks=[_check()]),
                        "patch": "PRIVATE_PATCH_DO_NOT_RENDER",
                        "stdout": "PRIVATE_LOG_DO_NOT_RENDER",
                        "stderr": "PRIVATE_STDERR_DO_NOT_RENDER",
                        "checks": [
                            {
                                **_check(),
                                "command": ["PRIVATE_CHECK_COMMAND_DO_NOT_RENDER"],
                            }
                        ],
                    }
                ],
            }
        ],
    )
    data["suite_name"] = '</title><script>alert("xss")</script>'
    manifest_path = _save_manifest(tmp_path, data)

    rendered = create_benchmark_report(manifest_path).read_text(encoding="utf-8")

    assert "&lt;script&gt;" in rendered
    assert "<script>alert" not in rendered
    assert 'onerror="alert(1)"' not in rendered
    for private_value in (
        "PRIVATE_PROMPT_DO_NOT_RENDER",
        "PRIVATE_REPOSITORY_PATH",
        "PRIVATE_PATCH_DO_NOT_RENDER",
        "PRIVATE_LOG_DO_NOT_RENDER",
        "PRIVATE_STDERR_DO_NOT_RENDER",
        "PRIVATE_CHECK_COMMAND_DO_NOT_RENDER",
    ):
        assert private_value not in rendered


def test_create_benchmark_report_rejects_malformed_or_non_object_manifest(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    manifest_path = _save_manifest(tmp_path, _manifest())
    manifest_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ArenaError, match="manifest|JSON|read"):
        create_benchmark_report(manifest_path)

    manifest_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ArenaError, match="object|manifest"):
        create_benchmark_report(manifest_path)


def test_create_benchmark_report_rejects_deeply_nested_json(tmp_path: Path) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    manifest_path = _save_manifest(tmp_path, _manifest())
    nesting = 300
    document = json.dumps(_manifest())
    nested_object = '{"ignored":' + '{"nested":' * nesting + "0" + "}" * nesting + "}"
    manifest_path.write_text(document[:-1] + "," + nested_object[1:], encoding="utf-8")

    with pytest.raises(ArenaError, match="(?i)nest|depth"):
        create_benchmark_report(manifest_path)


def test_create_benchmark_report_rejects_excessive_json_width(tmp_path: Path) -> None:
    from agent_arena.benchmark_report import _MAX_JSON_TOKENS, create_benchmark_report

    manifest_path = _save_manifest(tmp_path, _manifest())
    document = json.dumps(_manifest())
    wide_array = "0," * (_MAX_JSON_TOKENS - 1) + "0"
    manifest_path.write_text(document[:-1] + ',"ignored":[' + wide_array + "]}", encoding="utf-8")

    with pytest.raises(ArenaError, match="too many JSON values"):
        create_benchmark_report(manifest_path)


def test_create_benchmark_report_accepts_large_generated_manifest(tmp_path: Path) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    check_results = [
        {
            "command": "pytest",
            "returncode": 0,
            "timed_out": False,
            "duration_seconds": 0.25,
            "stdout_file": f"check-{index}.stdout.log",
            "stderr_file": f"check-{index}.stderr.log",
        }
        for index in range(1, 121)
    ]
    tasks = [{"id": f"task-{index:02d}", "prompt": "Run the checks."} for index in range(64)]
    runs = [
        {
            "task_id": task["id"],
            "repetition": repetition,
            "agents": [_outcome("codex", checks=check_results)],
        }
        for task in tasks
        for repetition in (1, 2)
    ]
    data = _manifest(
        tasks=tasks,
        agent_names=["codex"],
        runs=runs,
    )
    data["suite_snapshot"]["repetitions"] = 2
    data["suite_snapshot"]["checks"] = [["pytest"] for _ in range(120)]
    manifest_path = _save_manifest(tmp_path, data)

    report_path = create_benchmark_report(manifest_path)

    assert report_path.is_file()


def test_create_benchmark_report_accepts_json_at_maximum_nesting_depth(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    data = _manifest()
    nested_value: Any = []
    # The root object plus 255 nested lists exercises exactly 256 levels.
    for _ in range(254):
        nested_value = [nested_value]
    data["ignored"] = nested_value
    manifest_path = _save_manifest(tmp_path, data)

    report_path = create_benchmark_report(manifest_path)

    assert report_path.is_file()


def test_create_benchmark_report_ignores_brackets_inside_json_strings(tmp_path: Path) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    data = _manifest()
    data["ignored"] = '[{]} "quoted" \\ slash' * 300
    manifest_path = _save_manifest(tmp_path, data)

    report_path = create_benchmark_report(manifest_path)

    assert report_path.is_file()


def test_create_benchmark_report_rejects_oversized_manifest(tmp_path: Path) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    manifest_path = _save_manifest(tmp_path, _manifest())
    manifest_path.write_bytes(b" " * (_MANIFEST_LIMIT + 1))

    with pytest.raises(ArenaError, match="large|limit|size"):
        create_benchmark_report(manifest_path)


def test_create_benchmark_report_rejects_duration_integer_outside_float_range(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    outcome = _outcome("codex", checks=[_check()])
    outcome["duration_seconds"] = 10**400
    data = _manifest(
        tasks=[{"id": "large-duration", "prompt": "PRIVATE_PROMPT", "checks": [["pytest"]]}],
        agent_names=["codex"],
        runs=[
            {
                "task_id": "large-duration",
                "task_index": 1,
                "repetition": 1,
                "agents": [outcome],
            }
        ],
    )
    manifest_path = _save_manifest(tmp_path, data)

    with pytest.raises(ArenaError, match="(?i)invalid duration"):
        create_benchmark_report(manifest_path)


def test_create_benchmark_report_rejects_symlinked_input_and_output(
    tmp_path: Path,
) -> None:
    from agent_arena.benchmark_report import create_benchmark_report

    manifest_path = _save_manifest(tmp_path, _manifest())
    input_link = manifest_path.with_name("linked-benchmark.json")
    try:
        input_link.symlink_to(manifest_path)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"file symlinks are unavailable: {error}")

    with pytest.raises(ArenaError, match="symlink"):
        create_benchmark_report(input_link)

    input_link.unlink()
    output_path = manifest_path.with_name("report.html")
    outside_target = tmp_path / "outside.html"
    outside_target.write_text("preserve me", encoding="utf-8")
    output_path.symlink_to(outside_target)

    with pytest.raises(ArenaError, match="symlink"):
        create_benchmark_report(manifest_path)
    assert output_path.is_symlink()
    assert outside_target.read_text(encoding="utf-8") == "preserve me"


def test_create_benchmark_report_preserves_existing_temporary_path_on_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_arena import benchmark_report
    from agent_arena.benchmark_report import create_benchmark_report

    manifest_path = _save_manifest(tmp_path, _manifest())
    report_path = manifest_path.with_name("report.html")
    report_path.write_text("existing report", encoding="utf-8")
    collision_path = report_path.with_name(".report.html.fixed.tmp")
    collision_path.write_text("preserve this file", encoding="utf-8")
    monkeypatch.setattr(benchmark_report.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))

    with pytest.raises(ArenaError, match="Could not write benchmark scorecard"):
        create_benchmark_report(manifest_path)

    assert report_path.read_text(encoding="utf-8") == "existing report"
    assert collision_path.read_text(encoding="utf-8") == "preserve this file"


@pytest.mark.parametrize("benchmark_id", ["latest", "../escape", "20261013T120000Z-ABCDEF12"])
def test_benchmark_report_command_rejects_invalid_ids(
    tmp_path: Path,
    benchmark_id: str,
) -> None:
    result = CliRunner().invoke(
        cli.app,
        ["benchmark-report", benchmark_id, "--repo", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "invalid" in result.stdout.lower()


def test_benchmark_report_command_renders_valid_id_and_opens_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(tmp_path))
    artifacts = tmp_path / "benchmark-history"
    manifest_path = artifacts / _BENCHMARK_ID / "benchmark.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    expected_report = manifest_path.with_name("report.html")
    calls: list[Path] = []
    opened: list[str] = []

    def generate(path: Path) -> Path:
        calls.append(path)
        expected_report.write_text("scorecard", encoding="utf-8")
        return expected_report

    monkeypatch.setattr(cli, "resolve_repository", lambda _: repository, raising=False)
    monkeypatch.setattr(cli, "benchmark_artifacts_directory", lambda _: artifacts, raising=False)
    monkeypatch.setattr(cli, "create_benchmark_report", generate, raising=False)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    result = CliRunner().invoke(
        cli.app,
        ["benchmark-report", _BENCHMARK_ID, "--repo", str(repository), "--open"],
    )

    assert result.exit_code == 0, result.stdout
    assert calls == [manifest_path]
    assert expected_report.name in result.stdout
    assert opened == [expected_report.as_uri()]


def test_benchmark_command_creates_and_prints_html_scorecard_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "benchmark.json"
    manifest = _manifest()
    manifest["summary"] = [
        {
            "agent": "codex",
            "verified_pass_rate": 1.0,
            "verified_passes": 1,
            "verified_attempts": 1,
            "agent_successes": 1,
            "attempts": 1,
            "verification_coverage": 1.0,
            "mean_duration_seconds": 1.0,
        }
    ]
    report_path = tmp_path / "report.html"
    calls: list[Path] = []

    def run_benchmark(**_: Any) -> tuple[Path, dict[str, Any]]:
        return manifest_path, manifest

    def generate(path: Path) -> Path:
        calls.append(path)
        report_path.write_text("scorecard", encoding="utf-8")
        return report_path

    monkeypatch.setattr(cli, "run_benchmark", run_benchmark)
    monkeypatch.setattr(cli, "create_benchmark_report", generate, raising=False)

    result = CliRunner().invoke(
        cli.app,
        ["benchmark", "--suite", str(tmp_path / "suite.toml"), "--repo", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert calls == [manifest_path]
    assert report_path.name in result.stdout


def test_benchmark_command_prints_recovery_command_for_cancelled_benchmark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo with spaces"

    def stop_benchmark(**_: Any) -> tuple[Path, dict[str, Any]]:
        raise BenchmarkCancelled(_BENCHMARK_ID, tmp_path / "benchmark.json")

    monkeypatch.setattr(cli, "run_benchmark", stop_benchmark)

    result = CliRunner().invoke(
        cli.app,
        ["benchmark", "--suite", str(tmp_path / "suite.toml"), "--repo", str(repository)],
    )
    normalized_output = " ".join(result.stdout.split())

    assert result.exit_code == 130
    assert f"arena benchmark-report {_BENCHMARK_ID} --repo" in normalized_output
    assert f"--repo {cli._quote_cli_path(repository)}" in normalized_output
