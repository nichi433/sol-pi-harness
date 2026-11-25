# Will this help in my Claude Code / Codex / OpenCode sessions?

Short answer: **sometimes, modestly, and not for free** — and in an A/B on short Claude Code tasks it did *nothing* (§7). It is worth installing if your sessions repeatedly run
builds/tests that print more than a few KB. It is a net loss (small, but a loss) if they do not. This page says
how to tell, with the numbers we have and the ones we do not.

## 1. What the paper actually shows (and what it does not)

Re-checked against the paper's tables ([details](./PAPER_2609.20519.md#4-benchmark-results-reported-in-arxiv260920519-re-checked-against-the-papers-tables-1-4)):

| | Result |
| :--- | :--- |
| Full four-mechanism stack vs the paper's Pi baseline | −44.7…−49.0 % tokens, ≈ −⅓ cost, **score 93.7 % (GPT-5.6 Sol) / 94.3 % (Opus 5) of baseline** |
| Same stack on Opus 5 vs **native Claude Code** | −34.6 % tokens, **−54 % cost**, score 42.2 vs 43.7 (**−3.4 %**) |
| Terminal-Bench 4 | 15 tasks solved vs 18 for Codex and Pi; cost per solved task only −7…−12 % |
| Best *scoring* setups | single mechanisms (ObservationPack on GPT, Action Fusion on Opus), saving only ≈ 6 % tokens |

Read honestly: the efficiency is real, **it costs a few points of task success**, the evidence is preliminary
(one frozen configuration, one reported run, developed on one model), and the mechanisms fire less often on a
model they were not developed on.

**This repository is not that system.** The paper implements the mechanisms *inside* the Pi harness. Here they
are an external MCP server + hooks around Claude Code / Codex / OpenCode, which cannot do everything Pi does:

| Paper | In an external port |
| :--- | :--- |
| Rewrites the request history: big results are sent in full for 2 requests, then replaced by a handle | Not possible. Large results are replaced **immediately** (more saving, but the agent never sees the full log, so it may need a recall call) |
| Action Fusion is part of the file-mutation tool schema | An extra tool the agent must *choose*. The paper itself found prompt-only triggering unreliable |
| Compaction runs natively at plan-step completion | Only a recommendation (`/compact`) |
| Reducer applies to a predefined set of build/test commands | Same, via a hook that rewrites only **simple** commands (see below) |

So **none of the paper's percentages should be expected here.** What we *can* claim is what we measure.

## 2. What we measure (and what we do not)

Measured, reproducible (`python benchmarks/run_benchmark.py`):
- On build/test logs ≥ 4 KiB the receipt is **≈ 94–96 % smaller** (estimated tokens) and kept **21/21** planted
  error lines directly visible (a naive `tail -n 40` keeps 7/21), **24/24** recoverable through the handle.
- Real run in Claude Code: the hook rewrote `make test` (38 KB output) into a receipt that kept the `FAILED` line.
- Secrets are redacted in receipts and in the stored log; the hook never rewrites compound commands.

**Not measured:** end-to-end task success, total session cost, or how many extra turns an agent spends recalling
archived output. Those need real agent runs (protocol in §5). Until then treat "−96 %" as *tool-output size*, not
as "−96 % of your bill".

## 3. Where the money actually goes (so you can reason about it)

In the paper ≈ 98 % of recorded tokens are **cache reads**: every request re-reads the whole prompt. A tool result
therefore costs `size × (number of later requests)`, not just `size`. That is why shrinking big outputs matters
at all — and why a 200-byte output is not worth touching.

Cost of having the toolkit installed (every request, cached): MCP tool schemas **≈ 735 tokens** (4 tools; set
`SOLPI_MCP_TOOLS=all` to also list `solpi_tune`/`solpi_telemetry`), plus ≈ 180 tokens of `AGENTS.md` rules on
Codex/OpenCode. Claude Code defers MCP schemas, so its real cost is lower.

Rule of thumb: over a session of *R* requests the fixed cost is ≈ `735 × R` cached-read tokens. One 10 KB output
(≈ 2,500 tokens) produced mid-session is re-read ≈ *R*/2 times and a receipt removes ≈ 90 % of it →
`2,250 × R/2 ≈ 1,100 × R`. **One big log per session roughly pays for the full install; two or more is a gain.**
Sessions with no outputs over ~4 KB only pay the overhead.

