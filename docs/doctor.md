# Environment doctor

Use `arena doctor` to check whether the current repository and selected agent profiles meet the
local prerequisites for a run:

```console
arena doctor --repo /path/to/repository --agent codex --agent claude
```

`--repo` defaults to the current directory. `--config` defaults to
`.agent-arena.toml` inside the resolved Git repository. Repeat `--agent` to inspect only the
profiles you plan to run; without it, every configured profile is checked.
Agent Arena runs are limited to 16 profiles, and doctor blocks a larger selection.

The command checks that Git can inspect the repository and list its worktrees, that the repository
has a base commit and a clean working tree, that the selected TOML config is valid, and that the
selected agent executables, installed adapters, requested environment-variable names, and configured
verification executables are available. Executables with `{task}`, `{worktree}`, or `{base_sha}`
placeholders cannot be resolved before a run, so they produce a warning. Relative executable paths
must refer to files tracked in the current commit, matching the worktree used for a run. An untracked
selected config file is allowed, matching the normal `arena run` preflight.

Warnings do not block a run. A warning appears when no project checks are configured because such
results are unverified, when a command uses a run-time placeholder or relative symlink, or when
Docker is available but the daemon and configured image have not been checked. Confirm that image is
available locally before running a container profile. Repositories whose tracked files use
configured Git clean/process filters are blocked from cleanliness checks because doctor does not run
filters. Repositories with submodules are blocked because doctor does not inspect their worktrees;
check those repositories manually before running Agent Arena.

The doctor does not launch provider commands, plugin code, Git clean/process filters, or Docker; it
does not test provider authentication or contact a remote service. Before repository probes, it
checks `.git` pointers, Git config files, and object alternates for network paths. Git config includes
are blocked because they can load additional files, and symlinks or junctions anywhere in Git
metadata block the check. It disables Git's lazy fetch, skips UNC paths and paths that resolve through
symlinks/junctions to remote shares or unknown targets, and removes Git tracing variables that can
write trace files. Relative executable symlinks are reported as unverified because a temporary run
worktree may resolve them differently. It checks whether
explicitly configured environment-variable names are present, but never reads or displays their
values. Git status runs
with optional index writes and fsmonitor disabled. Repositories using clean/process filters or
submodules are reported as blocked because their cleanliness cannot be verified safely. The
selected config and repository files are not changed.

Exit status is `0` when there are no blocking failures, including when warnings are present; it is
`1` when a prerequisite blocks the selected profiles. Use `arena doctor --help` for the full option
list.
