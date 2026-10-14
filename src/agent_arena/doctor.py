"""Read-only checks for Agent Arena's local run prerequisites."""

from __future__ import annotations

import ntpath
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_arena.adapters import resolve_agent_adapter
from agent_arena.errors import ArenaError
from agent_arena.git_utils import git_subprocess_environment
from agent_arena.runner import _MAX_AGENTS_PER_RUN, AgentProfile, _read_config

DoctorStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    """One prerequisite result with an optional corrective action."""

    name: str
    status: DoctorStatus
    detail: str
    suggestion: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    """The checks needed before running agents in a local repository."""

    checks: tuple[DoctorCheck, ...]

    @property
    def ready(self) -> bool:
        """Return whether the report contains no blocking failures."""
        return not any(check.status == "fail" for check in self.checks)

    @property
    def warning_count(self) -> int:
        """Return the number of checks that need a manual follow-up."""
        return sum(check.status == "warn" for check in self.checks)


def _check(
    name: str,
    status: DoctorStatus,
    detail: str,
    suggestion: str | None = None,
) -> DoctorCheck:
    return DoctorCheck(name=name, status=status, detail=detail, suggestion=suggestion)


def _git(
    *arguments: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    git_executable = _which_local("git")
    if git_executable is None:
        return None
    environment = git_subprocess_environment()
    for name in tuple(environment):
        if name.upper().startswith("GIT_TRACE"):
            environment.pop(name)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["PATH"] = os.pathsep.join(_local_path_entries())
    try:
        return subprocess.run(
            [git_executable, *arguments],
            stdin=None if input_text is not None else subprocess.DEVNULL,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _is_remote_path(path: str) -> bool:
    """Return whether a path explicitly names a Windows network share/drive."""
    normalized = path.replace("/", "\\").lower()
    if normalized.startswith("\\\\?\\unc\\"):
        return True
    if normalized.startswith("\\\\.\\"):
        return True
    if path.startswith("//") and not normalized.startswith("\\\\?\\"):
        return True
    if path.startswith("\\\\") and not normalized.startswith(("\\\\?\\", "\\\\.\\")):
        return True
    if os.name == "nt":
        drive, _ = ntpath.splitdrive(path)
        if len(drive) == 2 and drive[1] == ":":
            try:
                import ctypes

                return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == 4
            except (AttributeError, OSError):
                return True
    return False


def _contains_reparse_point(path: str | Path) -> bool:
    """Return whether a path follows a reparse point to a remote or unknown target."""

    def inspect(candidate: str | Path, seen: set[str]) -> bool:
        absolute = Path(os.path.abspath(candidate))
        if _is_remote_path(str(absolute)):
            return True
        anchor = Path(absolute.anchor)
        current = anchor
        for component in absolute.parts[len(anchor.parts) :]:
            current = current / component
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                return False
            except OSError:
                return True
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
                key = os.path.normcase(str(current))
                if key in seen:
                    return True
                seen.add(key)
                try:
                    target = Path(os.readlink(current))
                except OSError:
                    return True
                if not target.is_absolute():
                    target = current.parent / target
                if _is_remote_path(str(target)) or inspect(target, seen):
                    return True
                current = Path(os.path.abspath(target))
        return False

    return inspect(path, set())


def _has_reparse_point(path: str | Path) -> bool:
    """Return whether any existing component is a symlink or junction."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[len(current.parts) :]:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _metadata_tree_has_reparse_point(root: Path) -> bool:
    """Reject links anywhere in Git metadata before Git can follow one."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            directory_metadata = directory.lstat()
        except OSError:
            return True
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or getattr(directory_metadata, "st_file_attributes", 0) & 0x400
        ):
            return True
        try:
            entries = os.scandir(directory)
        except OSError:
            return True
        try:
            with entries:
                for entry in entries:
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        return True
                    if (
                        stat.S_ISLNK(metadata.st_mode)
                        or getattr(metadata, "st_file_attributes", 0) & 0x400
                    ):
                        return True
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(Path(entry.path))
        except OSError:
            return True
    return False


def _executable_variants(executable: str) -> list[str]:
    variants = [executable]
    if os.name == "nt":
        extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        if Path(executable).suffix.lower() not in {extension.lower() for extension in extensions}:
            variants.extend(executable + extension for extension in extensions if extension)
    return variants


def _which_local(executable: str) -> str | None:
    """Resolve a command without following network or symlink PATH entries."""
    path = Path(executable)
    if path.is_absolute() or "/" in executable or "\\" in executable:
        candidate_names = _executable_variants(str(path))
        directories = [""]
    else:
        candidate_names = _executable_variants(executable)
        directories = _local_path_entries()

    for directory in directories:
        for name in candidate_names:
            candidate = Path(os.path.abspath(Path(directory) / name))
            if _is_remote_path(str(candidate)) or _contains_reparse_point(candidate):
                continue
            found = shutil.which(str(candidate), path="")
            if found is not None:
                return found
    return None


def _local_path_entries() -> list[str]:
    return [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not _is_remote_path(entry) and not _contains_reparse_point(entry)
    ]


def _find_executable(executable: str, repository: Path) -> bool | None:
    """Check whether an executable will be available in the agent worktree.

    Return ``None`` when a run-time placeholder prevents a safe preflight.
    """
    if any(placeholder in executable for placeholder in ("{task}", "{worktree}", "{base_sha}")):
        return None
    executable = executable.replace("{repo}", str(repository))
    path = Path(executable)
    if _is_remote_path(executable):
        return False
    if path.is_absolute():
        candidate = str(path)
    elif "/" in executable or "\\" in executable:
        if ".." in path.parts:
            return False
        relative_path = path.as_posix()
        tracked = _git(
            "--literal-pathspecs",
            "-C",
            str(repository),
            "ls-files",
            "--error-unmatch",
            "--",
            relative_path,
        )
        if tracked is None or tracked.returncode != 0:
            return False
        candidate = str(repository / path)
    else:
        candidate = executable
    if _contains_reparse_point(candidate):
        return False
    if not path.is_absolute() and ("/" in executable or "\\" in executable):
        if _has_reparse_point(candidate):
            return None
    return _which_local(candidate) is not None


def _git_config_paths(git_executable: str) -> list[Path]:
    """Return Git config files that may be read by the doctor subprocesses."""
    candidates: set[Path] = set()
    home_paths = {Path.home()}
    home_override = os.environ.get("HOME")
    if home_override:
        home_paths.add(Path(home_override).expanduser())
    for home in home_paths:
        candidates.add(home / ".gitconfig")
        xdg = os.environ.get("XDG_CONFIG_HOME")
        candidates.add((Path(xdg).expanduser() if xdg else home / ".config") / "git" / "config")

    if os.name == "nt":
        executable_path = Path(git_executable)
        candidates.update(parent / "etc" / "gitconfig" for parent in executable_path.parents)
    else:
        candidates.update({Path("/etc/gitconfig"), Path("/usr/local/etc/gitconfig")})
    return sorted(candidates, key=lambda path: str(path))


def _git_config_value(value: str) -> str:
    """Read the scalar portion of a Git config value without interpreting commands."""
    value = value.strip()
    if value.startswith('"'):
        escaped = False
        result: list[str] = []
        for character in value[1:]:
            if escaped:
                result.append({"n": "\n", "t": "\t", "b": "\b"}.get(character, character))
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                return "".join(result)
            else:
                result.append(character)
        return ""
    return re.split(r"\s[;#]", value, maxsplit=1)[0].strip()


def _git_config_issue(config_path: Path, repository: Path | None) -> str | None:
    """Reject config directives that could make local Git probes read remote files."""
    if _is_remote_path(str(config_path)) or _contains_reparse_point(config_path):
        return "Git configuration resolves through a network or unsafe path."
    try:
        config_path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "Git configuration could not be inspected safely."
    try:
        contents = config_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return "Git configuration could not be inspected safely."

    logical_lines: list[str] = []
    pending_line = ""
    for line in contents.splitlines():
        combined = pending_line + line
        trailing_backslashes = len(combined) - len(combined.rstrip("\\"))
        if trailing_backslashes % 2:
            pending_line = combined[:-1]
            continue
        logical_lines.append(combined)
        pending_line = ""
    if pending_line:
        return "Git configuration could not be inspected safely."

    section = ""
    section_pattern = re.compile(r'^\s*\[\s*([^\s\]".]+)', re.IGNORECASE)
    assignment_pattern = re.compile(r"^\s*([A-Za-z][A-Za-z0-9.-]*)\s*(?:=\s*(.*))?$")
    include_pattern = re.compile(r"^\s*\[\s*include(?:if)?(?:\s|\])", re.IGNORECASE)
    for line in logical_lines:
        if include_pattern.match(line):
            return "Git configuration includes are disabled by the read-only safety check."
        section_match = section_pattern.match(line)
        if section_match:
            section = section_match.group(1).lower()
            continue
        if section != "core":
            continue
        assignment = assignment_pattern.match(line)
        if assignment is None:
            continue
        key, raw_value = assignment.groups()
        if key.lower() not in {"attributesfile", "excludesfile", "worktree"} or raw_value is None:
            continue
        value = _git_config_value(raw_value)
        if not value:
            continue
        external_path = Path(value).expanduser()
        if not external_path.is_absolute() and repository is not None:
            external_path = repository / external_path
        if _is_remote_path(str(external_path)) or _contains_reparse_point(external_path):
            return "Git configuration points to a network or unsafe path."
    return None


def _read_git_pointer(path: Path, key: str, base: Path) -> tuple[Path | None, str | None]:
    """Read a .git or commondir pointer after validating the pointer file and target."""
    if _is_remote_path(str(path)) or _contains_reparse_point(path):
        return None, "Git metadata points through a network or unsafe path."
    try:
        contents = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return None, "Git metadata pointer could not be inspected safely."
    prefix = f"{key}:" if key == "gitdir" else ""
    value = next(
        (
            line[len(prefix) :].strip()
            for line in contents
            if (line.startswith(prefix) if prefix else bool(line.strip()))
        ),
        "",
    )
    if not value:
        return None, "Git metadata pointer has an unsupported format."
    target = Path(value)
    if not target.is_absolute():
        target = base / target
    target = Path(os.path.abspath(target))
    if _is_remote_path(str(target)) or _contains_reparse_point(target):
        return None, "Git metadata points to a network or unsafe path."
    if not target.is_dir():
        return None, "Git metadata pointer does not name an accessible local directory."
    return target.resolve(), None


def _git_metadata_issue(repository_path: Path, git_executable: str) -> str | None:
    """Inspect Git metadata paths before Git follows repository pointers or config."""
    for config_path in _git_config_paths(git_executable):
        issue = _git_config_issue(config_path, repository_path)
        if issue:
            return issue

    marker: Path | None = repository_path / ".git"
    try:
        marker.lstat()
    except FileNotFoundError:
        marker = None
    except OSError:
        return "Git metadata could not be inspected safely."

    if marker is None:
        bare_markers = True
        for name in ("HEAD", "config", "objects"):
            metadata_path = repository_path / name
            if _is_remote_path(str(metadata_path)) or _contains_reparse_point(metadata_path):
                return "Git metadata points through a network or unsafe path."
            try:
                metadata_path.lstat()
            except FileNotFoundError:
                bare_markers = False
            except OSError:
                return "Git metadata could not be inspected safely."

        if bare_markers:
            git_dir = repository_path
        else:
            for parent in repository_path.parents:
                candidate = parent / ".git"
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    return "Git metadata could not be inspected safely."
                marker = candidate
                break
            if marker is None:
                return None

    if marker is not None:
        if _is_remote_path(str(marker)) or _contains_reparse_point(marker):
            return "Git metadata points through a network or unsafe path."

        if marker.is_dir():
            git_dir = marker.resolve()
        elif marker.is_file():
            git_dir, issue = _read_git_pointer(marker, "gitdir", marker.parent)
            if issue:
                return issue
            if git_dir is None:
                return "Git metadata pointer could not be inspected safely."
        else:
            return "Git metadata marker has an unsupported file type."

    common_dir = git_dir
    common_pointer = git_dir / "commondir"
    if _is_remote_path(str(common_pointer)) or _contains_reparse_point(common_pointer):
        return "Git common-directory pointer uses a network or unsafe path."
    try:
        common_pointer.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return "Git common-directory pointer could not be inspected safely."
    else:
        common_dir, issue = _read_git_pointer(common_pointer, "", git_dir)
        if issue:
            return issue
        if common_dir is None:
            return "Git common-directory pointer could not be inspected safely."
    metadata_dirs = list(dict.fromkeys((git_dir, common_dir)))
    if any(_metadata_tree_has_reparse_point(metadata_dir) for metadata_dir in metadata_dirs):
        return "Git metadata contains a symlink or junction and cannot be inspected safely."
    for metadata_dir in metadata_dirs:
        for relative in (
            Path("config"),
            Path("config.worktree"),
            Path("HEAD"),
            Path("index"),
            Path("packed-refs"),
            Path("refs"),
            Path("objects"),
            Path("objects/pack"),
            Path("info/exclude"),
            Path("info/attributes"),
        ):
            metadata_path = metadata_dir / relative
            if _is_remote_path(str(metadata_path)) or _contains_reparse_point(metadata_path):
                return "Git metadata points through a network or unsafe path."
        for config_path in (metadata_dir / "config", metadata_dir / "config.worktree"):
            issue = _git_config_issue(config_path, repository_path)
            if issue:
                return issue
        alternates = metadata_dir / "objects" / "info" / "alternates"
        if _is_remote_path(str(alternates)) or _contains_reparse_point(alternates):
            return "Git object alternates point through a network or unsafe path."
        try:
            alternates.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return "Git object alternates could not be inspected safely."
        else:
            try:
                alternate_paths = alternates.read_text(
                    encoding="utf-8", errors="strict"
                ).splitlines()
            except (OSError, UnicodeError):
                return "Git object alternates could not be inspected safely."
            for alternate in alternate_paths:
                if not alternate.strip():
                    continue
                alternate_path = Path(alternate.strip())
                if not alternate_path.is_absolute():
                    alternate_path = metadata_dir / "objects" / alternate_path
                if _is_remote_path(str(alternate_path)) or _contains_reparse_point(alternate_path):
                    return "Git object alternates point through a network or unsafe path."

    worktrees_dir = common_dir / "worktrees"
    if _is_remote_path(str(worktrees_dir)) or _contains_reparse_point(worktrees_dir):
        return "Git worktree metadata points through a network or unsafe path."
    try:
        worktree_entries = list(worktrees_dir.iterdir())
    except FileNotFoundError:
        worktree_entries = []
    except OSError:
        return "Git worktree metadata could not be inspected safely."
    for worktree_entry in worktree_entries:
        if _is_remote_path(str(worktree_entry)) or _contains_reparse_point(worktree_entry):
            return "Git worktree metadata points through a network or unsafe path."
        for filename in ("HEAD", "index", "config.worktree"):
            metadata_path = worktree_entry / filename
            if _is_remote_path(str(metadata_path)) or _contains_reparse_point(metadata_path):
                return "Git worktree metadata points through a network or unsafe path."
        worktree_config = worktree_entry / "config.worktree"
        issue = _git_config_issue(worktree_config, repository_path)
        if issue:
            return issue
        worktree_gitdir = worktree_entry / "gitdir"
        if _is_remote_path(str(worktree_gitdir)) or _contains_reparse_point(worktree_gitdir):
            return "Git worktree metadata points through a network or unsafe path."
        try:
            worktree_gitdir.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return "Git worktree metadata could not be inspected safely."
        else:
            try:
                target_text = worktree_gitdir.read_text(encoding="utf-8", errors="strict").strip()
            except (OSError, UnicodeError):
                return "Git worktree metadata could not be inspected safely."
            if target_text:
                target = Path(target_text)
                if not target.is_absolute():
                    target = worktree_entry / target
                if _is_remote_path(str(target)) or _contains_reparse_point(target):
                    return "Git worktree metadata points through a network or unsafe path."
        common_pointer = worktree_entry / "commondir"
        if _is_remote_path(str(common_pointer)) or _contains_reparse_point(common_pointer):
            return "Git worktree metadata points through a network or unsafe path."
        try:
            common_pointer.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return "Git worktree metadata could not be inspected safely."
        else:
            _pointer, issue = _read_git_pointer(common_pointer, "", worktree_entry)
            if issue:
                return issue

    return None


def _active_git_filters(repository: Path) -> list[str] | None:
    """Find configured external filters selected by tracked repository paths."""
    result = _git(
        "-C",
        str(repository),
        "config",
        "--null",
        "--name-only",
        "--get-regexp",
        r"^filter\..*\.(clean|process|required)$",
    )
    if result is None or result.returncode not in (0, 1):
        return None

    drivers: set[str] = set()
    for key in result.stdout.split("\0"):
        for suffix in (".clean", ".process"):
            if key.startswith("filter.") and key.endswith(suffix):
                drivers.add(key[len("filter.") : -len(suffix)])
                break
    if not drivers:
        return []

    tracked = _git("-C", str(repository), "ls-files", "-z")
    if tracked is None or tracked.returncode != 0:
        return None
    for tracked_path in tracked.stdout.split("\0"):
        if not tracked_path:
            continue
        relative_path = Path(tracked_path)
        for parent in (Path("."), *relative_path.parents):
            attributes_path = repository / parent / ".gitattributes"
            if _contains_reparse_point(attributes_path):
                return None

    attributes = _git(
        "-C",
        str(repository),
        "check-attr",
        "-z",
        "--stdin",
        "filter",
        input_text=tracked.stdout,
    )
    if attributes is None or attributes.returncode != 0:
        return None

    fields = attributes.stdout.split("\0")
    active_drivers = {
        fields[index + 2]
        for index in range(0, len(fields) - 2, 3)
        if fields[index + 1] == "filter" and fields[index + 2] not in {"unspecified", "unset"}
    }
    return sorted(drivers & active_drivers)


def _selected_profiles(
    agents: dict[str, AgentProfile], selected_agents: list[str] | None
) -> tuple[list[tuple[str, AgentProfile]], list[str]]:
    if not selected_agents:
        return list(agents.items()), []

    missing = [name for name in selected_agents if name not in agents]
    selected = [(name, agents[name]) for name in dict.fromkeys(selected_agents) if name in agents]
    return selected, missing


def _check_repository(
    repository_path: Path,
    config_path: Path,
    checks: list[DoctorCheck],
) -> Path | None:
    git_executable = _which_local("git")
    if git_executable is None:
        checks.append(
            _check(
                "Git",
                "fail",
                "Git was not found on PATH.",
                "Install Git and make sure the git executable is available on PATH.",
            )
        )
        return None

    version = _git("--version")
    if version is None or version.returncode != 0:
        checks.append(
            _check(
                "Git",
                "fail",
                "Git could not be started successfully.",
                "Check the Git installation and try again.",
            )
        )
        return None
    checks.append(_check("Git", "pass", "Git is available on PATH."))

    candidate = Path(os.path.abspath(repository_path.expanduser()))
    if _is_remote_path(str(candidate)) or _contains_reparse_point(candidate):
        checks.append(
            _check(
                "Repository",
                "fail",
                "The selected repository path uses a network or symlinked location.",
                "Use a direct local checkout and rerun arena doctor.",
            )
        )
        return None
    candidate = candidate.resolve()
    if not candidate.is_dir():
        checks.append(
            _check(
                "Repository",
                "fail",
                "The selected repository directory does not exist.",
                "Pass an existing Git repository with --repo.",
            )
        )
        return None

    metadata_issue = _git_metadata_issue(candidate, git_executable)
    if metadata_issue:
        checks.append(
            _check(
                "Repository",
                "fail",
                metadata_issue,
                "Review the local .git metadata and Git config paths before running doctor.",
            )
        )
        return None

    top_level = _git("-C", str(candidate), "rev-parse", "--show-toplevel")
    if top_level is None or top_level.returncode != 0:
        checks.append(
            _check(
                "Repository",
                "fail",
                "The selected directory is not an accessible Git repository.",
                "Run 'git init' or pass the path to an existing Git repository.",
            )
        )
        return None

    repository_path = Path(top_level.stdout.strip())
    if _is_remote_path(str(repository_path)) or _contains_reparse_point(repository_path):
        checks.append(
            _check(
                "Repository",
                "fail",
                "Git resolved the repository metadata through a network or symlinked path.",
                "Use a direct local checkout and rerun arena doctor.",
            )
        )
        return None
    repository = repository_path.resolve()
    checks.append(_check("Repository", "pass", "The selected Git repository is accessible."))

    worktrees = _git("-C", str(repository), "worktree", "list", "--porcelain")
    if worktrees is None or worktrees.returncode != 0:
        checks.append(
            _check(
                "Git worktrees",
                "fail",
                "Git could not list worktrees for this repository.",
                "Check the repository metadata and install a Git version with worktree support.",
            )
        )
    else:
        checks.append(_check("Git worktrees", "pass", "Git worktree support is available."))

    head = _git("-C", str(repository), "rev-parse", "--verify", "HEAD")
    if head is None or head.returncode != 0:
        checks.append(
            _check(
                "Repository base commit",
                "fail",
                "The repository has no readable HEAD commit.",
                "Create at least one commit before running agents.",
            )
        )
    else:
        checks.append(_check("Repository base commit", "pass", "The repository has a HEAD commit."))

    active_git_filters = _active_git_filters(repository)
    if active_git_filters is None:
        checks.append(
            _check(
                "Repository cleanliness",
                "fail",
                "Git filter configuration could not be inspected safely.",
                "Check Git configuration, then rerun arena doctor.",
            )
        )
        return repository
    if active_git_filters:
        checks.append(
            _check(
                "Repository cleanliness",
                "fail",
                "Git clean/process filters are configured, so cleanliness cannot be checked "
                "without running external filter commands. The filters were not run.",
                "Review the working tree manually; the normal run preflight may invoke its "
                "configured Git filters.",
            )
        )
        return repository

    tracked_entries = _git("-C", str(repository), "ls-files", "--stage", "-z")
    if tracked_entries is None or tracked_entries.returncode != 0:
        checks.append(
            _check(
                "Submodules",
                "fail",
                "Git could not inspect tracked paths for submodules.",
                "Check the repository metadata, then rerun arena doctor.",
            )
        )
        return repository
    elif any(record.startswith("160000 ") for record in tracked_entries.stdout.split("\0")):
        checks.append(
            _check(
                "Submodules",
                "fail",
                "This repository contains submodules; doctor does not inspect their worktrees "
                "because Git status there could run configured filters.",
                "Check each submodule is clean before running Agent Arena.",
            )
        )
        return repository
    status_arguments = ["--no-pager"]
    status_arguments.extend(
        (
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repository),
            "status",
            "--ignore-submodules=all",
            "--porcelain=v1",
            "--untracked-files=all",
            "-z",
        )
    )
    status = _git(*status_arguments)
    if status is None or status.returncode != 0:
        checks.append(
            _check(
                "Repository cleanliness",
                "fail",
                "Git could not inspect the working tree.",
                "Check repository permissions and Git status, then rerun arena doctor.",
            )
        )
        return repository

    allowed_config: str | None = None
    config = config_path.expanduser()
    if not config.is_absolute():
        config = repository / config
    if not _is_remote_path(str(config)) and not _contains_reparse_point(config):
        try:
            allowed_config = config.resolve().relative_to(repository).as_posix()
        except ValueError:
            pass

    dirty_paths = []
    for record in status.stdout.split("\0"):
        if not record:
            continue
        state, separator, path = record.partition(" ")
        if not separator:
            path = record[3:]
        if state == "??" and path == allowed_config:
            continue
        dirty_paths.append(path)

    if dirty_paths:
        checks.append(
            _check(
                "Repository cleanliness",
                "fail",
                "The repository has uncommitted changes.",
                "Commit or stash changes first; only the selected untracked config is allowed.",
            )
        )
    else:
        checks.append(
            _check(
                "Repository cleanliness",
                "pass",
                "The working tree is clean except for the selected untracked config file.",
            )
        )
    return repository


