from __future__ import annotations

import json
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

import agent_arena.adapters as adapters
from agent_arena.adapters import (
    AdapterContext,
    AdapterInvocation,
    CommandTemplateAdapter,
    available_adapter_descriptors,
    resolve_agent_adapter,
    validate_adapter_name,
)
from agent_arena.errors import ArenaError


def _context() -> AdapterContext:
    return AdapterContext(
        agent_name="demo",
        task="Fix the parser",
        repository=Path("/repo"),
        worktree=Path("/repo/.arena/demo"),
        base_sha="a" * 40,
        run_id="run-1",
    )


@dataclass
class _Distribution:
    metadata: dict[str, str]
    version: str


class _EntryPoint:
    def __init__(self, name: str, loaded: object = None, *, value: str = "demo:Adapter") -> None:
        self.name = name
        self.value = value
        self.dist = _Distribution({"Name": "demo-provider"}, "1.2.3")
        self._loaded = loaded
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def _install_entry_points(monkeypatch: pytest.MonkeyPatch, *items: _EntryPoint) -> None:
    monkeypatch.setattr(adapters, "entry_points", lambda *, group: items)


def test_command_adapter_expands_context_without_rewriting_inserted_task_text() -> None:
    invocation = CommandTemplateAdapter().build_invocation(
        _context(),
        {
            "command": (
                "codex",
                "exec",
                "{task}",
                "{worktree}",
                "{base_sha}",
            )
        },
    )

    assert invocation.argv == (
        "codex",
        "exec",
        "Fix the parser",
        str(Path("/repo/.arena/demo")),
        "a" * 40,
    )


def test_command_adapter_preserves_placeholder_like_text_in_task() -> None:
    context = AdapterContext(**{**_context().__dict__, "task": "Keep {base_sha} unchanged"})

    invocation = CommandTemplateAdapter().build_invocation(context, {"command": ("tool", "{task}")})

    assert invocation.argv == ("tool", "Keep {base_sha} unchanged")


def test_adapter_names_are_validated() -> None:
    assert validate_adapter_name("vendor.adapter-2") == "vendor.adapter-2"
    with pytest.raises(ArenaError, match="(?i)adapter name"):
        validate_adapter_name("../escape")


def test_available_descriptors_do_not_import_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _EntryPoint("demo")
    _install_entry_points(monkeypatch, entry_point)

    descriptors = available_adapter_descriptors()

    assert [item.name for item in descriptors] == ["command", "demo"]
    assert descriptors[1].distribution == "demo-provider"
    assert descriptors[1].version == "1.2.3"
    assert entry_point.load_calls == 0


def test_resolve_adapter_defers_plugin_loading_to_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExampleAdapter:
        def build_invocation(self, context: AdapterContext, options: dict[str, object]):
            return AdapterInvocation(("demo-cli", context.task, str(options["model"])))

    unused = _EntryPoint("unused", RuntimeError("must not load"))
    selected = _EntryPoint("demo", ExampleAdapter)
    _install_entry_points(monkeypatch, unused, selected)

    resolved = resolve_agent_adapter("demo")

    assert resolved.implementation is None
    assert resolved.descriptor.as_manifest() == {
        "name": "demo",
        "distribution": "demo-provider",
        "version": "1.2.3",
        "entry_point": "demo:Adapter",
    }
    assert selected.load_calls == 0
    assert unused.load_calls == 0


def test_selected_adapter_invocation_runs_in_worker_with_filtered_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entry_points(monkeypatch, _EntryPoint("demo", object()))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-plugin")
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            args[0], 0, json.dumps({"argv": ["demo-cli", "Fix the parser"]}), ""
        )

    monkeypatch.setattr(adapters.subprocess, "run", fake_run)

    invocation = resolve_agent_adapter("demo").build_invocation(_context(), {})

    assert invocation.argv == ("demo-cli", "Fix the parser")
    worker_environment = observed["env"]
    assert isinstance(worker_environment, dict)
    assert "OPENAI_API_KEY" not in worker_environment
    assert worker_environment["PYTHONIOENCODING"] == "utf-8"
    assert worker_environment["PYTHONUTF8"] == "1"


