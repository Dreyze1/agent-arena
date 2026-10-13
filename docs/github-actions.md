# GitHub Actions

Agent Arena includes a manually triggered workflow at `.github/workflows/benchmark.yml`. It runs a
committed benchmark suite on an Ubuntu hosted runner and uploads only `summary.csv`, which contains
agent names and aggregate counts and timings. Formula-like agent names are escaped before writing
the CSV. Prompts, repository paths, patches, per-run manifests, and command logs stay in the runner's
temporary directory and are not included in the uploaded artifact. The workflow uses read-only
repository permissions, disables checkout credential persistence, and keeps the uploaded summary
for 14 days.

## Set up a repository

1. Add `.agent-arena.toml` with the agent commands and any required `inherit_env` names. Do not put
   secret values in this file.
2. Add a benchmark suite, for example `benchmarks/suite.toml`, and commit both files.
3. Install the provider CLIs and any check tools in the workflow runner, or configure a Docker image
   that already contains them. Docker images must be built or pulled explicitly before the benchmark;
   Agent Arena does not pull images automatically.
4. Add provider credentials under **Settings → Secrets and variables → Actions**. Update the
   workflow's `Run benchmark` step to map each secret to the exact environment variable name listed
   in `inherit_env`. For example:

   ```yaml
   env:
     TMPDIR: ${{ runner.temp }}
     SUITE_PATH: ${{ inputs.suite }}
     OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
   ```

5. Open **Actions → Agent Arena benchmark → Run workflow**, enter the suite path, and start the run.

The workflow runs only on manual dispatch, so it does not execute agent commands on pull requests.
The benchmark and its checks still run code from the selected commit with the runner's permissions.
Review the agent configuration, task prompts, and checks before dispatching. Only explicitly
allowlisted environment variables are passed to agent commands; never allowlist `GITHUB_TOKEN`.

The workflow gives each invocation a 30-minute timeout and the job a three-hour cap. Keep suites
small enough to fit that budget. Uploading a summary does not make the benchmark results reproducible
by itself: retain the suite, agent configuration, base commit, and provider/tool versions in the
repository when that matters.

The example uses GitHub-hosted runners. Self-hosted runners need a cleanup policy for the temporary
Agent Arena artifacts after upload.
