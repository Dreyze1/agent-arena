"""Export one saved agent patch as a local branch without changing the checkout."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_arena.runner import (
    ArenaError,
    _git,
    _has_uncommitted_changes,
    _safe_name,
    resolve_repository,
    run_artifacts_directory,
)

_RUN_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MAX_MANIFEST_BYTES = 16_777_216
_MAX_PATCH_BYTES = 100_000_000


@dataclass(frozen=True)
class ExportResult:
    agent_name: str
    branch_name: str
    commit_sha: str
    base_sha: str
    run_id: str
    returncode: int | None
    timed_out: bool
    checks_passed: int
    checks_total: int
    cleanup_warning: str | None


def _load_run_manifest(run_dir: Path, run_id: str) -> dict[str, Any]:
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ArenaError(f"Run not found: {run_id}")
    manifest_path = run_dir / "run.json"
    if manifest_path.is_symlink():
        raise ArenaError("Run manifest is a symlink and cannot be exported.")
    try:
        with manifest_path.open("rb") as source:
            contents = source.read(_MAX_MANIFEST_BYTES + 1)
        if len(contents) > _MAX_MANIFEST_BYTES:
            raise ArenaError("Run manifest exceeds the 16 MiB export limit.")
        manifest = json.loads(contents.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArenaError(f"Could not read run manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
        raise ArenaError("Run manifest is invalid or does not match the requested run ID.")
    return manifest


def _selected_agent(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    agents = manifest.get("agents")
    if not isinstance(agents, list):
        raise ArenaError("Run manifest does not contain an agents list.")
    matches = [agent for agent in agents if isinstance(agent, dict) and agent.get("name") == name]
    if not matches:
        raise ArenaError(f"Agent {name!r} is not present in run {manifest['run_id']}.")
    if len(matches) != 1:
        raise ArenaError(f"Agent {name!r} appears more than once in the run manifest.")
    return matches[0]


def _selected_patch(run_dir: Path, agent: dict[str, Any], agent_name: str) -> Path:
    expected = Path("agents") / _safe_name(agent_name) / "patch.diff"
    stored_path = agent.get("patch_file")
    if not isinstance(stored_path, str) or Path(stored_path) != expected:
        raise ArenaError("Run manifest contains an unexpected patch path.")
    patch_path = run_dir / expected
    try:
        patch_path.resolve().relative_to(run_dir.resolve())
    except (OSError, ValueError) as error:
        raise ArenaError("Selected patch escapes the run artifact directory.") from error
    try:
        if patch_path.is_symlink() or not patch_path.is_file():
            raise ArenaError("Selected agent patch is missing or is a symlink.")
        if patch_path.stat().st_size > _MAX_PATCH_BYTES:
            raise ArenaError("Selected patch exceeds the 100 MB export limit.")
    except OSError as error:
        raise ArenaError(f"Could not inspect selected patch: {error}") from error
    return patch_path


def _validate_branch_name(repository: Path, branch_name: str) -> None:
    if not branch_name or branch_name.startswith("@{-"):
        raise ArenaError("Choose a plain local branch name.")
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "check-ref-format", "--branch", branch_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as error:
        raise ArenaError("Git was not found. Install Git and make sure it is on PATH.") from error
    if result.returncode:
        raise ArenaError(f"Invalid local branch name: {branch_name!r}")

    ref = f"refs/heads/{branch_name}"
    existing = subprocess.run(
        ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", ref],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if existing.returncode == 0:
        raise ArenaError(f"Local branch already exists: {branch_name}")
    if existing.returncode != 1:
        raise ArenaError("Could not check whether the requested branch already exists.")


def export_agent_branch(
    *,
    repository_path: Path,
    run_id: str,
    agent_name: str,
    branch_name: str | None = None,
) -> ExportResult:
    """Create a commit and local branch from a selected run's patch."""
    if not _RUN_ID.fullmatch(run_id):
        raise ArenaError("Invalid run ID format.")
    if not agent_name.strip():
        raise ArenaError("Agent name cannot be empty.")

    repository = resolve_repository(repository_path)
    config_path = repository / ".agent-arena.toml"
    allowed_untracked = {config_path} if config_path.is_relative_to(repository) else set()
    if _has_uncommitted_changes(repository, allowed_untracked):
        raise ArenaError(
            "The target repository has uncommitted changes. Commit or stash them before export."
        )

    run_dir = run_artifacts_directory(repository) / run_id
    manifest = _load_run_manifest(run_dir, run_id)
    recorded_repository = manifest.get("repository")
    if not isinstance(recorded_repository, str):
        raise ArenaError("Run manifest does not identify its source repository.")
    if Path(recorded_repository).expanduser().resolve() != repository:
        raise ArenaError("The selected run belongs to a different repository.")

    base_sha = manifest.get("base_sha")
    if not isinstance(base_sha, str) or not _OBJECT_ID.fullmatch(base_sha):
        raise ArenaError("Run manifest contains an invalid base commit.")
    agent = _selected_agent(manifest, agent_name)
    checks = agent.get("checks", [])
    if not isinstance(checks, list) or not all(isinstance(check, dict) for check in checks):
        raise ArenaError("Selected agent has invalid check results in the run manifest.")
    returncode = agent.get("returncode")
    if returncode is not None and (not isinstance(returncode, int) or isinstance(returncode, bool)):
        raise ArenaError("Selected agent has an invalid exit code in the run manifest.")
    timed_out = agent.get("timed_out")
    if not isinstance(timed_out, bool):
        raise ArenaError("Selected agent has an invalid timeout result in the run manifest.")
    patch_path = _selected_patch(run_dir, agent, agent_name)
    requested_branch = (
        branch_name if branch_name is not None else f"arena/{run_id}/{_safe_name(agent_name)}"
    )
    _validate_branch_name(repository, requested_branch)

    worktree_root = run_artifacts_directory(repository).parent / "exports"
    worktree_path = worktree_root / uuid.uuid4().hex
    try:
        worktree_root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            worktree_root.parent.chmod(0o700)
    except OSError as error:
        raise ArenaError(f"Could not prepare temporary export storage: {error}") from error

    worktree_created = False
    branch_created = False
    commit_sha = ""
    expected_tree = ""
    cleanup_warning: str | None = None
    try:
        _git(repository, "cat-file", "-e", f"{base_sha}^{{commit}}")
        _git(repository, "worktree", "add", "--detach", str(worktree_path), base_sha)
        worktree_created = True
        _git(worktree_path, "apply", "--index", "--binary", "--", str(patch_path))

        staged = subprocess.run(
            ["git", "-C", str(worktree_path), "diff", "--cached", "--quiet"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if staged.returncode == 0:
            raise ArenaError("The selected patch contains no changes.")
        if staged.returncode != 1:
            detail = staged.stderr.decode("utf-8", errors="replace").strip()
            raise ArenaError(f"Could not inspect applied patch: {detail}")

        expected_tree = _git(worktree_path, "write-tree").stdout.strip()
        safe_agent = _safe_name(agent_name)
        commit_message = f"Agent Arena: {safe_agent} result from {run_id}"
        _git(worktree_path, "commit", "-m", commit_message)
        commit_sha = _git(worktree_path, "rev-parse", "HEAD").stdout.strip()
        committed_tree = _git(worktree_path, "rev-parse", "HEAD^{tree}").stdout.strip()
        if committed_tree != expected_tree:
            raise ArenaError(
                "A Git commit hook changed the staged patch; no export branch was created."
            )
        _git(repository, "branch", "--", requested_branch, commit_sha)
        branch_created = True
    finally:
        if worktree_created:
            removal = subprocess.run(
                ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            if removal.returncode:
                detail = removal.stderr.decode("utf-8", errors="replace").strip()
                cleanup_warning = f"Temporary worktree remains at {worktree_path}: {detail}"
            subprocess.run(
                ["git", "-C", str(repository), "worktree", "prune"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    if not branch_created:
        raise ArenaError("Could not create the export branch.")
    return ExportResult(
        agent_name=agent_name,
        branch_name=requested_branch,
        commit_sha=commit_sha,
        base_sha=base_sha,
        run_id=run_id,
        returncode=returncode,
        timed_out=timed_out,
        checks_passed=sum(
            check.get("returncode") == 0 and not check.get("timed_out") for check in checks
        ),
        checks_total=len(checks),
        cleanup_warning=cleanup_warning,
    )