def _check_agent(
    name: str,
    profile: AgentProfile,
    repository: Path,
    checks: list[DoctorCheck],
) -> None:
    adapter_available = False
    try:
        resolve_agent_adapter(profile.adapter_name)
    except ArenaError:
        checks.append(
            _check(
                f"Adapter {name}",
                "fail",
                "The configured adapter is not installed or could not be resolved.",
                "Install the adapter package with Agent Arena, then run 'arena adapters'.",
            )
        )
    else:
        adapter_available = True
        checks.append(
            _check(
                f"Adapter {name}",
                "pass",
                "Adapter metadata is installed; plugin code and provider command were not run.",
            )
        )

    if profile.container is not None:
        if _which_local("docker") is None:
            checks.append(
                _check(
                    f"Agent {name}",
                    "fail",
                    "This agent uses a container, but Docker was not found on PATH.",
                    "Install the Docker CLI and make it available on PATH.",
                )
            )
        else:
            checks.append(
                _check(
                    f"Agent {name}",
                    "warn",
                    "Docker CLI is available; runtime and image were not checked.",
                    "Confirm the configured image is local and can run the agent command.",
                )
            )
    elif adapter_available and profile.adapter_name == "command":
        command = profile.adapter_options["command"]
        executable_available = _find_executable(command[0], repository)
        if executable_available is None:
            checks.append(
                _check(
                    f"Agent {name}",
                    "warn",
                    "The executable uses a run-time placeholder or relative symlink and could "
                    "not be checked before a worktree exists.",
                    "Confirm the executable is available in the worktree created for the run.",
                )
            )
        elif not executable_available:
            checks.append(
                _check(
                    f"Agent {name}",
                    "fail",
                    "The configured executable was not found in the run context.",
                    "Install the provider CLI or add its executable to PATH.",
                )
            )
        else:
            checks.append(
                _check(f"Agent {name}", "pass", "The configured executable is available.")
            )

    current_environment = {key.upper() for key in os.environ}
    missing_environment = [
        variable for variable in profile.inherit_env if variable.upper() not in current_environment
    ]
    if missing_environment:
        checks.append(
            _check(
                f"Environment for {name}",
                "fail",
                "One or more configured environment variable names are missing: "
                + ", ".join(missing_environment)
                + ". Values were not read or displayed.",
                "Set the listed variables or remove them from inherit_env.",
            )
        )
    else:
        checks.append(
            _check(
                f"Environment for {name}",
                "pass",
                "Configured variable names are present; values were not read or displayed.",
            )
        )


