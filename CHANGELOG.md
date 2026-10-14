# Changelog

User-visible changes to Agent Arena are recorded here. Dates are added when a version is released.

## 0.1.0 — Unreleased

### Added

- Run configured coding-agent CLIs from the same Git commit in separate worktrees.
- Verify patches with repository checks and review results in offline HTML reports.
- Run repeatable benchmark suites with task-by-agent scorecards and JSON/CSV summaries.
- Export a selected patch as a local branch without switching the user's checkout.
- Add optional Docker execution with explicit network and resource controls.
- Extend provider support through Python adapter plugins.
- Run a deterministic offline demo and check local prerequisites with `arena doctor`.
- Build and validate Python distributions in CI, with a tag-triggered PyPI workflow that uses
  Trusted Publishing. The one-time PyPI publisher setup is still required.

### Security and privacy

- Keep run artifacts local by default; the optional GitHub Actions workflow uploads aggregate
  benchmark metrics only.
- Do not treat local Git worktrees as a security sandbox. See the
  [security model](README.md#security-model).
