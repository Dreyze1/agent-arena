"""Restricted-environment worker for installed provider adapter plugins."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_arena.adapters import (
    AdapterContext,
    AdapterInvocation,
    load_plugin_implementation,
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        adapter_name = payload.get("adapter")
        descriptor = payload.get("descriptor")
        raw_context = payload.get("context")
        options = payload.get("options")
        if (
            not isinstance(adapter_name, str)
            or not isinstance(descriptor, dict)
            or not isinstance(raw_context, dict)
            or not isinstance(options, dict)
        ):
            raise ValueError("request fields are invalid")
        context = AdapterContext(
            agent_name=_string_field(raw_context, "agent_name"),
            task=_string_field(raw_context, "task"),
            repository=Path(_string_field(raw_context, "repository")),
            worktree=Path(_string_field(raw_context, "worktree")),
            base_sha=_string_field(raw_context, "base_sha"),
            run_id=_string_field(raw_context, "run_id"),
        )

        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                adapter = load_plugin_implementation(adapter_name, descriptor)
                invocation = adapter.build_invocation(context, options)
        if not isinstance(invocation, AdapterInvocation):
            raise TypeError("adapter must return AdapterInvocation")
        print(json.dumps({"argv": list(invocation.argv)}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        return 1


def _string_field(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
