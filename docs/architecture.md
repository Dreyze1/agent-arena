# Architecture notes

## Goal

Run several coding agents against one task from the same starting commit and make their outcomes straightforward to inspect.

## Planned components

1. **Task and run definition** — task text, repository path, base revision, agent profiles, timeout, and checks.
2. **Workspace manager** — one detached Git worktree per agent, all created from the same base revision.
3. **Provider adapters** — invoke installed agent CLIs with a consistent task contract and capture exit status and output.
4. **Evaluator** — run configured checks against each result and record command, duration, and outcome.
5. **Run artifacts** — preserve a manifest and patch per agent in the system temporary directory.
6. **Review surface** — the first version prints a terminal summary; a static HTML report can follow.

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

## Isolation boundary

Git worktrees prevent agents from editing each other's files. They are not a security boundary: child processes still run as the current user. The initial local runner should say this clearly. A later container runner can offer stronger isolation with an explicit, reviewable mount and network policy.

## Design constraints

- Keep provider integrations optional; users should not need one vendor's SDK.
- Store configuration in readable TOML.
- Make runs reproducible from a recorded base commit and command configuration.
- Keep artifacts portable and avoid storing credentials.
