# Will this help in my Claude Code / Codex / OpenCode sessions?

Short answer: **sometimes, modestly, and not for free.** It is worth installing if your sessions repeatedly run
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
`2,250 × R/2 ≈ 1,100 × R`. **One big log per session roughly pays for the tools; two or more is a gain.** Sessions
with no outputs over ~4 KB only pay the overhead.

## 4. Who should use it

| Your sessions look like… | Verdict |
| :--- | :--- |
| Long agentic runs, repeated `pytest` / `cargo test` / `npm test` / `make` / `tsc`, big failures (C++ templates, many tests) | **Likely worth it.** This is exactly what the reducer targets |
| Context pressure (auto-compaction, 100k+ tokens) | **Likely worth it**; receipts also keep the window clean |
| Multi-agent / subagent swarms (paper: −27 % cost) | Worth trying |
| Short, conversational, or read-and-edit sessions | **Not worth it** — nothing ≥ 4 KB to shrink |
| Mostly `cat`/`grep`/file reads | **No.** Searches and file reads bypass the reducer by design (head/tail only above 10 KiB) |
| You chain commands (`cd app && pytest`, `pytest \| tail`) | The **hook skips them on purpose** (safety), so coverage is lower than you think. Use `solpi_run` explicitly or loosen your habits |
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
   It reports how many Bash results are big, what the hook covers today vs a looser rule, estimated saved
   cache-read tokens, and the **net after the tool overhead**. It is an *upper bound* (ignores auto-compaction,
   recall turns and quality).
2. **A/B on real tasks** (the only way to see quality and extra turns): pick ~10 representative tasks, run each
   twice (toolkit on/off, same model, fresh sessions), and compare total cost (`/cost`), number of requests,
   and whether the task passed. Per the paper, expect a *lower cost and possibly a slightly lower pass rate*; if
   you do not see the cost drop, uninstall.
3. **Start lean:** install, then keep only what pays off — the hook and `solpi_read_obs`. Skip
   `solpi_fuse`/`solpi_milestone` if the agent never uses them.

## 6. Limits we know about

- The reducer's extractor is **keyword-based** (`error`, `fail`, `panic`, `timed out`, …). A failure that prints
  none of them falls back to the raw log (fail-open) or is recoverable via the handle — it is not lost, but it can
  cost an extra turn.
- Our corpus and some reducer fixes were developed together; only the real gcc/g++/Python runs are clean
  held-out data (all kept 100 % of evidence).
- No claim here covers non-English tool output, Windows paths, or very exotic toolchains.
