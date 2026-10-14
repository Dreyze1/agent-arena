# Roadmap

This roadmap covers the first useful release and the evidence needed to grow it. Priorities should
change when developers try the project and tell us what blocks them.

## Milestone 0 — Project foundation

- [x] Public repository, license, contribution guide, and security policy.
- [x] Python package and starter CLI.
- [x] Document the first architecture and security boundaries.

## Milestone 1 — Local comparison

- [x] Load agent profiles and checks from TOML.
- [x] Create one Git worktree per selected agent from the same commit.
- [x] Run provider commands with a consistent task placeholder.
- [x] Capture logs, exit codes, elapsed time, and diffs.
- [x] Print a useful comparison summary.

## Milestone 2 — Better review

- [x] Add a static HTML report for side-by-side patches and check results.
- [x] Allow selecting a winning patch and exporting it as a local branch.
- [x] Add versioned TOML benchmark suites, repetitions, fixed-base runs, and aggregate results.
- [x] Generate an offline benchmark scorecard with per-task/per-agent outcomes and incomplete-attempt reporting.
- [x] Add a local demo benchmark with deterministic mock agents and offline reports.

## Milestone 3 — Safer and shareable runs

- [x] Add an optional Docker-backed runner.
- [x] Add explicit CPU, memory, process, capability, and network controls.
- [x] Add CI integration that uploads a minimal aggregate summary.
- [x] Fix container Git metadata setup so host-side preparation can read the base objects before
  switching alternates to the container mount path; cleanup tracks the created directory identity
  through the runner and removes it only while that identity still matches. Regression coverage
  preserves pre-existing and replaced folders and checks successful setup and partial-failure
  cleanup on Windows and Linux.

## Milestone 4 — Extensible provider integrations

- [x] Define a public adapter API with Python entry-point discovery.
- [x] Add adapter metadata to run and benchmark manifests.
- [x] Document adapter packaging and include a minimal example plugin.

## Milestone 5 — Package distribution

- [x] Add a CI job to build and validate wheel and source distributions.
- [x] Exclude local `work/` data from source archives and check built sdists for absolute,
  traversal, local-environment, and common credential paths (case-insensitive, including nested
  paths) in CI and the release guide.
- [x] Keep PyPI classifiers aligned with verified Python versions; tests assert 3.11–3.14 and both
  built distributions contain the corresponding metadata.
- [x] Add tag-triggered PyPI publishing with Trusted Publishing and OIDC.
- [x] Build current 0.1.0 wheel and sdist from a clean source snapshot on Windows Python 3.11; both
  pass `twine check` and install in clean virtual environments. The wheel passes `arena --version`,
  `arena adapters`, `arena doctor --help`, and `arena demo`; the sdist passes `arena --version` and
  `arena doctor --help`.
- [x] Rebuild the 0.1.0 wheel and sdist from the latest working tree on Windows Python 3.11
  (2026-10-14). Both pass `twine check`; the sdist contains no local or sensitive paths; both
  install in fresh environments and pass `arena --version`, `arena adapters`, `arena doctor --help`,
  and `arena demo`.
- [x] Rebuild the current working tree on Windows Python 3.14 after the runner cleanup fix. Both
  distributions pass `twine check`; the sdist passes the local-path checker; the wheel and sdist
  install in separate fresh environments. The wheel passes `arena --version`, `arena adapters`,
  `arena doctor --help`, and `arena demo`; the sdist passes `arena --version` and `arena doctor
  --help`. This remains local validation until the changes reach GitHub.
- [x] Diagnose the current hosted CI failure: GitHub's run annotation reports an account billing
  lock, and no runner was assigned to any job.
- [x] Retry the failed CI run through a separate repository collaborator. GitHub returned the same
  billing-lock annotation before any steps ran, so changing the triggering account does not bypass
  the restriction.
