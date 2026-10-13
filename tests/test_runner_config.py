from __future__ import annotations

from pathlib import Path

import pytest

from agent_arena.errors import ArenaError
from agent_arena.runner import _read_config


def _read(tmp_path: Path, content: str):
    config_path = tmp_path / ".agent-arena.toml"
    config_path.write_text(content, encoding="utf-8")
    return _read_config(config_path)


def test_legacy_command_profile_remains_compatible(tmp_path: Path) -> None:
    agents, checks = _read(
        tmp_path,
        """\
        version = 1

        [agents.codex]
        command = ["codex", "exec", "{task}"]
        inherit_env = ["OPENAI_API_KEY"]
        """,
    )

    assert agents["codex"].adapter_name == "command"
    assert agents["codex"].adapter_options == {"command": ("codex", "exec", "{task}")}
    assert agents["codex"].inherit_env == ("OPENAI_API_KEY",)
    assert checks == []


def test_plugin_profile_reads_adapter_and_options(tmp_path: Path) -> None:
    agents, _ = _read(
        tmp_path,
        """\
        version = 1

        [agents.demo]
        adapter = "demo-provider"
        inherit_env = ["DEMO_API_KEY"]

        [agents.demo.options]
        model = "fast"
        retries = 2
        """,
    )

    assert agents["demo"].adapter_name == "demo-provider"
    assert agents["demo"].adapter_options == {"model": "fast", "retries": 2}
    assert agents["demo"].inherit_env == ("DEMO_API_KEY",)


def test_plugin_profile_cannot_mix_command_and_adapter(tmp_path: Path) -> None:
    with pytest.raises(ArenaError, match="cannot define command"):
        _read(
            tmp_path,
            """\
            version = 1

            [agents.demo]
            adapter = "demo-provider"
            command = ["demo-agent"]
            """,
        )


def test_builtin_adapter_rejects_options_table(tmp_path: Path) -> None:
    with pytest.raises(ArenaError, match="instead of adapter options"):
        _read(
            tmp_path,
            """\
            version = 1

            [agents.demo]
            command = ["demo-agent"]

            [agents.demo.options]
            model = "fast"
            """,
        )
