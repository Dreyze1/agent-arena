"""Validate that a release tag matches the package version."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def validate_release_tag(tag: str, project_version: str) -> None:
    """Raise ValueError unless *tag* is exactly the package's v-prefixed version."""
    expected_tag = f"v{project_version}"
    if tag != expected_tag:
        raise ValueError(
            f"Release tag {tag!r} must match project version exactly ({expected_tag!r})."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Git tag that triggered the release workflow")
    parser.add_argument(
        "--project-file",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to the package's pyproject.toml",
    )
    args = parser.parse_args()

    project = tomllib.loads(args.project_file.read_text(encoding="utf-8"))["project"]
    try:
        validate_release_tag(args.tag, project["version"])
    except ValueError as error:
        parser.error(str(error))
    print(f"Release tag {args.tag} matches project version {project['version']}.")


if __name__ == "__main__":
    main()