**Lean install** (`solpi install --hook-only`): only the hook plus an MCP server that advertises `solpi_read_obs` →
**≈ 143 tokens per request** instead of ≈ 735, no skill/rules, no compaction notice. Break-even drops to any single
result of about 1.3 KB or more (`143·R ≤ 0.9 × tokens × R/2`), i.e. **one ordinary ≥ 4 KiB log pays for it** (the
hook only touches results that large anyway).

## 4. Who should use it

| Your sessions look like… | Verdict |
| :--- | :--- |
| Long agentic runs, repeated `pytest` / `cargo test` / `npm test` / `make` / `tsc`, big failures (C++ templates, many tests) | **Likely worth it.** This is exactly what the reducer targets |
| Context pressure (auto-compaction, 100k+ tokens) | **Likely worth it**; receipts also keep the window clean |
| Multi-agent / subagent swarms (paper: −27 % cost) | Worth trying |
| Short, conversational, or read-and-edit sessions | **Not worth it** — nothing ≥ 4 KB to shrink |
| Mostly `cat`/`grep`/file reads | **No.** Searches and file reads bypass the reducer by design (head/tail only above 10 KiB) |
| You chain commands (`cd app && pytest`, `PYTHONPATH=src pytest`) | Covered by the default `prefixed` hook mode (see §4b). Pipes/redirects/`&` (`pytest \| tail`) are never rewritten |
| Tasks where one missed log line is expensive | Be careful: the receipt is keyword-based; the handle recovers the rest but costs a turn |

Per harness:
- **Claude Code:** hook + `solpi_read_obs` are the tested part. Action Fusion needs Claude to pick `solpi_fuse`
  over its own Edit — expect low use. Verified end-to-end (rewrite + receipt).
- **Codex:** same protocol (`hooks.json`, `config.toml`); *implemented from the docs, not run here* — verify
  with `codex` before relying on it.
- **OpenCode:** MCP + `AGENTS.md` only (no hook API), so output is packed only when the agent calls
  `solpi_run`; benefit is smaller and depends on the model following the rule.
- **AGY / Cursor:** files are generated; AGY's hook contract could not be confirmed.

## 5. Find out for *your* usage (30 minutes, no risk)

1. **Estimate from your own history** (reads Claude Code transcripts locally, prints aggregates only):
   ```bash
   python3 benchmarks/analyze_sessions.py            # ~/.claude/projects
   ```
   It reports, as a share of your cache-read tokens: what the build/test hook covers (`hook_only`,
   `hook_prefixed`, `reducible`), what a generic archive of any big Bash result would cover (`all_big_bash`), and
   the broadest case across all tools incl. `Read`/`Grep` (`all_big_any`, with a per-tool breakdown), each with the
   **net after the tool overhead**. Savings stop at the next detected compaction (a sharp cache-read drop); the
   no-compaction figure is printed next to it. Still an *upper bound*: no recall turns, no quality change, and
   Claude Code also clears old tool results on its own.
2. **A/B on real tasks** (the only way to see quality and extra turns): pick ~10 representative tasks, run each
   twice (toolkit on/off, same model, fresh sessions), and compare total cost (`/cost`), number of requests,
   and whether the task passed. Per the paper, expect a *lower cost and possibly a slightly lower pass rate*; if
   you do not see the cost drop, uninstall.
3. **Start lean:** `solpi install --target claude --hook-only` keeps only what the benchmarks back — the hook and
   `solpi_read_obs`. Add the full install later if the agent would actually use `solpi_fuse`/`solpi_milestone`.

## 4b. Which commands does the hook rewrite? (`hook_mode`)

A user-reported run of `analyze_sessions.py` over 112 real Claude Code sessions found the first, strictest rule never fired (0 results): almost
every command Claude Code runs starts with `cd <dir> &&` or `VAR=val`. So there are two modes
(`observation_pack.hook_mode` in `sol-pi.json`, or `solpi install --hook-mode`, default **`prefixed`**):

