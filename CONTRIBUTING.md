# Contributing

Thanks for your interest in Agent Arena. The project is in early development, so focused issues and small pull requests are especially useful.

## Before you start

- Search existing issues before opening a new one.
- For a larger change, open an issue first so we can agree on the shape of the work.
- Keep pull requests focused and explain the user problem they solve.

## Development setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
# Activate the environment, then:
python -m pip install -e ".[dev]"
arena --help
ruff check .
```

## Pull requests

Include a short summary, the reason for the change, and any relevant command output or screenshots. Update the README or docs when user-facing behavior changes.

By submitting a contribution, you agree that it may be distributed under the repository's MIT license.
