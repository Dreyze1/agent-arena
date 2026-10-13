# Agent Arena

**One task. Multiple coding agents. Side-by-side results.**

Agent Arena is an open-source harness for running coding agents against the same task in separate Git worktrees, then reviewing their patches and checks in one place.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-early%20development-orange.svg)](#project-status)

## Project status

Early development. The local runner can launch configured commands in parallel Git worktrees and produce an offline HTML comparison report. Container isolation and benchmark suites are still planned.

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

Requires Python 3.11+, Git, and the command-line tools for the agents you want to run.

```bash
git clone https://github.com/Dreyze1/agent-arena.git
cd agent-arena
python -m pip install -e .
arena init --path /path/to/your/repository
```

Edit .agent-arena.toml in the target repository to choose the agent commands. Then run:

```bash
arena run \
  --repo /path/to/your/repository \
  --task "Fix the bug described in issue #42" \
  --agent codex \
  --agent claude

arena runs --repo /path/to/your/repository
arena report RUN_ID --repo /path/to/your/repository --open
```

The target repository must be clean, except for an untracked config file. Runs start from its current HEAD commit. Each agent gets a detached worktree, and all agents run concurrently. Patches and logs are saved under the operating system's temporary directory in an agent-arena folder.

A run can include up to 16 agents. Use --timeout to set the per-command limit; the maximum is 24 hours. Agent commands receive a minimal environment by default, with opt-in environment variable names available per profile.

Optional checks can be added to the top level of .agent-arena.toml:

```toml
checks = [["python", "-m", "pytest", "-q"], ["ruff", "check", "."]]
```

Check commands run separately in each agent's worktree after the agent command finishes.

## Roadmap

- [x] Publish the project scaffold and contribution guidelines.
- [x] Add CLI setup and local parallel runs in Git worktrees.
- [x] Capture patches, logs, exit codes, elapsed time, and optional check results.
- [ ] Define a provider adapter contract.
- [x] Add configurable checks.
- [x] Add an offline side-by-side HTML report.
- [ ] Add repeatable benchmark suites.
- [ ] Add an optional container runner for stronger isolation.

See [the roadmap](docs/roadmap.md) and [the architecture notes](docs/architecture.md).

## Security model

Git worktrees separate file changes; they do **not** sandbox a process. Until the container runner is implemented, a configured agent command has the permissions of the user who starts it. Use trusted tools and repositories, and review commands before running them.

Agent Arena will not ask users to paste model API keys into its configuration. Provider authentication should stay with the provider's own CLI or credential manager.

Run artifacts include the task prompt, agent output, and generated patch. They are stored locally under the system temporary directory; review them before sharing.

Child processes receive a small set of operating-system environment variables by default. To pass a specific credential variable to an agent, add its name to that profile's inherit_env list; the value is read from your current environment and is never copied into the run manifest. Agent Arena redacts exact allowlisted values from saved stdout and stderr logs, but tools can transform or encode secrets. Check commands do not receive profile-specific credentials.

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and use the issue templates for bugs and feature proposals.

Pull requests run Ruff lint and formatting checks on Python 3.11, 3.12, and 3.13. Dependabot checks Python and GitHub Actions dependencies weekly.

## License

Agent Arena is released under the [MIT License](LICENSE).
