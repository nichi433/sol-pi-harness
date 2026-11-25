# Reference Paper Guide: *SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness*

- **Paper ID**: [arXiv:2609.20519](https://arxiv.org/abs/2609.20519) ([PDF](https://arxiv.org/pdf/2609.20519))
- **Authors**: Haozhe Liu, Tian Ye, Sensen Gao, Qihang Cao, Yitong Li, Mingchen Zhuge, Duomin Wang, Ruihua Zhang, Ping Luo, Jiawang Bian, Lei Zhu, Ligeng Zhu, Enze Xie, Song Han (NVIDIA NVlabs, NTU, MIT, HKU)
- **Official Pi Extension Repo**: [`NVlabs/SoL-Pi`](https://github.com/NVlabs/SoL-Pi)
- **Companion Recursive Harness Reference**: [`google-research/harness2`](https://github.com/google-research/harness2)

---

## 1. Abstract & Motivation

As coding and software engineering agents transition from short single-turn completions to multi-hour, around-the-clock exploration trajectories, **token traffic and API costs** become the primary bottleneck. Rather than modifying foundation model weights, **SoL-Pi** applies **Recursive Self-Improvement (RSI)** directly to the **agent harness**—the surrounding execution layer responsible for context management, tool invocation, observation storage, and delegated reading.

---

## 2. The Auto-Research Outer Loop (152 Proposals $\rightarrow$ 4 Survivors)

SoL-Pi scaled automated research loops across **500+ executable developer environments** built from real GitHub Issue-PR pairs and synthetic software tasks.

### The 6 Proposal Families Explored (152 Total Candidate Hypotheses)
1. **Context (`Context`)**: Dynamic window pruning, milestone-triggered compaction, KV-cache-aware history rewriting.
2. **Progress (`Progress`)**: Plan step tracking, loop detection, early termination gates.
3. **Tools (`Tools`)**: Action fusion, batched mutations, pre-execution syntax validation.
4. **Delegation (`Delegation`)**: Cheap-model log readers, evidence-preserving reduction with deterministic verification.
5. **Prompt & Policy (`Prompt and Policy`)**: System prompt compression, behavioral token budgets.
6. **Improvement & Evaluation (`Improvement and Evaluation`)**: Capability floor verifiers, held-out generalization gates.

### Why 148 Proposals Were Rejected
Most naive token-saving ideas (e.g., aggressive truncation, blind summarization without verification, or restricting tool retries) saved tokens by **degrading the agent's solve rate** or causing the agent to hallucinate missing stacktrace details and loop even longer.

Through **capability-constrained selection** (a Capability Floor with tolerances frozen before held-out evaluation) and **Efficiency Gates**, **only 4 mechanisms survived**. Note that "capability-constrained" is *not* "lossless": the combined stack scores a few percent below the baseline (see §4).

---

## 3. Complete Technical Checklist: Paper vs. `sol-pi-harness`

| Paper Mechanism / Technique | Section / Detail in `arXiv:2609.20519` | Implemented in `sol-pi-harness`? | Source File & Details |
| :--- | :--- | :---: | :--- |
| **1. Action Fusion** | Combines file edit/write with immediate follow-up validation command (`pytest`, `tsc`, etc.) in 1 tool turn, collapsing 3 steps to 2. | 🟡 **Port** | [`src/solpi/action_fusion.py`](../src/solpi/action_fusion.py) (`fuse_edit_and_verify` + AST/JSON syntax pre-check + rollback) |
| **2. ObservationPack** | Content-addressed handle (`obs_<id>`), 2-turn grace window (`grace_turns: 2`), head/tail excerpt (~1 KB), deduplication, and exact paged recall. | 🟠 **Partial** — no 2-request grace window (an external wrapper cannot rewrite earlier tool results); packs immediately | [`src/solpi/observation_pack.py`](../src/solpi/observation_pack.py) (`ObservationPackStore.pack`, `read_obs` by page, regex grep, or line radius) |
| **3. Evidence-Preserving Reducer** | Offloads diagnostic logs ($\ge 4$ KiB) to produce a compact receipt, guarded by a **Deterministic Verifier** checking schema, source hash, exit code, secret leaks, compression ratio, and **100% verbatim substring matching** with **Fail-Open** fallback. | 🟡 **Port** — build/test commands only; credentials are redacted instead of falling back to the raw log | [`src/solpi/reducer.py`](../src/solpi/reducer.py) (`verify_receipt`, `reduce_log_fail_open`, `scan_and_redact_secrets`, Gemini Flash delegate + deterministic extractor) |
| **4. Online Context Compaction** | Evaluates compaction at natural plan milestones (`update_plan` / `milestone`) using a **KV-Cache Economic Projection** (`cacheWriteReadRatio`: 12.5) and window pressure ratio. | 🟠 **Partial** — gate + request estimator implemented; cannot run native compaction, only recommends `/compact` | [`src/solpi/compactor.py`](../src/solpi/compactor.py) (`OnlineContextCompactor.evaluate_milestone`) |
| **5. RSI Auto-Research Loop** | Outer search loop (`Propose -> Probe -> Filter -> Compose`) enforcing Capability Floor + Efficiency Gate. | 🟡 **Reduced** — grid search with train/holdout split over the reducer knobs, not the paper's agent-driven research loop | [`src/solpi/auto_research.py`](../src/solpi/auto_research.py) (`run_auto_research_loop` / `solpi tune`) |
| **6. Official `sol-pi.json` Compatibility** | Supports both `NVlabs/SoL-Pi` camelCase flags (`actionFusion`, `observationPack`, `evidencePreservingReducer`, `onlineContextCompact`, `cacheWriteReadRatio`) and granular settings. | ✅ | [`src/solpi/config.py`](../src/solpi/config.py) (`normalize_official_solpi_keys`) |

---

## 4. Benchmark Results Reported in `arXiv:2609.20519` (re-checked against the paper's Tables 1-4)

The paper's headline ("44.7–49.0 % fewer tokens, ~1/3 lower cost") is **relative to Pi**, the paper's own baseline harness, for the **complete four-mechanism stack**, and it comes **with a score drop**. All numbers are from the paper (EdgeBench, 51 public tasks, one reported run per configuration):

| EdgeBench | Tokens | Cost | Avg. score |
| :--- | :---: | :---: | :---: |
| GPT-5.6 Sol — Codex (native) | 3.054 B | $1,787 | 34.7 |
| GPT-5.6 Sol — Pi (baseline) | 2.154 B | $1,339 | 44.8 |
| GPT-5.6 Sol — SoL-Pi **full stack** ("Efficiency") | 1.099 B (**−49.0 %** vs Pi) | $894 (**−33.2 %**) | 42.0 (**93.7 %** of Pi) |
| GPT-5.6 Sol — SoL-Pi **ObservationPack only** ("Performance") | 2.022 B (−6.1 %) | $1,271 (−5.1 %) | **47.2 (+5.3 %)** |
| Opus 5 — Claude Code (native) | 2.005 B | $2,535 | 43.7 |
| Opus 5 — Pi (baseline) | 2.370 B | $1,741 | 44.8 |
| Opus 5 — SoL-Pi **full stack** (no re-search; transfer) | 1.310 B (−44.7 % vs Pi, **−34.6 % vs Claude Code**) | $1,158 (−33.5 % vs Pi, **−54.3 % vs Claude Code**) | 42.2 (94.3 % of Pi, **96.6 % of Claude Code**) |
| Opus 5 — SoL-Pi **Action Fusion only** ("Performance") | 2.102 B | $1,605 | **50.5** |

| Other benchmarks (GPT-5.6 Sol) | Codex | Pi | SoL-Pi |
| :--- | :---: | :---: | :---: |
| Terminal-Bench 4 (63 CPU tasks): solved / total cost | 18 / $272 | 18 / $286 | **15** / $211 (−26.3 % cost, −11.6 % cost per solved) |
| IMO 2026 (6 problems): passed / cost per passed | 5 / $22.89 | 3 / $25.32 | 3 / $20.90 |

What to take from it:
- **The saving is real but not free.** The full stack trades ~3–6 % of score on EdgeBench and 3 of 18 solved tasks on Terminal-Bench 4 for the lower cost. The paper's highest-*scoring* configurations are single mechanisms (ObservationPack on GPT, Action Fusion on Opus), which save far less (≈ 6 % tokens).
- **Most of the traffic is cache reads** (≈ 2.1 of 2.15 B tokens for Pi); the full stack halves cache reads but *increases* cache writes (0.014 → 0.032 B), and still lowers cost.
- **Preliminary.** The paper says so: one frozen configuration, one backend developed on, mechanisms trigger less often on Opus 5, and the scaling-law claims are explicitly not established.
- **Harness-level, not add-on.** The mechanisms are extensions *inside* Pi. `sol-pi-harness` ports them to an external MCP/hook layer, so it can reproduce neither the exact behaviour nor the numbers (see §3 and [WHEN_IT_HELPS](./WHEN_IT_HELPS.md)).

---

## 5. BibTeX Citation

```bibtex
@article{liu2026solpi,
  title={SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness},
  author={Liu, Haozhe and Ye, Tian and Gao, Sensen and Cao, Qihang and Li, Yitong and Zhuge, Mingchen and Wang, Duomin and Zhang, Ruihua and Luo, Ping and Bian, Jiawang and Zhu, Lei and Zhu, Ligeng and Xie, Enze and Han, Song},
  journal={arXiv preprint arXiv:2609.20519},
  year={2026},
  url={https://arxiv.org/abs/2609.20519}
}
```
