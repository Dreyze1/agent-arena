from __future__ import annotations

import io
import json
import sys

import pytest

import agent_arena.adapter_worker as worker
from agent_arena.adapters import AdapterInvocation


def _payload() -> dict[str, object]:
    return {
        "adapter": "demo",
        "descriptor": {"name": "demo"},
        "context": {
            "agent_name": "worker-test",
            "task": "Fix parser",
            "repository": "/repo",
            "worktree": "/worktree",
            "base_sha": "abc123",
            "run_id": "run-123",
        },
        "options": {"model": "fast"},
    }


def test_worker_serializes_invocation_and_suppresses_plugin_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    class Adapter:
        def build_invocation(self, context, options):
            observed["context"] = context
            observed["options"] = options
            print("plugin stdout")
            print("plugin stderr", file=sys.stderr)
            return AdapterInvocation(("demo-agent", context.task))

    monkeypatch.setattr(worker, "load_plugin_implementation", lambda *_args: Adapter())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload())))

    assert worker.main() == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"argv": ["demo-agent", "Fix parser"]}
    assert captured.err == ""
    context = observed["context"]
    assert context.agent_name == "worker-test"
    assert context.repository.as_posix() == "/repo"
    assert observed["options"] == {"model": "fast"}


@pytest.mark.parametrize(
    ("input_json", "error_type"),
    [
        ("[]", "ValueError"),
        (
            json.dumps({**_payload(), "context": {**_payload()["context"], "task": None}}),
            "ValueError",
        ),
    ],
)
def test_worker_returns_redacted_error_for_invalid_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    input_json: str,
    error_type: str,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(input_json))

    assert worker.main() == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error_type": error_type}
    assert captured.err == ""


def test_worker_rejects_plugin_result_with_wrong_type(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Adapter:
        def build_invocation(self, _context, _options):
            return ["not", "an", "invocation"]

    monkeypatch.setattr(worker, "load_plugin_implementation", lambda *_args: Adapter())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload())))

    assert worker.main() == 1

    assert json.loads(capsys.readouterr().out) == {"error_type": "TypeError"}


def test_worker_redacts_plugin_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Adapter:
        def build_invocation(self, _context, _options):
            raise RuntimeError("secret should not be printed")

    monkeypatch.setattr(worker, "load_plugin_implementation", lambda *_args: Adapter())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload())))

    assert worker.main() == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error_type": "RuntimeError"}
    assert "secret" not in captured.out
    assert captured.err == ""
