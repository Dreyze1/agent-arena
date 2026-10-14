# Product brief

**Status:** Working hypothesis, 2026-10-14

**Recommendation:** Continue, then validate with external users before investing in hosted features.

## Who it is for

Maintainers and developers who already use two or more coding-agent CLIs and want to know which
one produces the most useful change on *their* repository, under *their* tests.

This is a hypothesis. The repository is new and has no external usage signals yet.

## The problem

Public leaderboards can show how an agent performs on a fixed suite, but they do not answer whether
it will handle a team's own code, conventions, and acceptance checks. Running the same issue through
multiple agents by hand makes patches, logs, and test outcomes tedious to compare.

## Product promise

Run the same task from the same commit in separate worktrees, verify each result with the repository's
own checks, then inspect the patches locally. The default local workflow keeps run artifacts on the
user's machine and Agent Arena does not upload them automatically. Configured agent commands may
send prompts or source context to external services; their network behavior and data handling are
controlled by those commands and providers. The optional GitHub Actions workflow can upload an
aggregate summary when the user configures and runs it.

The existing CLI provides local parallel runs, configurable checks, repeatable TOML suites, Docker
execution, per-run patch reports, CSV/JSON aggregates, an offline benchmark scorecard, and the
read-only `arena doctor` setup check. `arena demo` also gives new users a no-credential first result
using local mock agents. The next gap is learning whether developers can install and use real
provider CLIs without help, then improving that workflow from their feedback.

## Why this direction