def run_doctor(
    repository_path: Path,
    config_path: Path,
    selected_agents: list[str] | None = None,
) -> DoctorReport:
    """Inspect local run prerequisites without launching providers or changing files."""
    checks: list[DoctorCheck] = []
    repository = _check_repository(repository_path, config_path, checks)
    if repository is None:
        return DoctorReport(tuple(checks))

    config = config_path.expanduser()
    if not config.is_absolute():
        config = repository / config
    if _is_remote_path(str(config)) or _contains_reparse_point(config):
        checks.append(
            _check(
                "Configuration",
                "fail",
                "The selected config path uses a network or symlinked location.",
                "Copy the TOML config to a direct local path and rerun arena doctor.",
            )
        )
        return DoctorReport(tuple(checks))
    try:
        agents, project_checks = _read_config(config)
    except ArenaError as error:
        checks.append(
            _check(
                "Configuration",
                "fail",
                str(error),
                "Run 'arena init' to create a starter config or fix the selected TOML file.",
            )
        )
        return DoctorReport(tuple(checks))
    checks.append(_check("Configuration", "pass", "The selected TOML config is valid."))

    profiles, missing_agents = _selected_profiles(agents, selected_agents)
    if missing_agents:
        checks.append(
            _check(
                "Agent selection",
                "fail",
                "Selected agent profile(s) were not found: " + ", ".join(missing_agents) + ".",
                "Use 'arena doctor --help' or select a profile declared in the config.",
            )
        )
    if len(profiles) > _MAX_AGENTS_PER_RUN:
        checks.append(
            _check(
                "Agent limit",
                "fail",
                f"The selected profiles exceed the {_MAX_AGENTS_PER_RUN}-agent run limit.",
                f"Select no more than {_MAX_AGENTS_PER_RUN} agent profiles.",
            )
        )
        return DoctorReport(tuple(checks))
    if not profiles:
        return DoctorReport(tuple(checks))

    for name, profile in profiles:
        _check_agent(name, profile, repository, checks)

    if not project_checks:
        checks.append(
            _check(
                "Project checks",
                "warn",
                "No project verification checks are configured; run results will be unverified.",
                "Add repository-specific test or lint commands to the top-level checks array.",
            )
        )
    else:
        for index, command in enumerate(project_checks, start=1):
            executable_available = _find_executable(command[0], repository)
            if executable_available is None:
                checks.append(
                    _check(
                        f"Project check {index}",
                        "warn",
                        "The executable uses a run-time placeholder or relative symlink and "
                        "could not be checked before a worktree exists.",
                        "Confirm the check command is available in the worktree created for "
                        "the run.",
                    )
                )
            elif not executable_available:
                checks.append(
                    _check(
                        f"Project check {index}",
                        "fail",
                        "A configured verification executable was not found.",
                        "Install the check tool or update the command in the config.",
                    )
                )
            else:
                checks.append(
                    _check(
                        f"Project check {index}",
                        "pass",
                        "The configured verification executable is available.",
                    )
                )

    return DoctorReport(tuple(checks))
