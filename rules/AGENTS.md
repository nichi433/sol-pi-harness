# SoL-Pi: keep large tool output out of the context

- Run builds/tests/linters (pytest, cargo, npm/pnpm test, tsc, make, go test...) as `solpi_run`, or plainly via Bash when a hook rewrites them. Output over 4 KB comes back as a receipt: verbatim error lines + head/tail + an `obs_<id>` handle.
- The receipt is not the whole log. If the line you need is not in it, call `solpi_read_obs(obs_id, grep_pattern=...)` instead of re-running the command.
- After editing a file whose check you will run immediately, `solpi_fuse` does edit + verify in one call (skip it if you must inspect the edit first).
- At the end of a plan step in a long session, `solpi_milestone` says whether `/compact` pays off now; only then compact.
