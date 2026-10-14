from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_pyproject_classifiers_include_verified_python_versions() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    classifiers = set(project["classifiers"])

    assert project["requires-python"] == ">=3.11"
    assert {
        f"Programming Language :: Python :: {version}"
        for version in ("3.11", "3.12", "3.13", "3.14")
    } <= classifiers


def test_ci_matrix_covers_all_classified_python_versions() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    classifiers = project["classifiers"]
    supported_versions = {
        classifier.removeprefix("Programming Language :: Python :: ")
        for classifier in classifiers
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", classifier)
    }

    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"(?m)^\s+python-version: \[(?P<versions>[^\]]+)\]$", workflow)
    assert matrix is not None, "CI must declare its Python version matrix"
    ci_versions = set(re.findall(r'"(3\.\d+)"', matrix.group("versions")))

    assert supported_versions <= ci_versions
