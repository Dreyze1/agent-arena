from __future__ import annotations

import re
from pathlib import Path

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"


def _job_body(workflow: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert match is not None, f"Missing workflow job: {name}"
    return match.group("body")


def test_publish_workflow_roundtrips_pr_artifacts_without_publishing() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert re.search(r"(?ms)^on:\n  pull_request:\n  push:\n    tags:\n      - \"v\*\"", workflow)
    assert re.search(r"(?ms)^permissions:\n  contents: read\n\njobs:", workflow)
    assert re.search(
        r"(?m)^      - name: Check release tag\n        if: github.event_name == 'push'$",
        workflow,
    )

    build_job = _job_body(workflow, "build")
    assert "uses: actions/upload-artifact@" in build_job
    assert "name: python-package-distributions" in build_job

    verify_job = _job_body(workflow, "verify-artifact-roundtrip")
    assert "if: github.event_name == 'pull_request'" in verify_job
    assert "uses: actions/download-artifact@" in verify_job
    assert "name: python-package-distributions" in verify_job
    assert "python-package-distributions" in verify_job
    assert "*.whl" in verify_job
    assert "*.tar.gz" in verify_job
    assert "id-token: write" not in verify_job

    publish_job = _job_body(workflow, "publish")
    assert "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in publish_job
    assert "id-token: write" in publish_job
    assert "uses: actions/download-artifact@" in publish_job
