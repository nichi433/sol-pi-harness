---
name: sol-pi-efficiency
description: >-
  Implements the 4 token-efficiency and cost-reduction mechanisms from SoL-Pi (arXiv:2609.20519)
  and Harness2 (google-research/harness2): Action Fusion, ObservationPack, Evidence-Preserving
  Reducer, and Online Context Compaction. Activate this skill during multi-step coding, test suites,
  build debugging, log analysis, or harness optimization.
---

# SoL-Pi Multi-Harness Efficiency Skill (`arXiv:2609.20519`)

This skill equips the agent with the four surviving mechanisms discovered by **SoL-Pi** (*Recursively Scaling Auto-Research Loops for Efficient Agent Harness*) and **Harness²** (*Recursive Agent Harnessing*).

---

## Available Interfaces

Depending on your active harness (**Antigravity / AGY**, **Claude Code**, **OpenAI Codex CLI**, **Cursor**), you can invoke SoL-Pi either via the **MCP Server (`sol-pi`)** or the **standalone CLI script**:

- **MCP Tools**: `solpi_fuse`, `solpi_run`, `solpi_read_obs`, `solpi_milestone`, `solpi_telemetry`, `solpi_tune`
- **Portable Script**: [solpi_tool.py](./scripts/solpi_tool.py) (or installed `solpi` CLI binary)

---

## Mechanism 1: Action Fusion (`fuse`)

Eliminate the predictable 2-turn round-trip of editing a file, waiting for tool completion, and then calling a test runner.

### via MCP (`solpi_fuse`)
```json
{
  "file_path": "src/auth.py",
  "target": "return token == expected",
  "replacement": "return hmac.compare_digest(token, expected)",
  "verify_cmd": "pytest tests/test_auth.py -q"
}
```

### via CLI / Script
```bash
python3 skills/sol-pi-efficiency/scripts/solpi_tool.py fuse \
  --file src/auth.py \
  --target "return token == expected" \
  --replacement "return hmac.compare_digest(token, expected)" \
  --cmd "pytest tests/test_auth.py -q"
```
*Features*: Automatic AST/JSON syntax pre-check before touching disk or running slow tests, plus optional `--revert-on-fail`.

---

## Mechanism 2 & 3: ObservationPack + Evidence-Preserving Reducer (`run` & `read`)

Never let `> 10 KB` build/test outputs replay across 40+ subsequent turns.

1. **Execute verbose commands packed**:
   - **MCP**: `solpi_run(cmd="pytest -v")`
   - **CLI**: `python3 skills/sol-pi-efficiency/scripts/solpi_tool.py run --cmd "pytest -v"`
2. **Understand the Receipt**:
   - Output `>= 4 KB` is archived under a content-addressed handle (`obs_<10chars>`).
   - You receive the Head lines, **Verified Verbatim Quotes** (deterministically verified character-for-character against the raw log with Fail-Open safety), and Tail lines.
   - Repeated identical outputs automatically collapse into a deduplicated handle reference.
3. **Paged Recall on Demand**:
   - **By Page**: `solpi_read_obs(obs_id="obs_8f3a1b2c9d", page=2)`
   - **By Regex Grep**: `solpi_read_obs(obs_id="obs_8f3a1b2c9d", grep_pattern="AssertionError|Traceback")`
   - **Around Specific Line**: `solpi_read_obs(obs_id="obs_8f3a1b2c9d", around_line=142, radius=15)`

---

## Mechanism 4: Online Context Compaction (`milestone`)

Instead of compacting mid-debugging when context hits 95%, evaluate compaction at **clean subtask milestones**:

- **MCP**:
  ```json
  {
    "completed_step": "Implemented JWT signature validation and passed unit tests",
    "current_tokens_est": 85000,
    "expected_remaining_turns": 6,
    "files_modified": ["src/auth.py", "tests/test_auth.py"],
    "obs_handles": ["obs_8f3a1b2c9d"],
    "key_findings": "All 14 auth unit tests pass",
    "next_step": "Integrate auth middleware into FastAPI router"
  }
  ```
- When `solpi_milestone` reports `RECOMMENDED NOW`, trigger `/compact` (or delegate the next subtask to a fresh subagent) using the generated **Completed Milestones State Receipt**.

---

## Mechanism 5: Auto-Research RSI Loop (`tune`)

To auto-calibrate `sol-pi.json` thresholds for your repository using SoL-Pi's **Capability Floor + Efficiency Gate**:
```bash
python3 skills/sol-pi-efficiency/scripts/solpi_tool.py tune --floor 0.95 --write-config sol-pi.json
```

---

## Reference Paper & Theoretical Background

For complete technical details on the 152 candidate hypotheses across 6 proposal families, the Fail-Open Verifier guarantees, and the EdgeBench evaluation from **arXiv:2609.20519**, read:
- [PAPER_2609.20519.md](./references/PAPER_2609.20519.md)

