from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_arena.doctor as doctor
from agent_arena.adapters import AdapterDescriptor, ResolvedAgentAdapter
from agent_arena.cli import app
from agent_arena.git_utils import git_subprocess_environment


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=git_subprocess_environment(),
    )


def _make_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "init", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=git_subprocess_environment(),
    )
    _run_git(path, "config", "user.name", "Agent Arena Tests")
    _run_git(path, "config", "user.email", "tests@example.invalid")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _run_git(path, "add", "tracked.txt")
    _run_git(path, "commit", "-m", "initial commit")
    return path


def _write_config(repository: Path, command: str | None = None, extra: str = "") -> Path:
    configured_command = command or sys.executable
    config_path = repository / ".agent-arena.toml"
    config_path.write_text(
        f'version = 1\n\n[agents.local]\ncommand = [{configured_command!r}, "-c", "pass"]\n{extra}',
        encoding="utf-8",
    )
    return config_path


def _check(report: doctor.DoctorReport, name: str) -> doctor.DoctorCheck:
    return next(check for check in report.checks if check.name == name)


def test_doctor_accepts_a_clean_repository_with_an_untracked_config(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository)
    status_before = _run_git(repository, "status", "--porcelain=v1").stdout
    index_before = (repository / ".git" / "index").read_bytes()

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    assert report.ready
    assert _check(report, "Repository cleanliness").status == "pass"
    assert _run_git(repository, "status", "--porcelain=v1").stdout == status_before
    assert (repository / ".git" / "index").read_bytes() == index_before


