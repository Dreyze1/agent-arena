# Agent Arena

**One task. Multiple coding agents. Side-by-side results.**

Agent Arena is an open-source harness for running coding agents against the same task in separate Git worktrees, then reviewing their patches and checks in one place.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-early%20development-orange.svg)](#project-status)

## Project status

Early development. The repository is being set up; agent orchestration is the first milestone.

## Why Agent Arena?

Coding agents are easy to compare in a demo and hard to compare on your own codebase. Agent Arena aims to make the comparison repeatable:

- Give each agent the same task and the same starting commit.
- Keep each run in its own Git worktree.
- Collect patches, command output, duration, and check results.
- Review the outcomes together before choosing what to keep.

The first release will focus on local, reproducible runs. GitHub Actions and shared benchmark suites can come later.

## Planned workflow

```text
Task + repository
        │
        ├── Agent A → isolated worktree → patch + checks
        ├── Agent B → isolated worktree → patch + checks
        └── Agent C → isolated worktree → patch + checks
                              │
                    compare and review
```

## Quick start

The CLI currently provides a small setup command. The run engine is not available yet.

```bash
python -m venv .venv
# Activate the environment, then:
python -m pip install -e .
arena --help
arena init --path /path/to/your/repository
```

arena init creates an .agent-arena.toml starter config. It does not run agents or modify Git history.

## Roadmap

- [x] Publish the project scaffold and contribution guidelines.
- [x] Add the initial CLI and configuration starter.
- [ ] Define a provider adapter contract.
- [ ] Run configured agents in separate Git worktrees.
- [ ] Capture patches, logs, exit codes, and elapsed time.
- [ ] Add configurable checks and a side-by-side result view.
- [ ] Add an optional container runner for stronger isolation.

See [the roadmap](docs/roadmap.md) and [the architecture notes](docs/architecture.md).

## Security model

Git worktrees separate file changes; they do **not** sandbox a process. Until the container runner is implemented, a configured agent command has the permissions of the user who starts it. Use trusted tools and repositories, and review commands before running them.

Agent Arena will not ask users to paste model API keys into its configuration. Provider authentication should stay with the provider's own CLI or credential manager.

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and use the issue templates for bugs and feature proposals.

## License

Agent Arena is released under the [MIT License](LICENSE).
