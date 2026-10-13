# Offline demo

Run the built-in demo to see an Agent Arena benchmark without setting up a provider CLI:

```bash
arena demo
```

The command creates a disposable Git fixture in the operating system's temporary directory and
runs one benchmark task with two deterministic mock agents. `mock-pass` implements the requested
behavior and passes its check. `mock-check-fail` returns successfully but implements the behavior
incorrectly, so its check fails. This shows why Agent Arena reports successful agent commands and
verified passes separately.

The demo uses the installed Python interpreter and Git. It makes no API calls, needs no API keys,
does not start Docker, and does not make network requests. It does not use the current directory as
the fixture: the repository from which you invoke `arena demo` is left unchanged.

The command prints the temporary workspace, benchmark ID, offline HTML scorecard, and per-run HTML
report paths. Those files remain in the system temporary directory so you can inspect them after
the command exits. Add `--open` to open the scorecard in your default browser:

```bash
arena demo --open
```

The temporary Git fixture is kept with the results. Use the printed repository path with
`arena benchmark-report BENCHMARK_ID --repo PATH` if you want to regenerate the scorecard. The
workspace can be removed after you finish reviewing its reports.
