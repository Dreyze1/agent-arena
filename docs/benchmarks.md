# Benchmark suites

Benchmark suites let you compare configured agents across a repeatable set of tasks. Each task
uses the same repository commit for every selected agent. Every task and repetition is also saved
as a normal Agent Arena run, so you can open its patch report with `arena report`.

## Define a suite

Save a TOML file such as `benchmarks/suite.toml` in the target repository. This example is
illustrative; replace its prompts and test paths with fixtures from your project:

```toml
version = 1
name = "Issue fix comparison"
repetitions = 2
checks = [["python", "-m", "pytest", "-q"]]

[[tasks]]
id = "null-input"
prompt = "Fix the null-input behavior described in docs/issue-null-input.md. Add a regression test."

[[tasks]]
id = "empty-result"
prompt = "Fix the empty-result bug described in docs/issue-empty-result.md. Add a regression test."
checks = [["python", "-m", "pytest", "-q", "tests/test_empty_result.py"]]
```

Fields:

- `version` is currently `1`.
- `name` labels the suite in the terminal and result manifest.
- `repetitions` repeats every task, from `1` to `5` (default `1`).
- Suite-level `checks` are used unless a task defines its own `checks` array. A task can set
  `checks = []` to skip inherited checks. If neither level defines checks, Agent Arena uses the
  checks in `.agent-arena.toml`.
- Each `[[tasks]]` entry needs a unique `id` and non-empty `prompt`. Task IDs may contain letters,
  numbers, dots, underscores, and hyphens.

Checks are executed directly without a shell in each agent's worktree. They should verify the
behavior requested by that task. A successful agent command without checks is counted as an
unverified success, never as a verified pass.

## Run a suite

```bash
arena benchmark \
  --repo /path/to/your/repository \
  --suite benchmarks/suite.toml \
  --agent codex \
  --agent claude \
  --timeout 1800
```

Relative suite and config paths are resolved from the target repository. The repository must be
clean, except that an untracked `.agent-arena.toml` and the selected suite file are allowed. The
suite and agent configuration should be committed when you need other people to reproduce the
comparison.

The suite is read once before execution. Agent Arena records its exact snapshot and SHA-256 digest,
records a digest of the agent configuration, then pins every run to the starting commit. If the
target repository's `HEAD` or agent configuration changes during the benchmark, remaining runs
stop and the partial result is saved.

A benchmark is limited to 100 tasks, 5 repetitions, 16 agents, and 128 total agent invocations.
These limits keep accidental suite expansion from launching an unexpectedly large batch. Each
agent command and each check has its own timeout.

## Read results

The terminal table reports:

- **Verified passes:** agent command succeeded and all configured checks passed.
- **Agent success:** command exited successfully, whether or not checks were configured.
- **Check coverage:** share of attempts that ran at least one check.
- **Average time:** mean agent command duration, excluding checks.

The benchmark directory is printed after the run and contains `benchmark.json`, `summary.csv`, and
`report.html`. The JSON stores suite metadata, base commit, per-task run IDs, prompts, repository
path, and aggregate results. The HTML scorecard is generated locally and shows aggregate metrics
plus a task-by-agent matrix. It labels successful attempts without checks as unverified, failed
checks and timeouts separately, and missing attempts as incomplete. Its metrics use only recorded
attempts; incomplete attempts are shown separately instead of being counted as failures or zeros.

The scorecard includes the suite name, task IDs, agent names, attempt counts, outcomes, timings, and
benchmark ID. It omits task prompts, repository paths, check commands, patches, and logs. It is a
single offline HTML file with no external assets or network calls. Review the JSON and per-run
artifacts before sharing them; those files can contain prompts and repository details.

`arena benchmark` creates `report.html` automatically after a completed suite. To regenerate a
scorecard later, including one for a failed, cancelled, or still-running benchmark, use its ID:

```bash
arena benchmark-report 20261013T120000Z-abcdef12 --repo /path/to/your/repository --open
```

The command validates the ID and reads only that benchmark's local manifest. Each per-task run also
appears in `arena runs`; use `arena report RUN_ID` when you want to inspect its patch and logs.

For a manual GitHub Actions workflow that uploads only aggregate metrics, see the
[GitHub Actions guide](github-actions.md).
