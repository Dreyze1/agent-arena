# Roadmap

This roadmap describes the first useful release. Priorities may change as people try the project.

## Milestone 0 — Project foundation

- [x] Public repository, license, contribution guide, and security policy.
- [x] Python package and starter CLI.
- [x] Document the first architecture and security boundaries.

## Milestone 1 — Local comparison

- [ ] Load agent profiles and checks from TOML.
- [ ] Create one Git worktree per selected agent from the same commit.
- [ ] Run provider commands with a consistent task placeholder.
- [ ] Capture logs, exit codes, elapsed time, and diffs.
- [ ] Print a useful comparison summary.

## Milestone 2 — Better review

- [ ] Add a static HTML report for side-by-side patches and check results.
- [ ] Allow selecting a winning patch and exporting it as a branch.
- [ ] Add repeatable benchmark suites with task fixtures.

## Milestone 3 — Safer and shareable runs

- [ ] Add an optional container-backed runner.
- [ ] Add explicit resource limits and network controls.
- [ ] Add CI integration that uploads sanitized run artifacts.

## Out of scope for the first release

- Automatically merging an agent's changes.
- Sending repository contents to a hosted service.
- Claiming that a Git worktree is a sandbox.
