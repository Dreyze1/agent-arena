"""Public provider-adapter contract and entry-point discovery."""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_arena import __version__
from agent_arena.errors import ArenaError

ADAPTER_ENTRY_POINT_GROUP = "agent_arena.adapters"
BUILTIN_COMMAND_ADAPTER = "command"
_ADAPTER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_PLACEHOLDER = re.compile(r"\{(task|repo|worktree|base_sha)\}")
_WORKER_ENVIRONMENT = {
    "APPDATA",
    "COMSPEC",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "LOGNAME",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PYTHONHOME",
    "PYTHONPATH",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "USERNAME",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
}


@dataclass(frozen=True)
class AdapterContext:
    """Run-specific task and workspace information supplied to an adapter."""

    agent_name: str
    task: str
    repository: Path
    worktree: Path
    base_sha: str
    run_id: str


@dataclass(frozen=True)
class AdapterInvocation:
    """Argument vector returned by an adapter; commands never pass through a shell."""

    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or not all(isinstance(part, str) and part for part in self.argv)
        ):
            raise ValueError("argv must be a non-empty tuple of non-empty argument strings")


@runtime_checkable
class AgentAdapter(Protocol):
    """Interface implemented by a built-in or installed provider adapter."""

    def build_invocation(
        self,
        context: AdapterContext,
        options: Mapping[str, Any],
    ) -> AdapterInvocation:
        """Build the command for one agent run without launching it."""


@dataclass(frozen=True)
class AdapterDescriptor:
    """Stable identity for an adapter, suitable for manifests and CLI display."""

    name: str
    distribution: str
    version: str
    entry_point: str

    def as_manifest(self) -> dict[str, str]:
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "entry_point": self.entry_point,
        }


@dataclass(frozen=True)
class ResolvedAgentAdapter:
    """A selected adapter implementation and its recorded package identity."""

    descriptor: AdapterDescriptor
    implementation: AgentAdapter | None

    def build_invocation(
        self,
        context: AdapterContext,
        options: Mapping[str, Any],
    ) -> AdapterInvocation:
        if self.implementation is None:
            invocation = _build_plugin_invocation(self.descriptor, context, options)
        else:
            try:
                invocation = self.implementation.build_invocation(context, options)
            except Exception as error:
                raise ArenaError(
                    f"Adapter {self.descriptor.name!r} failed to build an invocation "
                    f"({type(error).__name__})."
                ) from None
        if not isinstance(invocation, AdapterInvocation):
            raise ArenaError(f"Adapter {self.descriptor.name!r} must return an AdapterInvocation.")
        if not invocation.argv or not all(
            isinstance(part, str) and part for part in invocation.argv
        ):
            raise ArenaError(
                f"Adapter {self.descriptor.name!r} returned an invalid command argument array."
            )
        return invocation


