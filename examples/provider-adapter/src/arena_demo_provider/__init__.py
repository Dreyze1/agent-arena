"""Small example plugin for Agent Arena's provider adapter API."""

from collections.abc import Mapping
from typing import Any

from agent_arena.adapters import AdapterContext, AdapterInvocation


class DemoProviderAdapter:
    """Build an argv for a generic CLI from TOML profile options."""

    def build_invocation(
        self,
        context: AdapterContext,
        options: Mapping[str, Any],
    ) -> AdapterInvocation:
        executable = str(options.get("executable", "demo-agent"))
        model = str(options.get("model", "default"))
        return AdapterInvocation(
            (
                executable,
                "run",
                "--model",
                model,
                "--directory",
                str(context.worktree),
                context.task,
            )
        )
