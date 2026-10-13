# Docker execution

Agent Arena uses local execution by default. To run an agent and its checks in disposable Docker
containers, add a `[container]` table to that agent profile:

```toml
[agents.codex]
command = ["codex", "exec", "--full-auto", "{task}"]
inherit_env = ["OPENAI_API_KEY"]

[agents.codex.container]
image = "ghcr.io/example/agent-arena-codex:2026-10"
network = "bridge"
memory = "8g"
cpus = 4.0
pids_limit = 512
user = "1000:1000"
```

The image must already be available locally and contain the agent CLI, language runtimes, and
dependencies needed by its checks. Agent Arena does not pull images automatically. It resolves the
image to an immutable local image ID before a run and records that ID in the run manifest. A
benchmark pins each agent image ID for the whole suite.

## Container boundaries

Each agent command and each check runs in its own `docker run --rm` container. The agent's Git
worktree is mounted read-write at `/workspace`. Git metadata is mounted separately: each agent gets
its own writable index and refs plus read-only access to a shallow snapshot of the base commit. The
original checkout, its `.git` directory and history, the user's home directory, and Docker socket
are not mounted. `{repo}` and `{worktree}` expand to `/workspace` inside the container;
`ARENA_REPOSITORY` and `ARENA_WORKTREE` use that path. The container's other files are discarded, so
install persistent dependencies into the image or the mounted worktree.

Containers drop Linux capabilities, set `no-new-privileges`, and use Docker's default seccomp
profile. Each command receives limits for memory, swap, CPU, and process count. Defaults are `4g`
memory, no swap beyond that limit, `2.0` CPUs, and 256 processes. Configure them per agent with
`memory`, `cpus`, and `pids_limit`. On Linux, the default container UID and GID match the process
that launched Agent Arena; on Windows, the image's default user is used. The optional `user`
setting accepts a numeric UID or UID:GID. Container `HOME` is set to `/tmp`, which is temporary.

Container networking defaults to `none`. Use `network = "bridge"` only when the agent needs to
reach an API or another network service. Bridge mode grants normal Docker network access; Agent
Arena does not filter destination hosts. Credentials are passed only when their environment
variable names appear in `inherit_env`; check containers do not receive those credentials. Exact
allowlisted values are redacted from logs, with the limits described in the
[security policy](../SECURITY.md).

The host user's CLI credential files are not mounted into containers. Configure provider access
through environment variables that you explicitly allow with `inherit_env`.

The Docker image and daemon are trusted computing inputs. Containers share the host kernel, and a
Docker daemon with elevated host access can still expose the host if the daemon or container runtime
is compromised. Docker mode improves isolation over local execution but is not a complete sandbox.
Use trusted images, keep Docker patched, and review the target repository before running agents.

## Requirements and troubleshooting

- Install Docker Engine or Docker Desktop and make sure the `docker` CLI can reach its daemon.
- Use Linux containers. On Windows, Docker Desktop must be set to Linux container mode.
- Build or pull the configured image before starting a run. Agent Arena intentionally refuses to
  pull on your behalf.
- The worktree must be bind-mountable by the local Docker daemon. Remote Docker contexts may not
  have access to paths on your machine.
- If the container cannot reach a provider API, verify that the image has the provider CLI and set
  `network = "bridge"` deliberately.
- If a check cannot find a tool installed by the agent command, bake it into the image or install it
  in the shared worktree. Each command gets a fresh container filesystem.

Agent profiles without a `[container]` table continue to run directly on the host.