def test_doctor_fails_when_repository_has_uncommitted_source_changes(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    cleanliness = _check(report, "Repository cleanliness")
    assert not report.ready
    assert cleanliness.status == "fail"
    assert "commit or stash" in (cleanliness.suggestion or "").lower()


def test_doctor_fails_when_configuration_is_missing(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    configuration = _check(report, "Configuration")
    assert not report.ready
    assert configuration.status == "fail"
    assert "arena init" in (configuration.suggestion or "")


def test_doctor_skips_config_files_on_unc_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    monkeypatch.setattr(
        doctor,
        "_read_config",
        lambda _path: pytest.fail("Network config path was read"),
    )

    report = doctor.run_doctor(repository, Path("//server/share/arena.toml"))

    configuration = _check(report, "Configuration")
    assert not report.ready
    assert configuration.status == "fail"
    assert "network" in configuration.detail


def test_doctor_rejects_unc_gitdir_before_repository_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: //server/share/repo.git\n", encoding="utf-8")
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git followed the unsafe .git pointer")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert repository_check.status == "fail"
    assert "network" in repository_check.detail
    assert git_calls == [("--version",)]


def test_doctor_rejects_git_config_includes_before_repository_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    with (repository / ".git" / "config").open("a", encoding="utf-8") as config_file:
        config_file.write('\n[include]\n\tpath = "//server/share/gitconfig"\n')
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git loaded an unsafe config include")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert repository_check.status == "fail"
    assert "includes" in repository_check.detail
    assert git_calls == [("--version",)]


def test_doctor_rejects_global_git_config_includes_for_non_repository_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "not-a-repository"
    repository.mkdir()
    global_config = tmp_path / "global-gitconfig"
    global_config.write_text('[include]\n\tpath = "//server/share/gitconfig"\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "_git_config_paths", lambda _git_executable: [global_config])
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git loaded an unsafe global config include")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path("config.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert repository_check.status == "fail"
    assert "includes" in repository_check.detail
    assert git_calls == [("--version",)]


def test_doctor_rejects_multiline_git_config_network_paths_before_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    with (repository / ".git" / "config").open("a", encoding="utf-8") as config_file:
        config_file.write("\n[core]\n\texcludesFile = \\\n\t//server/share/excludes\n")
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git loaded a multi-line remote excludesFile")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert repository_check.status == "fail"
    assert "network" in repository_check.detail
    assert git_calls == [("--version",)]


def test_doctor_checks_bare_git_metadata_before_repository_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer_repository = tmp_path / "outer-repository"
    (outer_repository / ".git").mkdir(parents=True)
    repository = outer_repository / "bare"
    repository.mkdir()
    (repository / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repository / "config").write_text(
        '[include]\n\tpath = "//server/share/gitconfig"\n', encoding="utf-8"
    )
    (repository / "objects").mkdir()
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git loaded an unsafe bare-repository config")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path("config.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert "includes" in repository_check.detail
    assert git_calls == [("--version",)]


def test_doctor_prefers_explicit_git_directory_over_bare_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    (repository / "HEAD").write_text("ordinary project file\n", encoding="utf-8")
    (repository / "config").write_text("ordinary project file\n", encoding="utf-8")
    (repository / "objects").mkdir()
    with (repository / ".git" / "config").open("a", encoding="utf-8") as config_file:
        config_file.write('\n[include]\n\tpath = "//server/share/gitconfig"\n')
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git loaded an unsafe config from the explicit .git directory")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert repository_check.status == "fail"
    assert "includes" in repository_check.detail
    assert git_calls == [("--version",)]


def test_doctor_rejects_remote_object_alternates_before_repository_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    alternates = repository / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text("//server/share/objects\n", encoding="utf-8")
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git read an unsafe object alternate")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert repository_check.status == "fail"
    assert "alternates" in repository_check.detail
    assert git_calls == [("--version",)]


def test_metadata_tree_rejects_links_inside_refs_or_objects(tmp_path: Path) -> None:
    metadata = tmp_path / "git-metadata"
    ref_directory = metadata / "refs" / "heads"
    ref_directory.mkdir(parents=True)
    target = tmp_path / "target-ref"
    target.write_text("ref target\n", encoding="utf-8")
    link = ref_directory / "main"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable in this environment")

    assert doctor._metadata_tree_has_reparse_point(metadata)


def test_metadata_tree_rejects_scandir_iteration_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = tmp_path / "git-metadata"
    metadata.mkdir()

    class FailingDirectoryIterator:
        def __enter__(self) -> FailingDirectoryIterator:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self) -> FailingDirectoryIterator:
            return self

        def __next__(self) -> os.DirEntry[str]:
            raise OSError("injected directory iteration failure")

    real_scandir = os.scandir

    def failing_scandir(path: str | os.PathLike[str]) -> os.ScandirIterator[str]:
        if Path(path) == metadata:
            return FailingDirectoryIterator()  # type: ignore[return-value]
        return real_scandir(path)

    monkeypatch.setattr(doctor.os, "scandir", failing_scandir)

    assert doctor._metadata_tree_has_reparse_point(metadata)


def test_doctor_blocks_links_inside_git_metadata_before_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    linked_ref = repository / ".git" / "refs" / "heads" / "unsafe"
    target = tmp_path / "external-ref"
    target.write_text("external ref content\n", encoding="utf-8")
    try:
        linked_ref.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable in this environment")
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git followed a symlink inside its metadata")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert "symlink" in repository_check.detail.lower()
    assert git_calls == [("--version",)]


def test_doctor_blocks_remote_linked_worktree_metadata_before_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    linked_worktree_metadata = repository / ".git" / "worktrees" / "remote"
    linked_worktree_metadata.mkdir(parents=True)
    (linked_worktree_metadata / "gitdir").write_text(
        "//server/share/checkout/.git\n", encoding="utf-8"
    )
    real_git = doctor._git
    git_calls: list[tuple[str, ...]] = []

    def recording_git(*arguments: str, **kwargs: object):
        git_calls.append(arguments)
        if arguments == ("--version",):
            return real_git(*arguments, **kwargs)
        pytest.fail("Git followed an unsafe linked-worktree pointer")

    monkeypatch.setattr(doctor, "_git", recording_git)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    repository_check = _check(report, "Repository")
    assert not report.ready
    assert "worktree metadata" in repository_check.detail.lower()
    assert git_calls == [("--version",)]


def test_doctor_strips_git_trace_file_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = tmp_path / "doctor-trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace_path))

    result = doctor._git("--version")

    assert result is not None
    assert result.returncode == 0
    assert not trace_path.exists()


def test_doctor_reports_missing_git_without_running_repository_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    monkeypatch.setattr(doctor, "_which_local", lambda _name: None)
    monkeypatch.setattr(doctor, "_git", lambda *_args: pytest.fail("Git probe was run"))

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    git_check = _check(report, "Git")
    assert not report.ready
    assert git_check.status == "fail"
    assert "install git" in (git_check.suggestion or "").lower()


def test_doctor_reports_a_missing_command_with_a_fix(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository, "agent-arena-missing-command-for-test")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    command_check = _check(report, "Agent local")
    assert not report.ready
    assert command_check.status == "fail"
    assert "PATH" in (command_check.suggestion or "")


def test_doctor_warns_when_executable_uses_a_worktree_placeholder(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository, "{worktree}/bin/agent")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    command_check = _check(report, "Agent local")
    assert report.ready
    assert command_check.status == "warn"
    assert "placeholder" in command_check.detail


def test_doctor_warns_for_a_relative_symlink_executable(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    ignored_target = repository / ".venv" / "bin" / "agent"
    ignored_target.parent.mkdir(parents=True)
    ignored_target.write_text("local-only executable\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    executable_link = repository / "bin" / "agent"
    executable_link.parent.mkdir()
    try:
        executable_link.symlink_to("../.venv/bin/agent")
    except OSError:
        pytest.skip("Symlink creation is unavailable in this environment")
    _run_git(repository, "add", ".gitignore", "bin/agent")
    _run_git(repository, "commit", "-m", "add a relative executable symlink")
    _write_config(repository, "./bin/agent")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    command_check = _check(report, "Agent local")
    assert report.ready
    assert command_check.status == "warn"
    assert (
        "symlink" in command_check.detail.lower()
        or "could not be checked" in command_check.detail.lower()
    )


def test_doctor_does_not_accept_an_ignored_local_executable(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    (repository / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _run_git(repository, "add", ".gitignore")
    _run_git(repository, "commit", "-m", "ignore local environments")
    ignored_executable = repository / ".venv" / "bin" / "agent"
    ignored_executable.parent.mkdir(parents=True)
    ignored_executable.write_text("local only\n", encoding="utf-8")
    _write_config(repository, "./.venv/bin/agent")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    command_check = _check(report, "Agent local")
    assert not report.ready
    assert command_check.status == "fail"
    assert "run context" in command_check.detail


def test_doctor_skips_unc_executable_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda *_args, **_kwargs: pytest.fail("Network executable path was probed"),
    )

    assert doctor._find_executable("//server/share/agent.exe", repository) is False


def test_doctor_treats_windows_device_namespace_as_unsafe() -> None:
    assert doctor._is_remote_path(r"\\.\UNC\server\share\agent.exe")


def test_doctor_allows_local_symlinked_executable_paths(
    tmp_path: Path,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    executable_link = tmp_path / ("agent-python.exe" if os.name == "nt" else "agent-python")
    try:
        executable_link.symlink_to(sys.executable)
    except OSError:
        pytest.skip("Symlink creation is unavailable in this environment")

    assert doctor._find_executable(str(executable_link), repository) is True


def test_doctor_reports_missing_inherited_environment_names_without_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    config_path = repository / ".agent-arena.toml"
    config_path.write_text(
        "version = 1\n\n"
        "[agents.local]\n"
        f'command = [{sys.executable!r}, "-c", "pass"]\n'
        'inherit_env = ["ARENA_DOCTOR_TOKEN"]\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ARENA_DOCTOR_TOKEN", raising=False)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    environment_check = _check(report, "Environment for local")
    assert not report.ready
    assert environment_check.status == "fail"
    assert "ARENA_DOCTOR_TOKEN" in (environment_check.detail + (environment_check.suggestion or ""))


def test_doctor_never_displays_an_inherited_environment_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    config_path = repository / ".agent-arena.toml"
    config_path.write_text(
        "version = 1\n\n"
        "[agents.local]\n"
        f'command = [{sys.executable!r}, "-c", "pass"]\n'
        'inherit_env = ["ARENA_DOCTOR_TOKEN"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ARENA_DOCTOR_TOKEN", "do-not-print-this-token")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    assert report.ready
    assert "do-not-print-this-token" not in repr(report)


def test_doctor_checks_only_selected_profiles(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    (repository / ".agent-arena.toml").write_text(
        "version = 1\n\n"
        f'[agents.ready]\ncommand = [{sys.executable!r}, "-c", "pass"]\n\n'
        '[agents.unavailable]\ncommand = ["agent-arena-missing-selected-profile"]\n',
        encoding="utf-8",
    )

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"), ["ready"])

    assert report.ready
    assert any(check.name == "Agent ready" for check in report.checks)
    assert not any(check.name == "Agent unavailable" for check in report.checks)


def test_doctor_blocks_more_profiles_than_a_run_can_accept(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    profiles = "\n\n".join(
        f'[agents.agent{index}]\ncommand = [{sys.executable!r}, "-c", "pass"]'
        for index in range(17)
    )
    (repository / ".agent-arena.toml").write_text(f"version = 1\n\n{profiles}\n", encoding="utf-8")

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    agent_limit = _check(report, "Agent limit")
    assert not report.ready
    assert agent_limit.status == "fail"
    assert "16-agent" in agent_limit.detail
    assert not any(check.name.startswith("Agent agent") for check in report.checks)


def test_doctor_fails_when_a_selected_profile_is_unknown(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"), ["missing"])

    selection = _check(report, "Agent selection")
    assert not report.ready
    assert selection.status == "fail"
    assert "missing" in selection.detail


def test_doctor_checks_plugin_metadata_without_loading_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    (repository / ".agent-arena.toml").write_text(
        'version = 1\n\n[agents.plugin]\nadapter = "example-plugin"\n',
        encoding="utf-8",
    )
    descriptor = AdapterDescriptor(
        name="example-plugin",
        distribution="example-plugin-package",
        version="1.0",
        entry_point="example_plugin:Adapter",
    )
    monkeypatch.setattr(
        doctor,
        "resolve_agent_adapter",
        lambda _name: ResolvedAgentAdapter(descriptor, None),
    )

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    adapter_check = _check(report, "Adapter plugin")
    assert report.ready
    assert adapter_check.status == "pass"
    assert "not run" in adapter_check.detail.lower()


def test_doctor_checks_docker_for_plugin_container_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    (repository / ".agent-arena.toml").write_text(
        'version = 1\n\n[agents.plugin]\nadapter = "example-plugin"\n\n'
        '[agents.plugin.container]\nimage = "example:latest"\n',
        encoding="utf-8",
    )
    descriptor = AdapterDescriptor(
        name="example-plugin",
        distribution="example-plugin-package",
        version="1.0",
        entry_point="example_plugin:Adapter",
    )
    monkeypatch.setattr(
        doctor,
        "resolve_agent_adapter",
        lambda _name: ResolvedAgentAdapter(descriptor, None),
    )
    real_which = doctor._which_local
    monkeypatch.setattr(
        doctor,
        "_which_local",
        lambda command: None if command == "docker" else real_which(command),
    )

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    container_check = _check(report, "Agent plugin")
    assert not report.ready
    assert container_check.status == "fail"
    assert "Docker was not found" in container_check.detail


@pytest.mark.parametrize("modify_worktree", [False, True])
def test_doctor_does_not_execute_git_clean_filters(
    tmp_path: Path,
    modify_worktree: bool,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    marker = tmp_path / "filter-ran.txt"
    filter_script = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    filter_command = f"{shlex.quote(sys.executable)} -c {shlex.quote(filter_script)}"
    _run_git(repository, "config", "filter.doctor.clean", filter_command)
    _run_git(repository, "config", "filter.doctor.required", "true")
    (repository / ".gitattributes").write_text("tracked.txt filter=doctor\n", encoding="utf-8")
    _run_git(repository, "add", ".gitattributes")
    _run_git(repository, "commit", "-m", "configure custom clean filter")
    if modify_worktree:
        (repository / "tracked.txt").write_text(
            "changed and requires conversion\n", encoding="utf-8"
        )
    _write_config(repository)
    marker.unlink(missing_ok=True)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    assert not report.ready
    cleanliness = _check(report, "Repository cleanliness")
    assert cleanliness.status == "fail"
    assert "cannot be checked" in cleanliness.detail
    assert not marker.exists()


def test_doctor_blocks_when_repository_contains_submodules(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    commit = _run_git(repository, "rev-parse", "HEAD").stdout.strip()
    _run_git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{commit},nested",
    )
    _run_git(repository, "commit", "-m", "record submodule")
    _write_config(repository)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    submodules = _check(report, "Submodules")
    assert not report.ready
    assert submodules.status == "fail"
    assert "contains submodules" in submodules.detail
    assert not any(
        check.name == "Repository cleanliness" and check.status == "pass" for check in report.checks
    )


def test_doctor_cli_returns_nonzero_when_a_configured_executable_is_missing(
    tmp_path: Path,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository, "agent-arena-missing-command-for-cli-test")

    result = CliRunner().invoke(app, ["doctor", "--repo", str(repository)])
    output = " ".join(result.stdout.split())

    assert result.exit_code == 1
    assert "not found" in output.lower()
    assert "PATH" in output


def test_doctor_cli_returns_success_and_explains_warnings(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path / "repo")
    _write_config(repository)

    result = CliRunner().invoke(app, ["doctor", "--repo", str(repository), "--agent", "local"])
    output = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "No project verification checks are configured" in output
    assert "No blocking prerequisites found" in output


def test_doctor_never_launches_agent_or_project_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _make_repository(tmp_path / "repo")
    (repository / ".agent-arena.toml").write_text(
        "version = 1\n"
        f'checks = [[{sys.executable!r}, "-c", "raise SystemExit(9)"]]\n\n'
        "[agents.local]\n"
        f'command = [{sys.executable!r}, "-c", "raise SystemExit(8)"]\n',
        encoding="utf-8",
    )
    observed_commands: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(command: list[str], *args: object, **kwargs: object):
        observed_commands.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(doctor.subprocess, "run", recording_run)

    report = doctor.run_doctor(repository, Path(".agent-arena.toml"))

    assert report.ready
    assert all(Path(command[0]).stem.lower() == "git" for command in observed_commands)
