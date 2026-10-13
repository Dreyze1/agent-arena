# Roadmap

This roadmap describes the first useful release. Priorities may change as people try the project.

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

## Milestone 3 — Safer and shareable runs

- [x] Add an optional Docker-backed runner.
- [x] Add explicit CPU, memory, process, capability, and network controls.
- [x] Add CI integration that uploads a minimal aggregate summary.

## Out of scope for the first release

- Automatically merging an agent's changes.
- Sending repository contents to a hosted service.
- Claiming that a Git worktree is a sandbox.