class CommandTemplateAdapter:
    """Built-in adapter that preserves the original command-array configuration."""

    def build_invocation(
        self,
        context: AdapterContext,
        options: Mapping[str, Any],
    ) -> AdapterInvocation:
        command = options.get("command")
        if (
            not isinstance(command, tuple)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ArenaError("The built-in command adapter requires a non-empty command array.")

        values = {
            "task": context.task,
            "repo": str(context.repository),
            "worktree": str(context.worktree),
            "base_sha": context.base_sha,
        }
        argv = tuple(
            _PLACEHOLDER.sub(lambda match: values[match.group(1)], part) for part in command
        )
        return AdapterInvocation(argv)


def validate_adapter_name(value: Any) -> str:
    """Validate an adapter entry-point name from TOML."""
    if not isinstance(value, str) or not _ADAPTER_NAME.fullmatch(value):
        raise ArenaError(
            "Adapter name must start with a letter or digit and contain only letters, "
            "numbers, dots, underscores, or hyphens (max 64 characters)."
        )
    return value


def _plugin_entry_points() -> tuple[Any, ...]:
    return tuple(entry_points(group=ADAPTER_ENTRY_POINT_GROUP))


def _plugin_descriptor(entry_point: Any) -> AdapterDescriptor:
    distribution = getattr(entry_point, "dist", None)
    distribution_name = "unknown"
    version = "unknown"
    if distribution is not None:
        distribution_name = distribution.metadata.get("Name") or "unknown"
        version = distribution.version or "unknown"
    return AdapterDescriptor(
        name=entry_point.name,
        distribution=distribution_name,
        version=version,
        entry_point=entry_point.value,
    )


def available_adapter_descriptors() -> tuple[AdapterDescriptor, ...]:
    """List built-in and installed adapters without importing plugin code."""
    built_in = AdapterDescriptor(
        name=BUILTIN_COMMAND_ADAPTER,
        distribution="agent-arena",
        version=__version__,
        entry_point="agent_arena.adapters:CommandTemplateAdapter",
    )
    plugins = sorted(
        (_plugin_descriptor(item) for item in _plugin_entry_points()),
        key=lambda item: (item.name.casefold(), item.distribution.casefold(), item.entry_point),
    )
    return (built_in, *plugins)


def _load_plugin(entry_point: Any, descriptor: AdapterDescriptor) -> AgentAdapter:
    try:
        loaded = entry_point.load()
        if inspect.isclass(loaded):
            implementation = loaded()
        elif callable(getattr(loaded, "build_invocation", None)):
            implementation = loaded
        elif callable(loaded):
            implementation = loaded()
        else:
            raise TypeError("entry point must resolve to an adapter class, factory, or instance")
    except Exception as error:
        raise ArenaError(
            f"Could not load adapter {descriptor.name!r} from "
            f"{descriptor.distribution!r} ({type(error).__name__})."
        ) from None

    if not callable(getattr(implementation, "build_invocation", None)):
        raise ArenaError(
            f"Adapter {descriptor.name!r} must implement build_invocation(context, options)."
        )
    return implementation


def _selected_plugin(name: str) -> tuple[Any, AdapterDescriptor]:
    matches = [item for item in _plugin_entry_points() if item.name == name]
    if not matches:
        available = ", ".join(item.name for item in available_adapter_descriptors())
        raise ArenaError(f"Adapter {name!r} is not installed. Available adapters: {available}")
    if len(matches) > 1:
        sources = ", ".join(_plugin_descriptor(item).distribution for item in matches)
        raise ArenaError(
            f"More than one installed plugin provides adapter {name!r}: {sources}. "
            "Resolve the entry-point name conflict."
        )
    entry_point = matches[0]
    return entry_point, _plugin_descriptor(entry_point)


def load_plugin_implementation(
    name: str,
    expected_descriptor: dict[str, str],
) -> AgentAdapter:
    """Load a plugin in the restricted-environment adapter worker after checking identity."""
    entry_point, descriptor = _selected_plugin(name)
    if descriptor.as_manifest() != expected_descriptor:
        raise ArenaError(f"Adapter {name!r} changed after the run was prepared.")
    return _load_plugin(entry_point, descriptor)


def _build_plugin_invocation(
    descriptor: AdapterDescriptor,
    context: AdapterContext,
    options: Mapping[str, Any],
) -> AdapterInvocation:
    payload = {
        "adapter": descriptor.name,
        "descriptor": descriptor.as_manifest(),
        "context": {
            "agent_name": context.agent_name,
            "task": context.task,
            "repository": str(context.repository),
            "worktree": str(context.worktree),
            "base_sha": context.base_sha,
            "run_id": context.run_id,
        },
        "options": dict(options),
    }
    try:
        serialized_payload = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ArenaError(
            f"Options for adapter {descriptor.name!r} must contain JSON-compatible values."
        ) from None
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in _WORKER_ENVIRONMENT
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agent_arena.adapter_worker"],
            input=serialized_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=tempfile.gettempdir(),
            env=environment,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as error:
        raise ArenaError("Python was not found while starting the adapter worker.") from error
    except subprocess.TimeoutExpired as error:
        raise ArenaError(
            f"Adapter {descriptor.name!r} timed out while building its command."
        ) from error
    except OSError as error:
        raise ArenaError(
            f"Could not start adapter {descriptor.name!r} ({type(error).__name__})."
        ) from None

    if len(result.stdout) > 1_000_000:
        raise ArenaError(f"Adapter {descriptor.name!r} returned an oversized response.")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ArenaError(f"Adapter {descriptor.name!r} returned an invalid response.") from None
    if result.returncode:
        error_type = response.get("error_type") if isinstance(response, dict) else None
        suffix = f" ({error_type})" if isinstance(error_type, str) else ""
        raise ArenaError(f"Adapter {descriptor.name!r} failed while building its command{suffix}.")
    if not isinstance(response, dict) or not isinstance(response.get("argv"), list):
        raise ArenaError(f"Adapter {descriptor.name!r} returned an invalid response.")
    argv = response["argv"]
    if not all(isinstance(part, str) and part for part in argv):
        raise ArenaError(f"Adapter {descriptor.name!r} returned an invalid command argument array.")
    try:
        return AdapterInvocation(tuple(argv))
    except ValueError as error:
        raise ArenaError(
            f"Adapter {descriptor.name!r} returned an invalid command argument array."
        ) from error


def resolve_agent_adapter(name: str) -> ResolvedAgentAdapter:
    """Resolve one adapter; installed plugin code runs only in the worker."""
    validate_adapter_name(name)
    if name == BUILTIN_COMMAND_ADAPTER:
        descriptor = AdapterDescriptor(
            name=BUILTIN_COMMAND_ADAPTER,
            distribution="agent-arena",
            version=__version__,
            entry_point="agent_arena.adapters:CommandTemplateAdapter",
        )
        return ResolvedAgentAdapter(descriptor, CommandTemplateAdapter())

    _, descriptor = _selected_plugin(name)
    return ResolvedAgentAdapter(descriptor, None)
