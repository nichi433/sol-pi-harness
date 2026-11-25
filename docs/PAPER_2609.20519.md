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

Through strict **Capability Floors** (requiring zero degradation in task solve rate) and **Efficiency Gates** evaluated on held-out tasks (**EdgeBench**), **only 4 mechanisms survived**.

---

## 3. Complete Technical Checklist: Paper vs. `sol-pi-harness`

| Paper Mechanism / Technique | Section / Detail in `arXiv:2609.20519` | Implemented in `sol-pi-harness`? | Source File & Details |
| :--- | :--- | :---: | :--- |
| **1. Action Fusion** | Combines file edit/write with immediate follow-up validation command (`pytest`, `tsc`, etc.) in 1 tool turn, collapsing 3 steps to 2. | ✅ **100%** | [`src/solpi/action_fusion.py`](../src/solpi/action_fusion.py) (`fuse_edit_and_verify` + AST/JSON syntax pre-check + rollback) |
| **2. ObservationPack** | Content-addressed handle (`obs_<id>`), 2-turn grace window (`grace_turns: 2`), head/tail excerpt (~1 KB), deduplication, and exact paged recall. | ✅ **100%** | [`src/solpi/observation_pack.py`](../src/solpi/observation_pack.py) (`ObservationPackStore.pack`, `read_obs` by page, regex grep, or line radius) |
| **3. Evidence-Preserving Reducer** | Offloads diagnostic logs ($\ge 4$ KiB) to produce a compact receipt, guarded by a **Deterministic Verifier** checking schema, source hash, exit code, secret leaks, compression ratio, and **100% verbatim substring matching** with **Fail-Open** fallback. | ✅ **100%** | [`src/solpi/reducer.py`](../src/solpi/reducer.py) (`verify_receipt`, `reduce_log_fail_open`, `scan_and_redact_secrets`, Gemini Flash delegate + deterministic extractor) |
| **4. Online Context Compaction** | Evaluates compaction at natural plan milestones (`update_plan` / `milestone`) using a **KV-Cache Economic Projection** (`cacheWriteReadRatio`: 12.5) and window pressure ratio. | ✅ **100%** | [`src/solpi/compactor.py`](../src/solpi/compactor.py) (`OnlineContextCompactor.evaluate_milestone`) |
| **5. RSI Auto-Research Loop** | Outer search loop (`Propose -> Probe -> Filter -> Compose`) enforcing Capability Floor + Efficiency Gate. | ✅ **100%** | [`src/solpi/auto_research.py`](../src/solpi/auto_research.py) (`run_auto_research_loop` / `solpi tune`) |
| **6. Official `sol-pi.json` Compatibility** | Supports both `NVlabs/SoL-Pi` camelCase flags (`actionFusion`, `observationPack`, `evidencePreservingReducer`, `onlineContextCompact`, `cacheWriteReadRatio`) and granular settings. | ✅ **100%** | [`src/solpi/config.py`](../src/solpi/config.py) (`normalize_official_solpi_keys`) |

---

## 4. Benchmark Results Reported in `arXiv:2609.20519` (EdgeBench)

Evaluated on **EdgeBench** (51 long-horizon software engineering tasks) across frontier models (including **GPT-5.6 Sol** and **Opus 5**):
- **Token Traffic Reduction**: **44.7% – 49.0%** reduction in total recorded token traffic.
- **API Cost Savings**: **~33% – 50%** reduction in overall operational inference costs.
- **Solve Rate Parity**: Matches or exceeds unmodified baseline harnesses (Pi baseline, Claude Code, Codex).

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
