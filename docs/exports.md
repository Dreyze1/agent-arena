# Export an agent result

After reviewing a run report, create a local branch containing one agent's patch:

```bash
arena export RUN_ID \
  --repo /path/to/your/repository \
  --agent codex \
  --branch review/codex-run
```

If `--branch` is omitted, Agent Arena uses `arena/RUN_ID/AGENT`. Branch names are validated, and
an existing local branch is never overwritten.

The exporter verifies that the run belongs to the selected repository and that the saved patch is
inside the run's artifact directory. It applies the patch to a temporary detached worktree at the
run's recorded base commit, commits the result, and creates the branch at that commit. It then
removes the temporary worktree. The current checkout and its branch are left unchanged; the new
branch is local and is not pushed automatically.

The target repository must be clean, except for an untracked `.agent-arena.toml`. Git must have
`user.name` and `user.email` configured so it can create the export commit. Git's normal commit
hooks run; if a hook changes the staged tree, export stops without creating a branch. If an agent
timed out or returned a nonzero exit code, its partial patch can still be exported; the CLI marks
that result before creating the branch and shows the recorded check results.

To review or publish the branch afterwards:

```bash
git -C /path/to/your/repository switch review/codex-run
git -C /path/to/your/repository push -u origin review/codex-run
```

Export does not merge agent work into the current branch. Review the branch and its checks before
opening a pull request.
