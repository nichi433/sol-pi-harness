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
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load_config

VERBOSE_CMD_REGEX = re.compile(
    r"^\s*(?:pytest|python3?\s+-m\s+unittest|python3?\s+-m\s+pytest|"
    r"cargo\s+(?:test|check|build|clippy)|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+test|run\s+build|run\s+lint|run\s+typecheck)|"
    r"go\s+test|tsc\b|mvn\s+(?:test|package|verify)|gradle\s+(?:test|build)|make\s+(?:test|check|all))\b"
)

# The hook AUTO-APPROVES the rewritten command (`permissionDecision: allow`), so a rewrite must never let
# anything run that the user's permission rules would not already have allowed for the original command.
# Hence: no expansion/redirection/background syntax, and only a tiny, benign set of prefixes.
UNSAFE_EXPANSION_REGEX = re.compile(r"[<>`$()\n\\]")
ENV_VAR_ASSIGN_REGEX = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|]*)$")

# Environment variables that are harmless for a test/build run. Everything else (LD_PRELOAD, PATH,
# BASH_ENV, NODE_OPTIONS, PYTEST_ADDOPTS, RUSTC_WRAPPER, GIT_*, ...) can execute arbitrary code or
# change what runs, so a command carrying them is left to the normal permission flow.
SAFE_ENV_VARS = frozenset({
    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONHASHSEED", "CI", "NODE_ENV",
    "FORCE_COLOR", "NO_COLOR", "TERM", "COLUMNS", "LANG", "LC_ALL", "TZ", "RUST_BACKTRACE", "RUST_LOG",
    "CARGO_TERM_COLOR", "DEBUG", "VERBOSE", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
})

HOOK_MODES = ("safe", "prefixed")


def _safe_env_assignment(token: str) -> bool:
    m = ENV_VAR_ASSIGN_REGEX.match(token)
    if not m or m.group(1) not in SAFE_ENV_VARS:
        return False
    value = m.group(2)
    if m.group(1) == "PYTHONPATH":  # only project-relative paths: never point imports somewhere else
        return not (value.startswith(("/", "~")) or ".." in value.split("/"))
    return True


