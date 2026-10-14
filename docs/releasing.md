# Package releases

Agent Arena builds a wheel and source distribution in CI. A version tag starting with `v` runs the
same checks and publishes the verified distributions to PyPI with Trusted Publishing. The workflow
uses GitHub's short-lived OIDC identity; it does not need a PyPI API token.

## One-time PyPI setup

Before the first release, configure a Trusted Publisher for the `agent-arena` project on PyPI. If
the project has not been published yet, create a pending publisher from the PyPI account's
publishing settings. Use these exact values:

| Setting | Value |
| --- | --- |
| Owner | `DreyzeDev` |
| Repository | `agent-arena` |
| Workflow filename | `publish.yml` |
| GitHub environment | `pypi` |

In the GitHub repository, create the `pypi` environment under **Settings → Environments**. Add
required reviewers if releases should wait for a human approval. The environment name must match the
Trusted Publisher setting exactly.

## Publish a release

1. Update `version` in `pyproject.toml` and commit the release changes to `main`.
2. Confirm CI is green for that commit.
3. Create and push a tag whose name is exactly `v` followed by the project version. For version
   `0.1.0`, the tag is `v0.1.0`:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. The `Publish to PyPI` workflow checks that the tag matches `pyproject.toml`, runs tests and code
   quality checks, builds both distributions, checks their metadata, and smoke-tests the installed
   wheel. The publish job starts only after those checks pass and uses the `pypi` environment.

The first successful publication creates the PyPI project. After it is available, the README can
also document `pipx install agent-arena` as an installation option. Until then, use the source
checkout instructions in the README.

## Local package check

From a clean checkout with the development dependencies installed:

```bash
python -m pip install build twine
python -m build --sdist --wheel
python -m twine check dist/*
python scripts/check_sdist.py dist
```

The source archive check rejects local work directories, virtual environments, and common credential
files before a distribution is uploaded.