The space already includes large fixed-suite benchmarks such as
[CCBench](https://github.com/codecrafters-io/ccbench), and multi-agent evaluation harnesses such as
[Google Cloud EvalBench](https://github.com/GoogleCloudPlatform/evalbench). Agent Arena should earn
its place through repository-specific checks, local patch review, provider-neutral CLI execution,
and privacy by default. A generic public leaderboard would not create that distinction.

[PicadoLabs Agent-Bench](https://github.com/PicadoLabs/agent-bench) is a closer product overlap: its
README advertises local execution, a mock provider, an environment doctor, and a web dashboard. The
mock-provider quick start lowers the cost of a first trial. The competitive picture below was
updated on 2026-10-14; it changes how much weight to give that initial demo.

## Competitive update — 2026-10-14

- [Aabbcdl AgentArena](https://github.com/aabbcdl/AgentArena) is a direct overlap: it runs local
  coding agents on the same repository and task, supports evidence-focused reports, and currently
  offers a no-auth demo, a readiness doctor, task packs, and a local workbench. Its README lists a
  public 0.2.1 release. This is the closest match to the onboarding and comparison experience
  proposed here.
- [PatchArena](https://github.com/sahenjp/PatchArena) is another direct overlap: its README describes
  repeatable Git-worktree runs, checks, agent comparisons, task suites, `doctor`, and self-contained
  reports. The repository lists v0.3.0 and currently requires Rust 1.85+ with Linux/WSL2 as its
  primary target. These are maintainer-stated capabilities and requirements, not an independent
  product evaluation.
- [catwithAI/agent-arena](https://github.com/catwithAI/agent-arena) describes same-task, same-prompt
  comparisons across several coding CLIs, repeated trials, and a local interface. [PicadoLabs
  Agent-Bench](https://github.com/PicadoLabs/agent-bench) adds a standardized task catalog, GitHub
  PR ingestion, and dashboard. These are repository claims, not independent quality evaluations.
- The similarly named [agentarena package on PyPI](https://pypi.org/project/agentarena/) was
  published on 2026-02-26 and also compares coding agents on a user's codebase with validation
  commands and timing/cost/token metrics. PyPI's exact `agent-arena` project endpoint returned 404
  on 2026-10-13, so the normalized package name used by this repository was available at that
  check; the existing `agentarena` package can still confuse search and word of mouth.
- [CCBench](https://github.com/codecrafters-io/ccbench) represents the fixed-suite research side:
  its README lists 187 tasks and results last updated on 2026-02-11, using private CodeCrafters
  submissions and official tests. [Google Cloud EvalBench](https://github.com/GoogleCloudPlatform/evalbench/blob/main/docs/agentic-evals.md)
  targets configurable multi-turn evaluation of coding CLIs and other agents, with simulated users,
  trajectory scoring, and cloud reporting. Neither is the same lightweight local workflow, but both
  make a generic leaderboard a weak place to compete.
- GitHub showed this repository at 0 stars and 0 forks on 2026-10-13. Several unrelated or adjacent
  projects also use the `Agent Arena` / `agent-arena` name, including [rbrus/agent-arena](https://github.com/rbrus/agent-arena).

**Inference:** The demo remains a useful installation check, but it is not a durable differentiator:
direct competitors already demonstrate local runs without provider credentials. The current product
hypothesis—run existing CLI agents against a developer's own Git repository and checks, then inspect
offline evidence—has not yet been validated as meaningfully distinct or valuable enough for repeat
use. A possible wedge is a lower-friction Python CLI that works on native Windows as well as Linux;
the project wheel, demo, and `init` command passed a clean Windows install check on 2026-10-13. The
hosted Linux and Windows matrix later passed on Python 3.11–3.14, including `arena demo` on Windows;
this verifies the tested project paths, not provider CLI behavior or user demand.

**Recommendation:** Do not add a hosted leaderboard or build a larger dashboard now. Keep the
five-developer onboarding test as the next product gate. Include native Windows developers and ask
what they currently use, what they would switch for, and whether Python/native-Windows support
changes that decision. Use the hosted matrix as a verified prerequisite, then improve supported-provider
setup only where observed sessions show friction; if developers do not value this workflow, revisit
the product direction before adding features. A distinctive name may help search, but renaming alone
will not resolve the product overlap. The exact PyPI name
was available at the 2026-10-13 check, while a similarly named package and several GitHub projects
can still confuse discovery.

## Adoption and release check — 2026-10-14

The [public repository](https://github.com/DreyzeDev/agent-arena) has 0 stars and 0 forks, and there
are [no published releases](https://github.com/DreyzeDev/agent-arena/releases). The [PyPI project
endpoint still returns 404](https://pypi.org/pypi/agent-arena/json) on 2026-10-14. GitHub Actions
was previously blocked by an account billing lock. After transferring the repository to
`DreyzeDev`, [the `main` CI run](https://github.com/DreyzeDev/agent-arena/actions/runs/38464071916)
(commit `e3535f6`, attempt 4) started all seven jobs. Lint and formatting passed; tests failed on
benchmark scorecard cases, so the package job stopped before building distributions. Local fixes for
those cases later passed locally. This describes the earlier failure; the hosted follow-up is recorded
below.
The three open pull requests are Dependabot updates, not reports from users. There is still no
external usage or demand signal.

At the earlier packaging checkpoint, the full Windows test suite passed on Python 3.11, 3.12, 3.13,
and 3.14 (118 passed, 6 skipped on each). PyPI classifiers now match those verified versions. A
fresh 0.1.0 wheel and sdist built from
the current source snapshot both pass `twine check`
and install in clean virtual environments. The wheel passes `arena --version`, `arena adapters`,
`arena doctor --help`, and `arena demo`; the sdist passes `arena --version` and `arena doctor
--help`. The sdist contains 58 entries (95,072 bytes) and passes the local archive-path checker.
The Ruff checks and `arena demo` also pass in clean Windows environments on Python 3.12 and 3.13;
the demo reports one verified pass and one expected check failure.
An earlier inspection caught the source archive including local `work/` data; that archive was
discarded without being published. The package build now excludes that directory, and the checker
rejects absolute or traversal paths, Windows drive paths, nested local environments, and common
credential filenames without regard to case. This validates local artifacts, not hosted CI or an
installation from PyPI.

The working tree now fixes the attempt-4 CI failures: symlinked input validation order and the
Windows scorecard test's temporary artifact root. The benchmark manifest loader also enforces
version-independent limits of 256 nested JSON objects/arrays and 1,500,000 JSON tokens before
parsing. At that checkpoint, scorecard tests passed on Python 3.11–3.14, covering excessive width
and depth, the exact depth limit, a large generated manifest, and brackets and escapes inside
strings; the full suite had 118 passed and 6 skipped on each Windows version. The later container
metadata fix and later cross-platform results are recorded in the [roadmap](roadmap.md). At the time
of that checkpoint, these changes were still uncommitted.

### Hosted CI follow-up — 2026-10-14

The earlier CI failures are resolved. The `main` run on commit `3dc9d5c` passed all nine jobs:
wheel and source distribution checks, clean wheel installation, and Ubuntu/Windows quality checks
on Python 3.11–3.14. The Windows jobs also completed `arena demo`. See
[run 38550194844](https://github.com/DreyzeDev/agent-arena/actions/runs/38550194844). This closes
the hosted CI blocker; it does not establish PyPI installation or external adoption.

Treat distribution and user validation as the work to unblock next. Do not interpret a successful
local run or this small feature set as proof of adoption.

Use the [first-run validation protocol](user-validation.md) after a package release is available.
Source-checkout sessions can identify usability problems earlier, but do not count as evidence that
the published installation path works.

## Ten-star experience

From a clean install, a developer selects two local agent CLIs, runs a small suite against a private
repository, and gets one self-contained scorecard showing verified outcomes and timing. The scorecard
explains when an attempt was unverified or incomplete instead of presenting missing evidence as a
failure or a zero; detailed patches remain in each run's local report. Nothing is uploaded
automatically.

Longer term, reported token usage and cost can help users compare quality against spend. Those values
must stay explicitly unknown when a CLI does not provide reliable telemetry; Agent Arena should not
invent prices or turn missing data into zero.

## Delivered slice: local benchmark scorecard

`arena benchmark` now writes an offline HTML scorecard beside the JSON and CSV results, and
`arena benchmark-report BENCHMARK_ID --repo PATH --open` regenerates it later. The report includes
aggregate metrics and a task-by-agent matrix with check outcomes, timeouts, durations, and incomplete
attempts. It leaves prompts, repository paths, check commands, patches, and logs out of the HTML.

This closes the gap between benchmark JSON/CSV and the project's visual per-run review. The next
step is to validate the first-run experience with independent developers before adding hosted
features.

## Delivered slice: local readiness doctor

`arena doctor` checks Git worktree support, the selected repository's base commit and cleanliness,
TOML configuration, selected command availability, adapter metadata, explicitly requested
environment-variable names, and configured verification tools. It never launches provider commands
or plugin code, tests authentication, contacts a network service, or displays environment-variable
values. Docker profiles are reported with a warning because checking an image or daemon could reach
a remote Docker context. A warning also explains that results without configured checks are
unverified. This lowers setup friction but does not prove that users can authenticate a provider or
finish a benchmark unaided; external onboarding validation remains the next product gate.

## Local first-run journey audit — 2026-10-14

In fresh Windows virtual environments on Python 3.11 and 3.14, installing the current checkout
produced a working `arena` command. The journey passed `arena --version`, `arena adapters`,
`arena demo`, `arena init`, `arena doctor`, and a two-agent `arena benchmark` against a temporary
Git repository. The benchmark used local Python command adapters and a file-existence check; both
agents received a verified pass and the CLI generated the offline scorecard.

This confirms the packaged CLI and mock-provider workflow on those two interpreter versions. It
does not verify installation from PyPI, real provider CLI setup or authentication, the full hosted
CI matrix, or independent developers completing the journey without help. Keep the external
five-developer validation as an open product gate.

## Delivered slice: offline first-run demo

`arena demo` creates a temporary Git fixture, runs two deterministic mock agents through the normal
benchmark path, and saves an offline scorecard plus a per-run report. One mock passes its checks;
the other returns success but fails verification. The command needs only Python and Git and leaves
the repository where it is launched unchanged. This demonstrates the result format before a new
user installs or authenticates a provider CLI. It does not yet prove that the real-provider setup is
clear; that remains a user-validation task.

## Anti-goals

- Do not build or operate a hosted leaderboard before users ask for one.
- Do not upload private code, prompts, patches, logs, or benchmark manifests automatically.
- Do not claim that results from one private repository rank agents universally.
- Do not report estimated cost unless the user opts into a documented pricing model.

## How to tell whether the direction works

The near-term product metric is five independent developers completing a two-agent benchmark on
their own repository and understanding the result without help. Ask them directly; do not add
telemetry to the CLI to measure this. At the time of this brief, there is no evidence that this has
happened.

Before calling the project adopted, configure PyPI Trusted Publishing and publish the first version;
the hosted CI matrix now passes as recorded above. The repository's [release guide](releasing.md)
documents the PyPI setup; the required GitHub `pypi` environment now exists, but the Trusted
Publisher is not yet configured. Independent first-run sessions remain uncompleted, and the open
pull requests are Dependabot updates rather than user reports. Current blockers and verification
live in the [roadmap](roadmap.md).

## Research notes

- CCBench describes a fixed evaluation over small, real-world codebases with official test runners
  and publishes task data and results: [project README](https://github.com/codecrafters-io/ccbench).
- Current competitive examples and their stated capabilities are linked in the competitive update
  above. They were reviewed on 2026-10-14; project README claims may change and are not independent
  evaluations.
- Google Cloud EvalBench documents support for coding CLIs including Gemini CLI, Claude Code, and
  Codex CLI, with scenario scoring that can include latency and token consumption:
  [agentic evaluations](https://github.com/GoogleCloudPlatform/evalbench/blob/main/docs/agentic-evals.md).
- Gemini CLI keeps vendor-specific behavioral evaluations for validating its own prompts and tools:
  [behavioral evals](https://github.com/google-gemini/gemini-cli/blob/main/evals/README.md).
