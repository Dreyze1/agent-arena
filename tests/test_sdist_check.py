from __future__ import annotations

import pytest
from scripts.check_sdist import find_forbidden_members


def test_find_forbidden_members_allows_project_files_and_example_env() -> None:
    assert (
        find_forbidden_members(
            [
                "agent_arena-0.1.0/README.md",
                "agent_arena-0.1.0/.env.example",
                "agent_arena-0.1.0/.ENV.EXAMPLE",
                "agent_arena-0.1.0/src/agent_arena/cli.py",
            ]
        )
        == []
    )


@pytest.mark.parametrize(
    "member",
    [
        "work/report.html",
        "agent_arena-0.1.0/work/report.html",
        r"agent_arena-0.1.0\work\report.html",
        "agent_arena-0.1.0/.venv/pyvenv.cfg",
        "agent_arena-0.1.0/pkg/tests/.venv/pyvenv.cfg",
        "agent_arena-0.1.0/pkg/.pytest_cache/state",
        "../outside/private.txt",
        "/outside/private.txt",
        r"C:\outside\private.txt",
        "C:outside/private.txt",
        r"\outside\private.txt",
        "agent_arena-0.1.0/.env",
        "agent_arena-0.1.0/.env.production",
        "agent_arena-0.1.0/pkg/.ENV.production",
        "agent_arena-0.1.0/credentials.json",
        "agent_arena-0.1.0/pkg/CREDENTIALS.JSON",
        "agent_arena-0.1.0/private/id_ed25519",
        "agent_arena-0.1.0/certs/client.pem",
        "agent_arena-0.1.0/keys/signing.key",
    ],
)
def test_find_forbidden_members_rejects_local_and_sensitive_paths(member: str) -> None:
    assert find_forbidden_members([member]) == [member]
