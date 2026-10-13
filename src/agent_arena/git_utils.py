"""Helpers for invoking local Git commands predictably."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_GIT_CONTEXT_VARIABLES = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_QUARANTINE_PATH",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
}
_GIT_CONFIG_SOURCE_VARIABLES = {
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_SYSTEM",
}


def git_subprocess_environment(*, preserve_identity_config: bool = False) -> dict[str, str]:
    """Return the current environment without Git repository/config overrides."""
    environment = os.environ.copy()
    for name in tuple(environment):
        normalized = name.upper()
        preserve_config_source = (
            preserve_identity_config and normalized in _GIT_CONFIG_SOURCE_VARIABLES
        )
        if normalized in _GIT_CONTEXT_VARIABLES or (
            normalized.startswith("GIT_CONFIG") and not preserve_config_source
        ):
            environment.pop(name)
    return environment


def git_commit_environment(repository: Path) -> dict[str, str]:
    """Keep configured Git identity while stripping config overrides from commits."""
    environment = git_subprocess_environment()
    config_environment = git_subprocess_environment(preserve_identity_config=True)
    for config_key, author_key, committer_key in (
        ("user.name", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"),
        ("user.email", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"),
    ):
        result = subprocess.run(
            ["git", "-C", str(repository), "config", "--get", config_key],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=config_environment,
        )
        if result.returncode == 0:
            value = result.stdout.rstrip("\r\n")
            environment.setdefault(author_key, value)
            environment.setdefault(committer_key, value)
    return environment