def test_plugin_worker_loads_entry_point_and_omits_profile_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_source = tmp_path / "demo_adapter.py"
    plugin_source.write_text(
        textwrap.dedent(
            """\
            import os
            from agent_arena.adapters import AdapterInvocation

            class DemoAdapter:
                def build_invocation(self, context, options):
                    print("plugin diagnostic")
                    secret = os.getenv("ARENA_TEST_SECRET", "<absent>")
                    return AdapterInvocation(("demo-cli", context.task, secret))
            """
        ),
        encoding="utf-8",
    )
    distribution_info = tmp_path / "arena_test_provider-1.0.dist-info"
    distribution_info.mkdir()
    (distribution_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: arena-test-provider\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (distribution_info / "entry_points.txt").write_text(
        "[agent_arena.adapters]\ndemo-isolated-test = demo_adapter:DemoAdapter\n",
        encoding="utf-8",
    )
    package_source = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(package_source), str(tmp_path))))
    monkeypatch.setenv("ARENA_TEST_SECRET", "should-not-be-forwarded")

    invocation = resolve_agent_adapter("demo-isolated-test").build_invocation(_context(), {})

    assert invocation.argv == ("demo-cli", "Fix the parser", "<absent>")


def test_plugin_load_helper_accepts_an_adapter_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExampleAdapter:
        def build_invocation(self, context: AdapterContext, options: dict[str, object]):
            return AdapterInvocation(("demo-cli",))

    instance = ExampleAdapter()
    _install_entry_points(monkeypatch, _EntryPoint("demo", instance))

    descriptor = adapters._plugin_descriptor(_EntryPoint("demo", instance))
    assert adapters.load_plugin_implementation("demo", descriptor.as_manifest()) is instance


def test_duplicate_plugin_names_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entry_points(
        monkeypatch,
        _EntryPoint("demo", object(), value="first:Adapter"),
        _EntryPoint("demo", object(), value="second:Adapter"),
    )

    with pytest.raises(ArenaError, match="(?i)more than one installed plugin"):
        resolve_agent_adapter("demo")


def test_missing_plugin_lists_available_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entry_points(monkeypatch, _EntryPoint("demo", object()))

    with pytest.raises(ArenaError, match="Available adapters: command, demo"):
        resolve_agent_adapter("missing")


def test_plugin_load_error_does_not_include_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _EntryPoint("demo", RuntimeError("secret-token"))
    _install_entry_points(monkeypatch, entry_point)
    descriptor = adapters._plugin_descriptor(entry_point)

    with pytest.raises(ArenaError) as error:
        adapters.load_plugin_implementation("demo", descriptor.as_manifest())

    assert "secret-token" not in str(error.value)
    assert "RuntimeError" in str(error.value)


def test_adapter_output_must_be_an_invocation() -> None:
    class InvalidAdapter:
        def build_invocation(self, context: AdapterContext, options: dict[str, object]):
            return {"argv": ["demo-cli"]}

    resolved = adapters.ResolvedAgentAdapter(
        descriptor=adapters.AdapterDescriptor("invalid", "demo", "1.0", "demo:Adapter"),
        implementation=InvalidAdapter(),
    )

    with pytest.raises(ArenaError, match="AdapterInvocation"):
        resolved.build_invocation(_context(), {})


def test_adapter_exceptions_are_redacted() -> None:
    class FailingAdapter:
        def build_invocation(self, context: AdapterContext, options: dict[str, object]):
            raise RuntimeError("secret-token")

    resolved = adapters.ResolvedAgentAdapter(
        descriptor=adapters.AdapterDescriptor("failing", "demo", "1.0", "demo:Adapter"),
        implementation=FailingAdapter(),
    )

    with pytest.raises(ArenaError) as error:
        resolved.build_invocation(_context(), {})

    assert "secret-token" not in str(error.value)
    assert "RuntimeError" in str(error.value)


def test_adapter_options_must_be_json_compatible() -> None:
    resolved = adapters.ResolvedAgentAdapter(
        descriptor=adapters.AdapterDescriptor("demo", "demo", "1.0", "demo:Adapter"),
        implementation=None,
    )

    with pytest.raises(ArenaError, match="JSON-compatible"):
        resolved.build_invocation(_context(), {"unsupported": object()})


@pytest.mark.parametrize("argv", [(), ("",), ("demo", "")])
def test_invocation_rejects_empty_arguments(argv: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="non-empty argument"):
        AdapterInvocation(argv)


def test_builtin_adapter_has_stable_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_entry_points(monkeypatch)

    resolved = resolve_agent_adapter("command")

    assert resolved.descriptor.name == "command"
    assert resolved.descriptor.distribution == "agent-arena"
    assert resolved.descriptor.entry_point == "agent_arena.adapters:CommandTemplateAdapter"
