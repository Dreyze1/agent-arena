import os
import subprocess
from pathlib import Path

import pytest

from agent_arena.git_utils import git_commit_environment
from agent_arena.runner import _git


def _run_git(repository: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


@pytest.mark.parametrize("config_variable", ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"])
def test_git_commit_environment_uses_configured_identity_and_drops_overrides(
    tmp_path: Path,
    monkeypatch,
    config_variable: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    global_config = tmp_path / "custom.gitconfig"
    global_config.write_text(
        "[user]\n\tname = Configured Export User\n\temail = export@example.invalid\n",
        encoding="utf-8",
    )

    git_environment = os.environ.copy()
    for name in tuple(git_environment):
        if name.upper().startswith("GIT_"):
            git_environment.pop(name)
    git_environment[config_variable] = str(global_config)
    if config_variable == "GIT_CONFIG_GLOBAL":
        git_environment["GIT_CONFIG_NOSYSTEM"] = "1"
    git_environment["HOME"] = str(home)
    if os.name == "nt":
        git_environment["USERPROFILE"] = str(home)

    subprocess.run(
        ["git", "init", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=git_environment,
    )
    _run_git(repository, "config", "--local", "user.useConfigOnly", "true", env=git_environment)
    (repository / "file.txt").write_text("tracked\n", encoding="utf-8")
    _run_git(repository, "add", "file.txt", env=git_environment)

    if config_variable == "GIT_CONFIG_SYSTEM":
        empty_global = tmp_path / "empty.gitconfig"
        empty_global.write_text("", encoding="utf-8")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    monkeypatch.setenv(config_variable, str(global_config))
    if config_variable == "GIT_CONFIG_GLOBAL":
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    else:
        monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Injected Name")

    commit_environment = git_commit_environment(repository)

    assert commit_environment["GIT_AUTHOR_NAME"] == "Configured Export User"
    assert commit_environment["GIT_AUTHOR_EMAIL"] == "export@example.invalid"
    assert commit_environment["GIT_COMMITTER_NAME"] == "Configured Export User"
    assert commit_environment["GIT_COMMITTER_EMAIL"] == "export@example.invalid"
    assert not any(name.upper().startswith("GIT_CONFIG") for name in commit_environment)

    _git(repository, "commit", "-m", "test export identity", env=commit_environment)
    identity = _run_git(
        repository,
        "show",
        "-s",
        "--format=%an <%ae>|%cn <%ce>",
        env=git_environment,
    ).stdout.strip()
    assert identity == (
        "Configured Export User <export@example.invalid>|"
        "Configured Export User <export@example.invalid>"
    )
