"""Universal Zero-Dependency MCP Stdio Server (`solpi-mcp`).

Speaks the Model Context Protocol (MCP) over standard input/output (JSON-RPC 2.0)
without requiring any third-party pip libraries. Works out-of-the-box in:
- Antigravity (AGY)
- Claude Code
- OpenAI Codex CLI
- Gemini CLI
- Cursor / Windsurf

Exposed Tools:
1. `solpi_fuse`: Mechanism 1 (Action Fusion: Edit file + run validation command in 1 turn).
2. `solpi_run`: Mechanism 2 & 3 (Run command with ObservationPack CAS & Evidence-Preserving Reducer).
3. `solpi_read_obs`: Mechanism 2 (Paged recall, line neighborhood, or regex grep over `obs_<id>`).
4. `solpi_milestone`: Mechanism 4 (Online Context Compaction economic evaluator & state receipt).
5. `solpi_telemetry`: Session token/byte savings and fail-open statistics.
6. `solpi_tune`: Mechanism 5 (Auto-Research RSI loop to calibrate thresholds).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__
from .action_fusion import fuse_edit_and_verify
from .auto_research import run_auto_research_loop
from .compactor import OnlineContextCompactor
from .config import load_config
from .observation_pack import ObservationPackStore

TOOLS_SCHEMA = [
    {
        "name": "solpi_fuse",
        "description": (
            "Edit a file (exact substring replace) AND run its verification command in ONE call. "
            "Syntax-checks first; output is packed like solpi_run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File to edit."},
                "target": {"type": "string", "description": "Exact substring to replace."},
                "replacement": {"type": "string", "description": "Replacement text."},
                "verify_cmd": {"type": "string", "description": "Command that verifies the edit."},
                "cwd": {"type": "string", "description": "Working directory."},
                "revert_on_test_failure": {
                    "type": "boolean",
                    "description": "Restore the file if verify_cmd fails.",
                },
            },
            "required": ["file_path", "target", "replacement", "verify_cmd"],
        },
    },
    {
        "name": "solpi_run",
        "description": (
            "Run a build/test/lint command. Large output (>4 KB) is archived behind an `obs_<id>` handle and "
            "returned as a receipt of verbatim error lines + head/tail; recall the rest with solpi_read_obs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to run."},
                "cwd": {"type": "string", "description": "Working directory."},
                "timeout_sec": {"type": "integer", "description": "Timeout seconds (120)."},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "solpi_read_obs",
        "description": (
            "Read an archived output by handle: a page, a regex grep, or lines around a line number."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "obs_id": {"type": "string", "description": "Handle, e.g. obs_8f3a1b2c9d."},
                "page": {"type": "integer", "description": "1-based page."},
                "grep_pattern": {"type": "string", "description": "Regex filter."},
                "around_line": {"type": "integer", "description": "Show lines around this line number."},
                "radius": {"type": "integer", "description": "Context lines (20)."},
            },
            "required": ["obs_id"],
        },
    },
    {
        "name": "solpi_milestone",
        "description": (
            "At a finished plan step: record it and get a cost-based advice on whether to /compact now "
            "or keep the prompt cache warm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "completed_step": {"type": "string", "description": "Completed step."},
                "current_tokens_est": {
                    "type": "integer",
                    "description": "Current context tokens (estimate).",
                },
                "expected_remaining_turns": {
                    "type": "integer",
                    "description": "Remaining requests; omit to estimate from unfinished_steps.",
                },
                "unfinished_steps": {"type": "integer", "description": "Unfinished plan steps."},
                "requests_since_last_milestone": {
                    "type": "integer",
                    "description": "Requests since last milestone.",
                },
                "files_modified": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files changed.",
                },
                "obs_handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Handles produced.",
                },
                "key_findings": {"type": "string", "description": "Verified outcome summary."},
                "next_step": {"type": "string", "description": "Next step."},
            },
            "required": ["completed_step", "current_tokens_est"],
        },
    },
    {
        "name": "solpi_telemetry",
        "description": "Returns cumulative token/byte savings, fused round-trips avoided, and Fail-Open statistics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reset": {"type": "boolean", "description": "If true, resets telemetry counters after reading."}
            },
        },
    },
    {
        "name": "solpi_tune",
        "description": (
            "SoL-Pi / Harness2 Auto-Research RSI Loop: Evaluates candidate harness compression parameters "
            "against a diagnostic corpus using a Capability Floor and Efficiency Gate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability_floor": {
                    "type": "number",
                    "description": "Minimum required diagnostic evidence recall score (0.0 to 1.0, default 0.95).",
                }
            },
        },
    },
]


# Every listed tool costs context tokens on EVERY request. The developer tools stay callable but are only
# advertised when SOLPI_MCP_TOOLS=all (default "core": fuse, run, read_obs, milestone).
CORE_TOOLS = {"solpi_fuse", "solpi_run", "solpi_read_obs", "solpi_milestone"}


def listed_tools() -> list:
    if os.environ.get("SOLPI_MCP_TOOLS", "core").lower() == "all":
        return TOOLS_SCHEMA
    return [t for t in TOOLS_SCHEMA if t["name"] in CORE_TOOLS]


def execute_mcp_tool(name: str, arguments: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatch an MCP tool call and return an MCP tool result object."""
    cfg = config or load_config()
    store = ObservationPackStore(config=cfg)

    try:
        if name == "solpi_fuse":
            res = fuse_edit_and_verify(
                file_path=arguments["file_path"],
                verify_cmd=arguments["verify_cmd"],
                target=arguments.get("target"),
                replacement=arguments.get("replacement"),
                cwd=arguments.get("cwd"),
                revert_on_test_failure=bool(arguments.get("revert_on_test_failure", False)),
                config=cfg,
                store=store,
            )
            return {
                "content": [{"type": "text", "text": res.combined_report}],
                "isError": not res.edit_applied,
            }

        elif name == "solpi_run":
            cmd = arguments["cmd"]
            cwd = arguments.get("cwd") or str(Path.cwd())
            timeout = int(arguments.get("timeout_sec", 120))
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
                exit_code = proc.returncode
                raw_out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            except subprocess.TimeoutExpired as exc:
                exit_code = 124
                raw_out = f"[Timeout after {timeout}s]\n{exc.stdout or ''}\n{exc.stderr or ''}"

            packed = store.pack(raw_out.strip(), exit_code=exit_code, command=cmd)
            return {
                "content": [{"type": "text", "text": packed.formatted_output}],
                "isError": False,
            }

        elif name == "solpi_read_obs":
            text = store.read_obs(
                obs_id=arguments["obs_id"],
                page=int(arguments.get("page", 1)),
                grep_pattern=arguments.get("grep_pattern"),
                around_line=arguments.get("around_line"),
                radius=int(arguments.get("radius", 20)),
            )
            return {"content": [{"type": "text", "text": text}], "isError": False}

        elif name == "solpi_milestone":
            compactor = OnlineContextCompactor(config=cfg, store_dir=store.store_dir)
            decision = compactor.evaluate_milestone(
                completed_step=arguments["completed_step"],
                current_tokens_est=int(arguments["current_tokens_est"]),
                expected_remaining_turns=arguments.get("expected_remaining_turns"),
                unfinished_steps=arguments.get("unfinished_steps"),
                requests_since_last_milestone=arguments.get("requests_since_last_milestone"),
                files_modified=arguments.get("files_modified"),
                obs_handles=arguments.get("obs_handles"),
                key_findings=arguments.get("key_findings"),
                next_step=arguments.get("next_step"),
            )
            return {"content": [{"type": "text", "text": decision.checkpoint_markdown}], "isError": False}

        elif name == "solpi_telemetry":
            summary = store.telemetry.summary()
            if arguments.get("reset"):
                store.telemetry.reset()
            return {"content": [{"type": "text", "text": json.dumps(summary, indent=2)}], "isError": False}

        elif name == "solpi_tune":
            floor = float(arguments.get("capability_floor", 0.95))
            report = run_auto_research_loop(capability_floor=floor, base_config=cfg)
            return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}], "isError": False}

        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            }
    except Exception as exc:
        return {
            "content": [{"type": "text", "text": f"[SoL-Pi MCP Error in {name}]: {exc}"}],
            "isError": True,
        }


def handle_jsonrpc_message(msg: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Process a single JSON-RPC 2.0 request or notification."""
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications have no `id` and expect no response
    if req_id is None:
        return None

    if method == "initialize":
        client_proto = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "sol-pi-harness", "version": __version__},
            },
        }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": listed_tools()}}

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        result = execute_mcp_tool(tool_name, tool_args, config=config)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    elif method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}

    elif method == "prompts/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def main() -> None:
    """Run the MCP Stdio server loop."""
    cfg = load_config()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_jsonrpc_message(msg, config=cfg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
