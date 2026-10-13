# Security policy

Agent Arena is in early development. Please do not include secrets, access tokens, or private repository contents in a public issue.

To report a security vulnerability, use GitHub's **Report a vulnerability** option in the repository's Security tab. Please include the affected version or commit, impact, and safe reproduction details.

Until container isolation is available, Agent Arena's worktrees do not restrict an agent process. Treat configured commands as code running with your user permissions.

Agent and check commands are launched without a shell. Only a small set of operating-system environment variables is forwarded by default. Agent profiles may explicitly allowlist additional variable names with inherit_env; their values are not written to the run manifest, and exact matches are redacted from saved stdout and stderr logs. This cannot catch transformed, encoded, or otherwise modified secret values. Check commands do not inherit those additional variables.

Run artifacts contain task prompts, process output, and patches. They are stored in the system temporary directory and are not uploaded by Agent Arena. Review them before sharing.