- [x] Re-run the current `main` CI from the collaborator account on 2026-10-14 (attempt 3). GitHub
  again failed all seven jobs before steps were assigned, with the same account billing-lock
  annotation: [run 38464071916](https://github.com/DreyzeDev/agent-arena/actions/runs/38464071916).
- [x] After transferring the repository to `DreyzeDev`, rerun `main` CI (attempt 4). All seven jobs
  received runners; lint and formatting passed, then tests exposed scorecard input validation and
  Windows test-fixture failures. The package job stopped at tests before building distributions:
  [run 38464071916](https://github.com/DreyzeDev/agent-arena/actions/runs/38464071916).
- [x] Fix the attempt-4 failures in the working tree: reject symlinked manifests before checking
  their names, pin the Windows report test to its own temporary root, and reject manifests with
  more than 1,500,000 JSON tokens before parsing. Full tests and lint passed locally; hosted CI had
  not run against these changes at that checkpoint.
- [x] Retry open Dependabot checks on 2026-10-14 after GitHub assigned runners. PR #1 passed all
  four jobs against base `ea82a4c` with 28 tests per test job; the Python 3.13 current-tree suite
  also passes locally with Rich 15.0.0 (174 passed, 6 skipped). PR #2 and #3 reached pytest but
  failed on the pre-fix benchmark manifest symlink check; PR #3 also hit the old Windows artifact
  path fixture. Their diffs only change artifact action pins in `publish.yml`, which the tag-only
  publish workflow does not run for pull requests. Those checks predate the fixes; recheck the PRs
  before merging them.
- [x] Confirm the hosted GitHub CI build and package checks complete successfully. Run
  [38550194844](https://github.com/DreyzeDev/agent-arena/actions/runs/38550194844) on commit
  `3dc9d5c` passed all nine jobs: wheel/sdist build and clean wheel install, plus Ubuntu and Windows
  quality jobs on Python 3.11–3.14. Windows jobs also ran `arena demo`.
- [x] Create the GitHub `pypi` environment referenced by the release workflow (2026-10-14). It
  currently has no reviewers or deployment restrictions.
- [ ] Configure the PyPI Trusted Publisher and publish the first version.

## Milestone 6 — Prove the first-run experience

- [x] Add a read-only local readiness check for Git, config, agent commands, and verification tools.
- [x] Verify source installation, demo, initialization, readiness check, and a verified two-agent
  benchmark in clean Windows virtual environments on Python 3.11 and 3.14 using local mock commands.
- [x] Add and verify version-independent benchmark manifest limits of 256 nested objects/arrays and
  1,500,000 JSON tokens. Tests cover the exact depth limit, excessive depth and width, a large
  generated manifest, and brackets and escapes inside strings.
- [x] At the manifest and source-archive validation checkpoint, the full Windows suite passed on
  Python 3.11, 3.12, 3.13, and 3.14 with 118 passed and 6 skipped. That count predates later test
  additions; see the latest cross-platform verification below.
- [x] Run the exact Windows CI quality and demo steps on Python 3.12 and 3.13; Ruff checks pass and
  the demo produces its expected verified pass and check failure.
- [x] Latest cross-platform verification after the container metadata fix (2026-10-14): Windows
  Python 3.11–3.14 each pass with 174 passed and 6 skipped; Ubuntu Python 3.14 passes with 180
  passed. Ruff and formatting pass; `arena demo` passes on Windows Python 3.12 and 3.13.
  `git diff --check` passes. Bandit reports no High findings; its one Medium
  finding is the intentional container `/tmp` tmpfs mount, and the remaining Low findings are
  subprocess and color-string heuristics reviewed against the current argv-based calls.
- [x] Re-ran the full Windows suite after adding the PR artifact round-trip check and aligning CI
  with supported Python classifiers (2026-10-14): Python 3.11, 3.12, 3.13, and 3.14 each pass
  with 176 passed and 6 skipped. This was local verification at that checkpoint; the later hosted
  matrix passed as recorded under Milestone 5.
- [x] Re-ran the Ubuntu Python 3.14 quality checks in WSL after the same changes: Ruff lint and
  formatting pass, and the full test suite passes with 182 tests. Hosted verification was still
  pending at that checkpoint; see the later run recorded under Milestone 5.
- [x] Latest local verification after CLI coverage tests and the runner-level container cleanup fix
  (2026-10-14): this host passes 185 tests with 6 skipped; Ruff lint, formatting, and
  `git diff --check` pass. Hosted Ubuntu and Windows verification across Python 3.11–3.14, including
  the package checks and Windows demo, passed in run [38550194844](https://github.com/DreyzeDev/agent-arena/actions/runs/38550194844).
- [ ] Make a real-provider first install and two-agent benchmark work from a clean environment
  without help.
- [ ] Ask five independent developers to run a benchmark using the
  [first-run validation protocol](user-validation.md).
- [ ] Record time-to-first-scorecard and the setup steps that confused them; do not add CLI telemetry.
- [ ] Fix the repeated onboarding problems before adding hosted services.

## Milestone 7 — Grow the adapter ecosystem

- [ ] Turn the adapter example into a starter repository that plugin authors can copy.
- [ ] Maintain a compatibility table for adapters that pass the same example benchmark.
- [ ] Prioritize new integrations from real user requests and keep provider-specific code outside the core.

## Milestone 8 — Expand from repeat use

- [ ] Validate that developers return to compare agents across multiple tasks, not only the first demo.
- [ ] Add shared benchmark suites only when users can contribute tasks without exposing private code.
- [ ] Revisit hosted reports or a public leaderboard only after repeat use and explicit user demand.

## Out of scope for the first release

- Automatically merging an agent's changes.
- Sending repository contents to a hosted service.
- Claiming that a Git worktree is a sandbox.
