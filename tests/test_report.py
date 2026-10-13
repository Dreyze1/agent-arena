from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import agent_arena.report as report_module
from agent_arena.errors import ArenaError
from agent_arena.report import create_report


def test_report_replacement_does_not_overwrite_an_existing_hard_link(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"agents": []}), encoding="utf-8")

    report_path = run_dir / "report.html"
    external_file = tmp_path / "important.txt"
    external_file.write_text("preserve this file\n", encoding="utf-8")
    try:
        os.link(external_file, report_path)
    except OSError as error:
        pytest.skip(f"This platform cannot create a test hard link: {error}")

    created_report = create_report(run_dir)

    assert created_report == report_path
    assert external_file.read_text(encoding="utf-8") == "preserve this file\n"
    assert "Local run report" in report_path.read_text(encoding="utf-8")
    assert not os.path.samefile(external_file, report_path)


def test_report_write_failure_preserves_previous_report_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
    report_path = run_dir / "report.html"
    report_path.write_text("previous report\n", encoding="utf-8")

    def fail_replace(*args: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(report_module.os, "replace", fail_replace)

    with pytest.raises(ArenaError, match="simulated replace failure"):
        create_report(run_dir)

    assert report_path.read_text(encoding="utf-8") == "previous report\n"
    assert list(run_dir.glob(".report.html.*.tmp")) == []
