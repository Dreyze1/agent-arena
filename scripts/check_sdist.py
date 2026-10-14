"""Reject source archives that include local data or credentials."""

from __future__ import annotations

import argparse
import tarfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

_LOCAL_DIRS = {"work", ".venv", "venv", ".pytest_cache", ".ruff_cache"}
_SENSITIVE_NAMES = {".env", "credentials.json", "id_rsa", "id_ed25519"}


def find_forbidden_members(member_names: Sequence[str]) -> list[str]:
    """Return archive member names that look like local artifacts or secrets."""
    forbidden: list[str] = []
    for name in member_names:
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(normalized)
        if (
            path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or windows_path.root
            or ".." in path.parts
        ):
            forbidden.append(name)
            continue
        parts = tuple(part.casefold() for part in path.parts)
        if any(
            part in _LOCAL_DIRS
            or part in _SENSITIVE_NAMES
            or (part.startswith(".env.") and part != ".env.example")
            or part.endswith((".pem", ".key"))
            for part in parts
        ):
            forbidden.append(name)
    return forbidden


def check_sdist(path: Path) -> None:
    """Raise ValueError if a source archive contains local or sensitive paths."""
    with tarfile.open(path, "r:gz") as archive:
        forbidden = find_forbidden_members([member.name for member in archive.getmembers()])
    if forbidden:
        raise ValueError(f"Source archive contains {len(forbidden)} local or sensitive path(s).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "distribution_dir",
        nargs="?",
        type=Path,
        default=Path("dist"),
        help="directory containing the built source archive (default: dist)",
    )
    args = parser.parse_args()
    archives = sorted(args.distribution_dir.glob("*.tar.gz"))
    if len(archives) != 1:
        parser.error("expected exactly one .tar.gz source archive")
    try:
        check_sdist(archives[0])
    except (OSError, tarfile.TarError, ValueError) as error:
        parser.exit(1, f"sdist check failed: {error}\n")
    print("sdist contains no local or sensitive paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
