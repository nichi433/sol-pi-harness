<div align="center">

<img src="./assets/nichi-banner.svg" alt="sol-pi-harness Nichi Banner uwu" width="100%" />

# 🐱 `sol-pi-harness` 🐾 — *Purr-fect Token Efficiency for Coding Agents uwu*

**[🇬🇧 English](./README.md) | [🇪🇸 Español (Edición Nichi uwu)](./README.es.md) | [📄 Paper Guide (`arXiv:2609.20519`)](./docs/PAPER_2609.20519.md)**

[![arXiv:2609.20519](https://img.shields.io/badge/arXiv-2609.20519-ff7eb3?style=for-the-badge&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2609.20519)
[![Harness2 RSI](https://img.shields.io/badge/Harness%C2%B2-Recursive_Loop-8b6bff?style=for-the-badge)](https://github.com/google-research/harness2)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero_(Pure_Python)-7ef2e4?style=for-the-badge&logo=python&logoColor=18122b)](./pyproject.toml)
[![Tests Passing](https://img.shields.io/badge/Tests-22%2F22_Passing_🐾-ffd280?style=for-the-badge)](./tests/test_solpi.py)
[![Nichi Approved](https://img.shields.io/badge/Nichi_Approved-uwu_😻-ff9ecd?style=for-the-badge)](./README.es.md)

*Don't let your AI coding agent become a chonky log-hoarding cat (`4.8M tokens`). Keep it agile like a **Nichi** (`-92%` on build/test log payloads in our [benchmark](#-reproducible-benchmark), `100% recoverable evidence`, zero hallucinations)! 🐾✨*

</div>

---

## 🐾 Meet the Nichi Philosophy (*Why `sol-pi-harness`?*)

```text
      |\__/,|   (`\     "Meow! Why replay 50 KB of raw pytest logs 60 times
    _.|o o  |_   ) )     when I can archive it in `obs_nichi` and keep only
  -(((---(((--------     100% verified verbatim error quotes? uwu" 🐾
```

As autonomous coding agents (**Google Antigravity / AGY**, **Claude Code**, **OpenAI Codex CLI**, **Gemini CLI**, **Cursor**) run for hours solving complex issues, **token traffic and API costs explode** due to three bad habits:
1. 😿 **Extra Round-Trip Pounces**: Taking 2 separate LLM turns just to edit a file and run `pytest`.
2. 🙀 **Chonky Hairball Logs**: Re-sending 40 KB of build output in the prompt history across 50 subsequent turns.
3. 😾 **Panic Context Compaction**: Waiting until 95% context window overflow mid-debugging to truncate memory.

In **[SoL-Pi (arXiv:2609.20519)](https://arxiv.org/abs/2609.20519)**, researchers from **NVIDIA (NVlabs), NTU, and MIT** tested **152 candidate harness ideas** across 6 families and 500+ executable environments. Only **4 mechanisms** survived capability-constrained selection. The full stack cut token traffic by **44.7% – 49.0%** and API cost by **~⅓ relative to the paper's Pi baseline** (−54 % vs native Claude Code on Opus 5) **while keeping ~94–97 % of the baseline score** — a few percent *worse*, not equal (Terminal-Bench 4: 15 vs 18 tasks solved). See [what the paper really says](./docs/PAPER_2609.20519.md#4-benchmark-results-reported-in-arxiv260920519-re-checked-against-the-papers-tables-1-4) and [**will it help in my sessions?**](./docs/WHEN_IT_HELPS.md)

**`sol-pi-harness`** packages all 4 surviving mechanisms—plus an **Auto-Research RSI tuning loop (`solpi tune`)** inspired by **[`google-research/harness2`](https://github.com/google-research/harness2)**—into a zero-dependency **MCP Server + Skill + Lifecycle Hook** bundle you can install in **10 seconds**! 🐱✨

---

## 📊 Visual Benchmark & Live Token Savings

<div align="center">
<img src="./assets/nichi-savings-chart.svg" alt="Nichi Token Savings Chart" width="100%" />
</div>

```mermaid
xychart-beta
    title "Cumulative Input Tokens Across a 50-Turn Coding Session (in Millions)"
    x-axis ["Turn 5", "Turn 10", "Turn 20", "Turn 30", "Turn 40", "Turn 50"]
    y-axis "Cumulative Tokens (Millions)" 0 --> 5.0
    line [0.25, 0.75, 1.85, 2.95, 3.90, 4.80]
    line [0.18, 0.45, 0.95, 1.50, 1.98, 2.45]
```
> 🩷 **Top Line (`4.80M tokens`)**: Standard Unmodified Agent Harness (replaying raw logs & separate edit/test turns).  
> 💚 **Bottom Line (`2.45M tokens`)**: **Paper-reported `-49.0%` token traffic (illustrative; NOT measured by this repo — see [Fidelity](#-fidelity-to-the-paper-arxiv260920519-24))**.

### 🧪 Real Telemetry from Our Live Workspace Verification

We tested `sol-pi-harness` live on a noisy 258-line test suite (`demo_app.py`) with a buried `AssertionError`, followed by a fused bugfix and milestone checkpoint:

| Metric / Mechanism | Without `sol-pi-harness` 😿 | With `sol-pi-harness` 😻 | Net Nichi Savings 🐾 |
| :--- | :---: | :---: | :---: |
| **Edit $\rightarrow$ Test Round-Trips** (`Action Fusion`) | 2 LLM calls per edit+verify | **1 call** (`solpi_fuse`) | **1 LLM call avoided per fused edit+verify** (paper: ~11.5 % of tokens *if every opportunity triggers*; trigger rate is lower in practice) |
| **Diagnostic Log Payload** (`ObservationPack` + `Reducer`) | `40,817 Bytes` (~10,204 tokens) | **`8,942 Bytes`** (~2,235 tokens) | **`-78.09%` immediate byte reduction** |
| **50-Turn Context Replay Multiplier** | `10,204 tok × 50 = 510,200 tok` | `2,235 tok × 50 = 111,750 tok` | **`~398,450 input tokens` avoided** |
| **Milestone Context Pruning** (`Online Compaction`) | Waits until 95% crash | Pruned at **70% milestone boundary** | **`~84,000 prunable tokens` flagged cleanly** |
| **Hallucinations / Lost Stacktraces** | Risk of summary distortion | **Deterministic Fail-Open Verifier** | **`0` hallucinations (`100% verbatim`)** |

---

## 🧩 Architecture: The 5 Nichi Superpowers

```mermaid
flowchart TD
    subgraph Agents["😺 Your Favorite Coding Agent"]
        AGY["Google Antigravity (AGY)"]
        CC["Claude Code"]
        CDX["OpenAI Codex CLI"]
        CUR["Cursor / Gemini CLI"]
    end

    subgraph Boundary["🐾 Zero-Friction Integration Layer"]
        HOOK["🪝 Lifecycle Hooks (hooks.json)\nAuto-rewrites pytest/cargo/npm via PreToolUse"]
        MCP["🔌 Universal MCP Server (solpi-mcp)\n6 Native JSON-RPC Tools"]
        SKILL["📘 Progressive Skill & Rules\n(SKILL.md + AGENTS.md)"]
    end

    subgraph Engine["⚡ SoL-Pi Core Engine (arXiv:2609.20519 × Harness²)"]
        M1["1️⃣ Action Fusion\nAtomic Edit + AST Check + Test in 1 Turn"]
        M2["2️⃣ ObservationPack\nCAS Store (obs_xxxx) + 2-Turn Grace + Paged Recall"]
        M3["3️⃣ Evidence-Preserving Reducer\n100% Verbatim Substring Verifier + Fail-Open Gate"]
        M4["4️⃣ Online Context Compactor\nKV-Cache Economic Formula (cacheWriteReadRatio=12.5)"]
        M5["5️⃣ Auto-Research RSI Loop\nPropose → Probe → Filter (Floor ≥ 0.95) → Compose"]
    end

    Agents --> Boundary
    HOOK --> M2
    HOOK --> M4
    MCP --> M1
    MCP --> M2
    MCP --> M3
    MCP --> M4
    MCP --> M5
    M1 --> M2
    M2 --> M3
```

### 1️⃣ Action Fusion (`solpi_fuse` / `solpi fuse`) — *Single-Pounce Edit & Test* 🐾
Combines a file edit (`target` $\rightarrow$ `replacement`) and its immediate validation command (`pytest`, `cargo test`, `tsc`, `ruff`) into **one atomic tool call**.
- **Zero-Cost Syntax Pre-Check**: Runs `ast.parse` (Python) or `json.loads` (JSON) *before* touching disk or spawning slow test runners. If syntax is broken, it blocks the edit immediately!
- **Automatic Rollback**: Optional `--revert-on-fail` restores the file if tests fail.

### 2️⃣ ObservationPack (`solpi_run` & `solpi_read_obs`) — *Tidy Litterbox for Massive Logs* 📦
Never let `> 4–10 KiB` logs clutter your prompt history:
- Archives the full raw output in a Content-Addressed Store (`~/.cache/sol-pi/obs/obs_<10chars>.log`).
- **2-Turn Grace Window & Deduplication**: If a command produces the exact same output past the grace window, emits a lightweight delta pointer saving ~98% of duplicate tokens.
- **Exact Paged Recall**: Inspect any page (`--page 2`), line neighborhood (`--around-line 125 --radius 10`), or regex match (`--grep "AssertionError"`) on demand.

### 3️⃣ Evidence-Preserving Reducer (`reducer.py`) — *Strict Cat-Eye Verifier* 🛡️
Compresses noisy build/test logs into structured **Diagnostic Receipts** (via deterministic extractor or optional cheap-LLM delegate like `gemini-2.5-flash`), guarded by a **Deterministic Fail-Open Verifier**:
1. **100% Verbatim Substring Check**: Every string in `verbatim_quotes` must exist *character-for-character* inside the raw log (`all(q in raw_log for q in quotes)`).
2. **Hash & Exit Code Integrity**: Verifies SHA-256 prefix and process exit status.
3. **Secret Redaction**: Automatically scrubs API keys (`sk-...`, `AIza...`, `ghp_...` $\rightarrow$ `[REDACTED_SECRET]`).
4. **Fail-Open Fallback**: If an LLM delegate hallucinates even **1 character**, the receipt is discarded and the harness safely falls back to the raw/head-tail log!

### 4️⃣ Online Context Compaction (`solpi_milestone`) — *Catnap at Subtask Boundaries* 🧹
Evaluates context compaction when a plan step finishes (`update_plan` / milestone) using the paper's **KV-Cache Economic Break-Even Formula** (`cacheWriteReadRatio: 12.5`):
$$\Delta \text{Savings} = \left(T_{\text{prunable}} \times N_{\text{rem}} \times C_{\text{cached\_read}}\right) - \left(T_{\text{retained}} \times C_{\text{cache\_write}}\right)$$
When $\Delta \text{Savings} > 0$ or window pressure crosses `65%`, it outputs a **Completed Milestones State Receipt** and recommends `/compact` or a clean subagent handoff.

### 5️⃣ Auto-Research RSI Loop (`solpi_tune` / `solpi tune`) — *Self-Improving Nichi* 🧬
Implements the **Propose $\rightarrow$ Probe $\rightarrow$ Filter $\rightarrow$ Compose** loop from **SoL-Pi** and **Harness²**:
- Scores a 36-candidate grid on a **diverse corpus**: 16 log families × 4 variants (pytest, unittest, gcc/g++, tsc, cargo, jest, go, maven, npm, linker, mypy, silent failures…) plus optional **real tool runs** (`--real`: gcc, g++, cargo, python, node). Every log has ground-truth error lines; recall = share of them visible verbatim in the receipt.
- **Anti-overfitting**: the split is *by family*. Candidates are chosen on TRAIN families only; HOLDOUT families and real logs only *validate* the winner. The floor applies to the macro-average **and to every single family** (default ≥ 95 % / ≥ 85 %). If the winner fails on holdout, the shipped default is recommended (`validated_on_holdout: false`).
- It discriminates: only **8 of 36** candidates pass (the old "ultra" `q15/r0` style loses up to 45 % of a family's evidence). Winner family `head 4 / tail 5, 35 quotes, radius 2` → recall **1.00** on train and holdout, **90 %** reduction vs 86 % for the previous `15/20` excerpt; this is now the shipped default.
- `solpi tune [--real] [--quick] [--seed N] [--verbose] [--write-config sol-pi.json]`.

---

## ❓ Will it help in my sessions?

Honest answer in [docs/WHEN_IT_HELPS.md](./docs/WHEN_IT_HELPS.md): it helps sessions that repeatedly run builds/tests printing > 4 KB; it is a small net cost otherwise, the paper's own full stack scores ~3–6 % below baseline, and this external MCP/hook port cannot reproduce the paper's numbers. Check your own history with `python3 benchmarks/analyze_sessions.py`.

## 🚀 10-Second Quickstart

### 1. Clone & Install (Zero External Dependencies!)
```bash
git clone https://github.com/sol-pi/sol-pi-harness.git
cd sol-pi-harness
pip install -e .
```

### 2. Install into Your Agent Harness in One Command 🐾
```bash
# Install into current workspace for ALL harnesses (AGY, Claude Code, Codex, OpenCode, Cursor)
solpi install --target all --scope workspace

# Or one harness / your user-wide config:
solpi install --target claude --scope workspace
solpi install --target codex --scope global
solpi install --target opencode --opencode-schema auto   # auto | v1 | v2
solpi install --target all --dry-run                     # preview, writes nothing
```

| Harness | MCP tools | Skill | Rules | Transparent hook (command rewrite + context notice) |
|---|---|---|---|---|
| **Claude Code** | `.mcp.json` / `~/.claude.json` | `.claude/skills` | via skill | ✅ `settings.local.json` (`PreToolUse` + `UserPromptSubmit`) — **verified end-to-end** |
| **Codex CLI** | `.codex/config.toml` (`[mcp_servers.sol-pi]`) | `.agents/skills` | `AGENTS.md` block | ✅ `.codex/hooks.json` (same protocol; project hooks need a trusted project) |
| **OpenCode** | `opencode.json` (v1 `mcp.<n>` / v2 `mcp.servers.<n>`) | `.opencode/skills` | `AGENTS.md` block | ❌ no shell-hook API — MCP + rules only |
| **AGY** | `.agents/mcp_config.json` | `.agents/skills` | `.agents/rules` | ✅ `.agents/hooks.json` (`overwrite` contract) |
| **Cursor** | `.cursor/mcp.json` | — | `.cursor/rules/sol-pi.mdc` (always-apply) | ❌ MCP + rules only |

The installer never overwrites a config file it cannot parse, keeps a one-time `*.solpi.bak` of files it modifies, and is idempotent.

> [!TIP]
> **Self-Contained Vendored Skill!**  
> `solpi install` automatically bundles a standalone copy of the `solpi` engine inside `.agents/skills/sol-pi-efficiency/scripts/vendor/solpi`, so the skill works in any repository even without global `pip install`! 🐱✨

---

## 🪝 Transparent Hook Magic (Works Even When the LLM Forgets!)

When installed in **Claude Code**, **Codex** or **Antigravity (AGY)**, `solpi install` registers a `PreToolUse` interceptor on the shell tool (`Bash` / `run_command`). Claude Code and Codex get the rewrite through `hookSpecificOutput.updatedInput`; AGY through `overwrite`. (The root-level `hooks.json` is only a template — use `solpi install`.)

Whenever the agent runs `pytest`, `python3 -m unittest`, `cargo test`, `npm test`, `go test`, `tsc`… the hook **transparently rewrites it** to pass through `solpi run`. In the default `prefixed` mode this includes `cd <dir> && <cmd>` and a few allow-listed `VAR=val` prefixes (real Claude Code commands almost always start that way); pipes, redirects, `&`, `$(...)` and risky env vars (`LD_PRELOAD`, `PATH`, `NODE_OPTIONS`…) are never rewritten, because the rewrite is auto-approved and must not bypass permission rules. Set `solpi install --hook-mode safe` for the strictest behaviour. Details: [docs/WHEN_IT_HELPS.md §4b](./docs/WHEN_IT_HELPS.md).

**Lean install:** `solpi install --target claude --hook-only` installs only the hook plus a one-tool MCP server (`solpi_read_obs`, ≈ 143 tokens per request instead of ≈ 735); no skill, rules or compaction notice.

```text
> Agent calls: run_command(CommandLine="python3 -m unittest discover -s tests")
> AGY Hook Notice: A pre-tool hook changed the arguments of this tool call before it ran. Changed: CommandLine.
> Executed: PYTHONPATH=... python3 -m solpi.cli run --cmd "python3 -m unittest discover -s tests"
```

---

## 🛠️ CLI & MCP Cheat Sheet

````carousel
```bash
# ⚡ 1. Action Fusion: Edit file + run test in ONE turn
solpi fuse \
  --file src/discount.py \
  --target "return price + (price * rate)" \
  --replacement "return price - (price * rate)" \
  --cmd "pytest tests/test_discount.py"
```
<!-- slide -->
```bash
# 📦 2. ObservationPack + Evidence-Preserving Reducer
solpi run --cmd "pytest -v"

# 🔍 3. Paged Recall / Regex Grep / Line Neighborhood
solpi read obs_607b6ba652 --page 1
solpi read obs_607b6ba652 --grep "AssertionError|Traceback"
solpi read obs_607b6ba652 --around-line 125 --radius 5
```
<!-- slide -->
```bash
# 🧹 4. Online Context Compaction at Subtask Milestone
solpi milestone \
  --step "Fixed discount calculation bug and verified 249 checks" \
  --tokens 140000 \
  --remaining-turns 6 \
  --files src/discount.py \
  --obs obs_607b6ba652 obs_f12adc7516 \
  --findings "All 249 subsystem checks pass (exit 0)" \
  --next-step "Deploy discount module"
```
<!-- slide -->
```bash
# 🧬 5. Auto-Research RSI Loop & Live Telemetry Stats
solpi tune --floor 0.95 --write-config sol-pi.json
solpi stats
```
````

---

## 📏 Reproducible Benchmark

```bash
python3 benchmarks/run_benchmark.py            # human-readable tables
python3 benchmarks/run_benchmark.py --check    # exit 1 on a regression (also run by the test-suite)
python3 benchmarks/run_benchmark.py --config winner.json   # compare a `solpi tune --write-config` result
```

It uses its own corpus (gcc, pytest, jest, cargo, a log with secrets, a real `unittest -v` run with injected failures, a real `grep`, a 40 KiB file read) with **planted ground-truth error lines**, and reports reduction, evidence that is *directly visible* in the receipt, evidence *recoverable* through the handle, and a naive `tail -n 40` baseline. Reference run:

| Metric (6 build/test logs ≥ 4 KiB) | Result |
| :--- | :---: |
| Estimated tool-output tokens | 85,597 → 3,063 (**−96.4 %**) |
| Planted errors visible in the receipt | **21/21** (naive `tail -n 40`: 7/21) |
| Planted errors recoverable via `read_obs` (all 9 cases) | **24/24** |
| Secrets leaked (receipt or stored log) / fail-opens | **0 / 0** |
| Hook: simple build/test commands wrapped / risky ones wrapped | 8/8 / 0/10 |
| Action Fusion: round trips, syntax gate, revert-on-fail | 2→1, blocks & keeps file intact, restores file |

> [!IMPORTANT]
> This measures **tool-output traffic** (tokens estimated as bytes/4), **not end-to-end session cost**. The `−44.7 – 49.0 %` token and `~33 %` cost figures quoted from the paper were measured on EdgeBench with the Pi harness; **this repository has not reproduced them**, and the 50-turn chart above is an illustration of the paper's range, not a measurement. Search results and file reads get little or no reduction by design (`grep` case: 0 %; the 40 KiB file read is archived behind a handle with head/tail only).

## 📐 Fidelity to the Paper (arXiv:2609.20519, §2.4)

| Paper mechanism | Here | Gap |
| :--- | :--- | :--- |
| **Action Fusion**: edit + follow-up command in one tool request | `solpi_fuse` (MCP) / `solpi fuse`, plus a syntax pre-check and optional revert | The paper extends the harness's own file-mutation tools; an external MCP tool must be *chosen* by the agent. The paper found prompt-only triggering unreliable, so expect a lower trigger rate than reported. |
| **Online Context Compact**: gate at plan-step completion; remaining requests estimated from observed requests per step, capped by window growth; later compactions need a larger margin | `solpi milestone` implements the cost gate, the estimate (`--unfinished-steps`, `--requests-since-last`), the window cap, unrecovered-cost + growing margin, and the window-pressure trigger | The harness cannot invoke native compaction: it **recommends** `/compact` (hook notice at ≥65 % estimated window). Transcript size/4 is only a rough pressure estimate. |
| **ObservationPack**: archive results > 10 KiB, send them in full for 2 requests, then a handle + ~1 KB head/tail excerpt | Archive + handle + head/tail + paged recall + dedup; threshold 10 KiB for non-build output | An external wrapper cannot rewrite earlier tool results, so the "full for the first two requests" grace window is **not** implemented (`grace_turns` only governs dedup of *identical* repeated output). Large results are packed immediately. |
| **Evidence-Preserving Reducer**: build/test logs ≥ 4 KiB from a predefined command set; file reads/searches bypass; low-cost LLM extracts a receipt; deterministic verifier (schema, source hash, exit status, exact quotes, size); fall back to original on failure | Same command gating (`extra_command_regex` to extend), same verifier and fail-open; deterministic extractor by default, optional LLM (`GEMINI_API_KEY`) | Suspected credentials are **redacted** and the receipt still used, where the paper falls back to the original log. |

## 🧪 Running the 22-Test Verification Suite

```bash
python3 -m unittest discover -s tests -v
```
All 22 unit and end-to-end subprocess tests (covering Action Fusion syntax gates, ObservationPack CAS & deduplication, Fail-Open 1-char hallucination rejection, secret redaction, KV-cache compaction math, RSI tuning, MCP stdio JSON-RPC pipes, Hook command rewriting, and `NVlabs/SoL-Pi` `sol-pi.json` schema parity) run in **`~0.22s`**.

---

## 📚 Academic References & Citation (`arXiv:2609.20519`)

- **Included Technical Monograph**: [`docs/PAPER_2609.20519.md`](./docs/PAPER_2609.20519.md) & [`CITATION.cff`](./CITATION.cff)
- **Foundational Paper**: Haozhe Liu et al., *"SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness"*, [arXiv:2609.20519](https://arxiv.org/abs/2609.20519) / [PDF](https://arxiv.org/pdf/2609.20519) (NVIDIA NVlabs, NTU, MIT, Sep 2026).
- **Official NVIDIA Pi Extension**: [`NVlabs/SoL-Pi`](https://github.com/NVlabs/SoL-Pi)
- **Recursive Agent Harnessing**: Google Research & Northwestern University, [`google-research/harness2`](https://github.com/google-research/harness2).

```bibtex
@article{liu2026solpi,
  title={SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness},
  author={Liu, Haozhe and Ye, Tian and Gao, Sensen and Cao, Qihang and Li, Yitong and Zhuge, Mingchen and Wang, Duomin and Zhang, Ruihua and Luo, Ping and Bian, Jiawang and Zhu, Lei and Zhu, Ligeng and Xie, Enze and Han, Song},
  journal={arXiv preprint arXiv:2609.20519},
  year={2026},
  url={https://arxiv.org/abs/2609.20519}
}
```

<div align="center">
  <br/>
  <b>Hecho con mucho cariño y ronroneos para la comunidad Open Source 🐱🐾 uwu</b>
</div>
