# Architecture notes

## Goal

Run several coding agents against one task from the same starting commit and make their outcomes straightforward to inspect.

## Planned components

1. **Task and run definition** — task text, repository path, base revision, agent profiles, timeout, and checks.
2. **Workspace manager** — one detached Git worktree per agent, all created from the same base revision.
3. **Provider adapters** — invoke installed agent CLIs with a consistent task contract and capture exit status and output.
4. **Evaluator** — run configured checks against each result and record command, duration, and outcome.
5. **Run artifacts** — preserve a manifest and patch per agent in the system temporary directory.
6. **Review surface** — print a terminal summary and generate an offline HTML report with side-by-side patches, checks, and logs.
7. **Benchmark suites** — execute versioned TOML task collections against the same agents and base commit, preserving per-run results plus aggregate JSON and CSV summaries.
8. **Patch export** — validate a selected run artifact, apply its patch in a temporary detached worktree, and create a local branch commit without switching the user's checkout.
9. **Container executor** — run agent and check commands in disposable Docker containers with a writable worktree, isolated per-agent Git metadata, a read-only shallow base snapshot, configurable resource caps, and an explicit network policy.
10. **CI summaries** — support manual GitHub Actions benchmark runs and upload aggregate CSV metrics without prompts, patches, logs, or run manifests.

## Run artifact shape

The first format should be simple and inspectable:

```text
<system-temp>/agent-arena/<repo-key>/runs/<run-id>/
  run.json
  agents/
    <agent-name>/
      patch.diff
      stdout.log
      stderr.log
      checks.json
```

The manifest should include the base commit, task, provider and model identifiers when available, timestamps, command exit codes, and check results. It must not include API keys or inherited secrets.

The current run manifest and logs can contain task text and repository data. Review artifacts before sharing them; the CLI does not upload them.

Benchmark manifests include a snapshot of suite prompts and SHA-256 digests for the suite and agent configuration so a result can be traced to the exact inputs used.

## Isolation boundary

Git worktrees prevent agents from editing each other's files. Local execution has no OS-level isolation. Docker execution adds explicit mounts, resource caps, and network policy but shares the host kernel and daemon, so it is not a complete security boundary.

## Design constraints

- Keep provider integrations optional; users should not need one vendor's SDK.
- Store configuration in readable TOML.
- Launch commands without a shell and forward only a small default environment allowlist.
- Require agent profiles to opt in to any additional environment variables by name.
- Make runs reproducible from a recorded base commit and command configuration.
- Keep artifacts portable and avoid storing credentials.