| Mode | Rewritten | Never rewritten |
| :--- | :--- | :--- |
| `safe` | one simple build/test command | anything chained or with env vars |
| `prefixed` (default) | also `cd <dir> && cmd`, `export VAR=val && cmd`, `VAR=val cmd` (≤ 2 prefixes, `&&`/`;`) with **allow-listed** vars only (`PYTHONPATH` project-relative, `CI`, `NODE_ENV`, `RUST_BACKTRACE`, …) | pipes, redirects, `&`, `$()`/backticks, `LD_PRELOAD`, `PATH`, `NODE_OPTIONS`, `PYTEST_ADDOPTS`, any other env var |

Why so strict: the rewrite is **auto-approved** (`permissionDecision: allow`), so it must never let through something
the original command's permission rules would have stopped. There is deliberately **no "wrap every command" mode**:
that would auto-approve `rm -rf ~` too, and `solpi run` has no TTY/stdin and merges stderr. For other commands the
agent can call `solpi_run` itself. The wrapped command also passes `--timeout` (≥ 30 min, above the harness's own
Bash timeout) so long builds are not killed at `solpi run`'s 120 s default.

## 6. Limits we know about

- The reducer's extractor is **keyword-based** (`error`, `fail`, `panic`, `timed out`, …). A failure that prints
  none of them falls back to the raw log (fail-open) or is recoverable via the handle — it is not lost, but it can
  cost an extra turn.
- Our corpus and some reducer fixes were developed together; only the real gcc/g++/Python runs are clean
  held-out data (all kept 100 % of evidence).
- No claim here covers non-English tool output, Windows paths, or very exotic toolchains.

## 7. A/B result: the same agent with and without the toolkit (measured)

`python3 benchmarks/ab/run_ab.py --out ab_results --reps 3` (code and raw data in `benchmarks/ab/`). Claude Code
2.1 + Sonnet 5.5, 10 small tasks I wrote (6 where the test/build output is 8–50 KB, 4 controls with tiny output),
each run 3× per condition = 60 real runs, total API spend **$2.55**. Condition B is `solpi install --hook-only`
(hook mode `prefixed`); both conditions load no user settings, skills or other MCP servers.

| | A (plain) | B (hook-only) |
| :--- | :---: | :---: |
| Tasks solved | **30/30** | **30/30** |
| Total cost | $0.42 | $0.43 (**+3.3 %**, noise) |
| Noisy tasks, geometric-mean cost B/A | | **1.08** (per task 0.92 … 1.42) |
| Control tasks, geometric-mean cost B/A | | **0.94** |
| Receipts produced / recall calls | – | **0 / 0** |
| Bash commands piped to `tail`/`head`/`grep` | **85 %** | **79 %** |

**The toolkit did nothing in these sessions** — not harm, not savings. The hook never fired (0 receipts in 30 runs)
because the agent *already trims its own output*: 4 out of 5 of its commands were
`python3 -m unittest -v 2>&1 | tail -30` or `make 2>&1 | head -50`, and the hook, by design, never rewrites piped
commands. The cost differences above are run-to-run noise (single-run cost standard deviation ≈ 28 %); no task
changed its success.

What this does and does not tell you:
- It **does** show that for short Claude Code sessions on a current model the savings are ≈ 0, which is the
  opposite of the optimistic scenarios in §3 — those assume large raw outputs actually reach the context.
- It does **not** cover long-horizon sessions (these ran 3–9 turns, ≈ $0.04 each), other agents (Codex/GPT may not
  trim output the same way), or tasks where the agent needs the *whole* log. That is where the paper's gains
  come from, and it was not tested here.
- All tasks are solvable and were solved in both conditions (a ceiling effect), so quality differences would only
  show up on harder tasks.
- `python3 benchmarks/analyze_sessions.py` on *your own* history remains the quickest way to see whether big,
  untrimmed outputs exist in your sessions at all.
- Follow-up checks: the same 10 tasks with Haiku (1 run each, $0.09 total) also gave **0 receipts** (77 % of its commands
  were piped), and in the Sonnet runs tool output was only **≈ 1.3 % of the re-read context** (≈ 106k tokens per run,
  mostly system prompt and tool definitions). So in short sessions even a perfect output reducer could save ≈ 1 %;
  the lever, if any, is **long sessions where stale results pile up**, which a Bash hook cannot reach.
