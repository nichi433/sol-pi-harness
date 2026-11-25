# Reference Paper Guide: *SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness* (arXiv:2609.20519)

- **Paper URL**: https://arxiv.org/abs/2609.20519 (PDF: https://arxiv.org/pdf/2609.20519)
- **Authors**: Haozhe Liu, Tian Ye, Sensen Gao, Qihang Cao, Yitong Li, Mingchen Zhuge, Duomin Wang, Ruihua Zhang, Ping Luo, Jiawang Bian, Lei Zhu, Ligeng Zhu, Enze Xie, Song Han (NVIDIA NVlabs, NTU, MIT, HKU)
- **Official Pi Repo**: https://github.com/NVlabs/SoL-Pi
- **Companion Recursive Harness Repo**: https://github.com/google-research/harness2

## Summary of the 6 Proposal Families (152 Candidates -> 4 Survivors)
The SoL-Pi outer auto-research loop evaluated 152 candidate modifications across 6 families (`Context`, `Progress`, `Tools`, `Delegation`, `Prompt and Policy`, `Improvement and Evaluation`) on 500+ executable environments and validated on **EdgeBench** (51 long-horizon coding tasks).

Only **4 reusable mechanisms** passed both the **Capability Floor** and **Efficiency Gate**, cutting token traffic by **44.7%–49.0%** and API cost by **~33%–50%**:

1. **Action Fusion (Tools)**: Combines a file edit/write and its immediate follow-up validation command into a single tool call (`solpi_fuse`).
2. **ObservationPack (Observations)**: Replaces large outputs (`>= 10 KiB`) after a 2-turn grace window with stable handles (`obs_<id>`), head/tail excerpts (~1 KB), and exact paged recall (`solpi_read_obs`).
3. **Evidence-Preserving Reducer (Delegation)**: Compresses diagnostic logs (`>= 4 KiB`) into structured receipts verified by a **Deterministic Fail-Open Verifier** (checking schema, SHA-256 hash, exit code, secret leaks, compression ratio, and 100% character-for-character verbatim substring match).
4. **Online Context Compaction (Context)**: Evaluates context compaction at subtask milestone boundaries using a KV-cache economic projection (`cacheWriteReadRatio`: 12.5) and window pressure ratio (`solpi_milestone`).
