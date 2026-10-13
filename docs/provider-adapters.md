# Provider adapter plugins

Agent Arena can integrate with provider CLIs through installable Python packages. A plugin builds the argument vector for one agent invocation; Agent Arena still owns process execution, timeouts, environment forwarding, Docker execution, checks, logs, patches, and result manifests.

## Install and select an adapter

Plugins register a name in the Python entry-point group `agent_arena.adapters`. Install the package in the same Python environment as Agent Arena, then list discovered adapters:

```console
python -m pip install -e ./path/to/your-adapter
arena adapters
```

Configure an agent profile using the registered entry-point name. Plugin-specific values belong in its `options` table:

```toml
[agents.codex]
command = ["codex", "exec", "--full-auto", "{task}"]

[agents.my_provider]
adapter = "my-provider"
inherit_env = ["MY_PROVIDER_API_KEY"]

[agents.my_provider.options]
model = "fast-model"
mode = "edit"
```

The existing `command = [...]` profile remains supported and uses the built-in `command` adapter. Its `{task}`, `{repo}`, `{worktree}`, and `{base_sha}` placeholders are expanded as individual arguments without invoking a shell. A profile using a plugin cannot also set `command`.

## Public contract

Implement `AgentAdapter.build_invocation(context, options)` and return `AdapterInvocation` with a non-empty tuple of non-empty strings. Import the public types from `agent_arena.adapters`:

```python
from collections.abc import Mapping
from typing import Any

from agent_arena.adapters import AdapterContext, AdapterInvocation


class MyProviderAdapter:
    def build_invocation(
        self,
        context: AdapterContext,
        options: Mapping[str, Any],
    ) -> AdapterInvocation:
        model = str(options.get("model", "default"))
        return AdapterInvocation(
            (
                "my-provider",
                "run",
                "--model",
                model,
                "--directory",
                str(context.worktree),
                context.task,
            )
        )
```

`AdapterContext` provides `agent_name`, `task`, `repository`, `worktree`, `base_sha`, and `run_id`. Paths point to `/workspace` when the agent profile uses Docker. Options contain JSON-compatible values from that profile's `[agents.NAME.options]` table: strings, numbers, booleans, arrays, and tables.

An entry point may refer to an adapter class, an already-created adapter instance, or a no-argument factory that returns an adapter. For example, a package using a `src` layout can declare:

```toml
[project.entry-points."agent_arena.adapters"]
my-provider = "my_provider_adapter:MyProviderAdapter"
```

See [`examples/provider-adapter`](../examples/provider-adapter) for a complete tiny package.

## Environment and trust

Adapter discovery reads package metadata without importing plugin code. The selected plugin runs in a short-lived worker process with task context and options; profile credentials from `inherit_env` are not forwarded to that worker. The returned argv is then launched by the normal runner, which applies the profile's `inherit_env` settings. Do not put secrets in TOML options.

Plugins are executable Python packages and run with the current user's filesystem and network permissions. The worker reduces accidental environment exposure; it is not a sandbox. Install and use plugins you trust. Docker applies to the provider command, not to Python adapter code.

The run manifest records each selected adapter's entry-point name, distribution, version, and entry-point target. Benchmark manifests pin the same identity and stop if it changes between repetitions.
