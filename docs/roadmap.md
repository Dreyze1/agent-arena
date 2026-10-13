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

## Milestone 4 — Extensible provider integrations

- [x] Define a public adapter API with Python entry-point discovery.
- [x] Add adapter metadata to run and benchmark manifests.
- [x] Document adapter packaging and include a minimal example plugin.

## Milestone 5 — Package distribution

- [x] Build and validate wheel and source distributions in CI.
- [x] Add tag-triggered PyPI publishing with Trusted Publishing and OIDC.
- [ ] Configure the PyPI Trusted Publisher and publish the first version.

## Milestone 6 — Prove the first-run experience

- [ ] Make the first install and two-agent benchmark work from a clean environment without help.
- [ ] Ask five independent developers to run a benchmark on repositories they choose.
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
