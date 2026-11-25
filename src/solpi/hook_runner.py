"""Lifecycle Hook Runner (`solpi-hook`) for Antigravity (AGY) and Claude Code.

Automatically enforces SoL-Pi efficiency mechanisms at the harness lifecycle boundary
even when the LLM calls native `run_command` or `Bash` directly:

1. `PreToolUse`:
   - Matches heavy test/build/diagnostic shell commands (`pytest`, `cargo test`, `cargo build`,
     `npm test`, `npm run build`, `pnpm test`, `go test`, `tsc`, `mvn`, `gradle`, `make`).
   - Uses AGY's `"overwrite"` contract (or Claude Code's `updatedInput`) to transparently wrap
     the command with `solpi run --cmd ...`, routing massive logs through ObservationPack &
     Evidence-Preserving Reducer automatically!
2. `PreInvocation` / `PostInvocation` (AGY) and `UserPromptSubmit` (Claude Code, Codex):
   - Checks the active transcript file size (`transcriptPath`) to estimate context token pressure.
   - When the SoL-Pi Online Context Compaction threshold is crossed at a turn boundary, injects
     an `ephemeralMessage` advising the agent to compact completed milestones via `solpi_milestone`
     or `/compact`.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .config import load_config

VERBOSE_CMD_REGEX = re.compile(
    r"^\s*(?:pytest|python3?\s+-m\s+unittest|python3?\s+-m\s+pytest|"
    r"cargo\s+(?:test|check|build|clippy)|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+test|run\s+build|run\s+lint|run\s+typecheck)|"
    r"go\s+test|tsc\b|mvn\s+(?:test|package|verify)|gradle\s+(?:test|build)|make\s+(?:test|check|all))\b"
)

# Any shell control/expansion syntax means the command is not a single simple command.
# Wrapping such commands would change their semantics (pipes, redirects, `a && b`) and,
# because the rewrite is auto-approved, could smuggle extra commands past permission rules.
SHELL_META_REGEX = re.compile(r"[;&|<>`$()\n\\]")


def should_wrap_command(cmd: str) -> bool:
    """Return True for a *simple* verbose test/build command not already wrapped by solpi."""
    if not cmd or SHELL_META_REGEX.search(cmd):
        return False
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens:
        return False
    first = Path(tokens[0]).name
    if first.startswith("solpi") or first == "env" or "=" in tokens[0]:
        return False
    return bool(VERBOSE_CMD_REGEX.search(cmd))


def build_wrapped_command(cmd: str, solpi_bin: str = "solpi") -> str:
    """`solpi_bin` is trusted shell text (may be several words, already quoted by the installer)."""
    return f"{solpi_bin} run --cmd {shlex.quote(cmd)}"


_REASON = "SoL-Pi Hook: routed verbose test/build command through ObservationPack."


def process_pre_tool_use(payload: Dict[str, Any], solpi_bin: str = "solpi") -> Dict[str, Any]:
    """Handle `PreToolUse`.

    Two wire formats are supported:
    - Antigravity (AGY): `{"toolCall": {"name", "args"}}` -> reply `{"decision", "overwrite"}`.
    - Claude Code / Codex: `{"tool_name", "tool_input"}` -> reply `hookSpecificOutput.updatedInput`.
    """
    if "toolCall" in payload:
        tool_call = payload.get("toolCall") or {}
        tool_name = tool_call.get("name", "")
        args = tool_call.get("args") or {}
        if tool_name in ("run_command", "Bash", "bash"):
            key = "CommandLine" if "CommandLine" in args else ("command" if "command" in args else None)
            if key and should_wrap_command(str(args.get(key, ""))):
                return {
                    "decision": "allow",
                    "reason": _REASON,
                    "overwrite": {key: build_wrapped_command(str(args[key]), solpi_bin)},
                }
        return {"decision": "allow"}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input")
    if tool_name == "Bash" and isinstance(tool_input, dict):
        original = tool_input.get("command")
        if isinstance(original, str) and should_wrap_command(original):
            # `updatedInput` replaces the whole input, so keep every other field (timeout, ...).
            updated = dict(tool_input)
            updated["command"] = build_wrapped_command(original, solpi_bin)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": _REASON,
                    "updatedInput": updated,
                }
            }
    return {}


def _pressure_message(payload: Dict[str, Any], config: Optional[Dict[str, Any]]) -> Optional[str]:
    cfg = (config or load_config()).get("online_compaction", {})
    if not cfg.get("enabled", True):
        return None
    transcript_path = payload.get("transcriptPath") or payload.get("transcript_path")
    if not transcript_path:
        return None
    tp = Path(transcript_path)
    try:
        if not tp.is_file():
            return None
        byte_size = tp.stat().st_size
    except OSError:
        return None

    # Rough heuristic: ~4 bytes of JSONL transcript per active token
    est_tokens = byte_size // 4
    window_limit = max(1, int(cfg.get("window_limit_tokens", 200000)))
    pressure = est_tokens / window_limit
    if pressure < float(cfg.get("pressure_ratio_trigger", 0.65)):
        return None
    return (
        f"[SoL-Pi Online Context Compaction Notice] Estimated context pressure is "
        f"{pressure*100:.1f}% (~{est_tokens:,}/{window_limit:,} tokens). "
        f"At the end of your current subtask milestone, call `solpi_milestone` (or `solpi milestone`) "
        f"and recommend `/compact` or delegate the next subtask to a clean subagent."
    )


def process_pre_invocation(payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """AGY `PreInvocation`/`PostInvocation`: inject an ephemeral compaction notice."""
    msg = _pressure_message(payload, config)
    if not msg:
        return {}
    return {"injectSteps": [{"ephemeralMessage": msg}], "terminationBehavior": ""}


def process_user_prompt_submit(payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Claude Code / Codex `UserPromptSubmit`: add the compaction notice as additional context."""
    msg = _pressure_message(payload, config)
    if not msg:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}


def dispatch_hook(event_type: str, payload: Dict[str, Any], solpi_bin: str = "solpi") -> Dict[str, Any]:
    if event_type == "PreToolUse":
        return process_pre_tool_use(payload, solpi_bin=solpi_bin)
    if event_type in ("PreInvocation", "PostInvocation"):
        return process_pre_invocation(payload)
    if event_type == "UserPromptSubmit":
        return process_user_prompt_submit(payload)
    return {}


def main() -> None:
    event_type = sys.argv[1] if len(sys.argv) > 1 else "PreToolUse"
    solpi_bin = sys.argv[2] if len(sys.argv) > 2 else "solpi"
    raw_in = sys.stdin.read().strip()
    try:
        payload = json.loads(raw_in) if raw_in else {}
        out = dispatch_hook(event_type, payload if isinstance(payload, dict) else {}, solpi_bin=solpi_bin)
    except Exception:  # fail-open: a hook bug must never block the agent
        out = {}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
