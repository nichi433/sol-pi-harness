# SoL-Pi Agent Harness Efficiency Protocol (arXiv:2609.20519)

When executing multi-step coding, debugging, build, or test loops, adhere to the four **SoL-Pi** harness efficiency mechanisms to eliminate redundant round-trips and cut context token bloat by ~45-49% without losing diagnostic evidence:

1. **Action Fusion (Single-Turn Edit + Verify)**:
   - When modifying a file and immediately running a linter, compiler, or unit test (`pytest`, `cargo test`, `tsc`, `npm test`), prefer calling the fused MCP tool `solpi_fuse` (or `solpi fuse --file <path> --target "..." --replacement "..." --cmd "<verify_cmd>"`) in **one turn** instead of spending two separate turns.
2. **ObservationPack (Content-Addressed Handles & Paged Recall)**:
   - For commands that may emit verbose output (`> 4 KB`), run them via `solpi_run` (or `solpi run --cmd "<cmd>"`).
   - Large logs are automatically archived into a handle (`obs_<10chars>`). Inspect specific pages or regex matches on demand via `solpi_read_obs(obs_id="obs_...", page=1, grep_pattern="...")` rather than dumping full raw logs into the prompt history.
3. **Evidence-Preserving Reducer (100% Verbatim Quotes)**:
   - Trust the `VERIFIED VERBATIM QUOTES` section in SoL-Pi receipts: every quoted line is deterministically verified (`substring match`) against the raw log with Fail-Open fallback.
4. **Online Context Compaction at Subtask Boundaries**:
   - Whenever you finish a planned subtask milestone, record it via `solpi_milestone` (or isolate noisy exploratory research inside a subagent). When `solpi_milestone` signals an economic KV-cache break-even or window pressure threshold, trigger context compaction (`/compact`) before starting the next subtask.