def _tokenize(cmd: str) -> Optional[List[str]]:
    """Shell tokens with operators split out (`&&`, `;`, `|`, `&`, `>`...). None if it cannot be parsed."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None


def _is_operator(tok: str) -> bool:
    return bool(tok) and all(c in "();<>|&" for c in tok)


def _verbose_segment(tokens: List[str], allow_env: bool) -> bool:
    """`[VAR=val ...] <known build/test command ...>` that is not already wrapped by solpi."""
    idx = 0
    while idx < len(tokens) and ENV_VAR_ASSIGN_REGEX.match(tokens[idx]):
        if not allow_env or not _safe_env_assignment(tokens[idx]):
            return False
        idx += 1
    if idx >= len(tokens):
        return False
    first = Path(tokens[idx]).name
    if first.startswith("solpi") or first == "env":
        return False
    return bool(VERBOSE_CMD_REGEX.search(" ".join(tokens[idx:])))


def _safe_prefix(segment: List[str]) -> bool:
    """`cd <dir>` or `export SAFE_VAR=val ...`."""
    if len(segment) == 2 and segment[0] == "cd":
        return not _is_operator(segment[1])
    return len(segment) >= 2 and segment[0] == "export" and all(_safe_env_assignment(t) for t in segment[1:])


def should_wrap_command(cmd: str, *, mode: str = "safe") -> bool:
    """Decide whether `PreToolUse` should wrap `cmd` with `solpi run`.

    Modes (`observation_pack.hook_mode`):
    - `"safe"` (default): one simple build/test command, nothing else.
    - `"prefixed"`: additionally `cd <dir> && <cmd>`, `export SAFE=val && <cmd>` and `SAFE=val <cmd>`
      (chained with `&&` or `;`, at most 2 prefixes). Pipes, redirections, `&`, expansions and any env var
      outside `SAFE_ENV_VARS` (e.g. `LD_PRELOAD`, `PATH`) are still refused.

    There is intentionally no "wrap everything" mode: the rewrite is auto-approved, so wrapping arbitrary
    commands would bypass the user's permission rules, and `solpi run` has no TTY/stdin and merges stderr.
    Unknown values (including the removed `"all"`) fall back to `"prefixed"`/`"safe"` behaviour below.
    """
    if not cmd or not cmd.strip() or UNSAFE_EXPANSION_REGEX.search(cmd):
        return False
    tokens = _tokenize(cmd.strip())
    if not tokens or any(_is_operator(t) and t not in ("&&", ";") for t in tokens):
        return False
    if mode not in HOOK_MODES:
        mode = "prefixed" if mode == "all" else "safe"

    segments: List[List[str]] = [[]]
    for tok in tokens:
        if tok in ("&&", ";"):
            if not segments[-1]:
                return False  # `&& x`, `; ;`
            segments.append([])
        else:
            segments[-1].append(tok)
    if not segments[-1]:
        segments.pop()  # trailing `;`
    if not segments:
        return False

    if mode == "safe":
        return len(segments) == 1 and _verbose_segment(segments[0], allow_env=False)
    return (
        len(segments) <= 3
        and all(_safe_prefix(seg) for seg in segments[:-1])
        and _verbose_segment(segments[-1], allow_env=True)
    )


def build_wrapped_command(cmd: str, solpi_bin: str = "solpi", timeout_sec: Optional[int] = None) -> str:
    """`solpi_bin` is trusted shell text (may be several words, already quoted by the installer).

    `solpi run` kills the command after `--timeout` seconds (default 120), which is shorter than a long
    build the harness would have allowed, so the timeout is passed explicitly.
    """
    extra = f" --timeout {int(timeout_sec)}" if timeout_sec else ""
    return f"{solpi_bin} run{extra} --cmd {shlex.quote(cmd)}"


def _timeout_for(tool_input: Dict[str, Any]) -> int:
    """Harness Bash timeouts are milliseconds (Claude Code); stay above them so the harness decides."""
    raw = tool_input.get("timeout")
    secs = int(raw) // 1000 if isinstance(raw, (int, float)) and raw > 0 else 0
    return max(1800, secs + 60)


_REASON = "SoL-Pi Hook: routed verbose test/build command through ObservationPack."


def process_pre_tool_use(
    payload: Dict[str, Any],
    solpi_bin: str = "solpi",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Handle `PreToolUse`.

    Two wire formats are supported:
    - Antigravity (AGY): `{"toolCall": {"name", "args"}}` -> reply `{"decision", "overwrite"}`.
    - Claude Code / Codex: `{"tool_name", "tool_input"}` -> reply `hookSpecificOutput.updatedInput`.
    """
    cfg = config or load_config()
    # SOLPI_HOOK_MODE (set by `solpi install --hook-mode`) wins over sol-pi.json `observation_pack.hook_mode`
    hook_mode = os.environ.get("SOLPI_HOOK_MODE") or str(cfg.get("observation_pack", {}).get("hook_mode", "prefixed"))

    if "toolCall" in payload:
        tool_call = payload.get("toolCall") or {}
        tool_name = tool_call.get("name", "")
        args = tool_call.get("args") or {}
        if tool_name in ("run_command", "Bash", "bash"):
            key = "CommandLine" if "CommandLine" in args else ("command" if "command" in args else None)
            if key and should_wrap_command(str(args.get(key, "")), mode=hook_mode):
                return {
                    "decision": "allow",
                    "reason": _REASON,
                    "overwrite": {key: build_wrapped_command(str(args[key]), solpi_bin, 1800)},
                }
        return {"decision": "allow"}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input")
    if tool_name == "Bash" and isinstance(tool_input, dict):
        original = tool_input.get("command")
        if isinstance(original, str) and should_wrap_command(original, mode=hook_mode):
            # `updatedInput` replaces the whole input, so keep every other field (timeout, ...).
            updated = dict(tool_input)
            updated["command"] = build_wrapped_command(original, solpi_bin, _timeout_for(tool_input))
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
