"""A minimal agent loop on the Anthropic Messages API, with interchangeable tool-output policies ("arms").

This is the "no harness, just the API" setting. It exists to measure, on long tasks, what each way of keeping
tool output out of the context actually costs and saves.

Arms (all use prompt caching and an append-only history):
  N  naive      raw tool output (capped at 100 KB)
  T  trim       every tool output cut to head+tail (8 KB) - the cheap trick agents already use
  S  solpi      sol-pi receipts: build/test output -> verified receipt, any other result >= 10 KiB -> handle +
                head/tail excerpt, all applied when the result is first produced (append-only safe) + `solpi_read_obs`
  C  ctx-edit   raw output + the API's own server-side context editing (`clear_tool_uses_20250919`)

Why S replaces results immediately instead of "after 2 requests" like the paper's ObservationPack: rewriting an
earlier message is a history edit. On current models (Sonnet 5.5 / Opus 5.5 ...) thinking blocks are bound to the
exact prefix they were produced under, so a client-side rewrite either 400s (new accounts) or drops the reasoning
(`drop_block`), and it also invalidates the prompt cache from the edit onward. Server-side context editing is the
sanctioned way to clear old results (it does not count as an edit) - that is arm C.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from solpi.observation_pack import ObservationPackStore  # noqa: E402

# $/MTok: (input, output, cache read, 5-minute cache write). From the Claude API model/pricing tables.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
}
CONTEXT_EDIT_BETA = "context-management-2025-06-27"
RAW_CAP = 100_000
TRIM_CAP = 8_000
SYSTEM = ("You are a software engineer working in the current directory, using the provided tools. "
          "Solve the task you are given and stop when it is done.")

TOOLS: List[Dict[str, Any]] = [
    {"name": "bash", "description": "Run a shell command in the project directory.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a file with line numbers. Optional 1-based start_line/end_line.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"},
                                                       "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "edit_file", "description": "Replace one exact, unique occurrence of `old` with `new` in a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                       "new": {"type": "string"}}, "required": ["path", "old", "new"]}},
]
READ_OBS_TOOL = {
    "name": "solpi_read_obs",
    "description": "Read an archived output by handle: a page, a regex grep, or lines around a line number.",
    "input_schema": {"type": "object", "properties": {
        "obs_id": {"type": "string"}, "page": {"type": "integer"}, "grep_pattern": {"type": "string"},
        "around_line": {"type": "integer"}, "radius": {"type": "integer"}}, "required": ["obs_id"]},
}


@dataclass
class Arm:
    name: str
    solpi: bool = False
    trim: bool = False
    context_editing: bool = False
    # context editing thresholds (see the Context Editing docs): clear once the prompt passes `trigger_tokens`,
    # keep the most recent `keep_tool_uses` tool uses, and only clear if at least `clear_at_least` tokens go.
    trigger_tokens: int = 30_000
    keep_tool_uses: int = 3
    clear_at_least: int = 10_000


ARMS: Dict[str, Arm] = {
    "N": Arm("N"),
    "T": Arm("T", trim=True),
    "S": Arm("S", solpi=True),
    "C": Arm("C", context_editing=True),
}


@dataclass
class Result:
    arm: str
    turns: int = 0
    stop: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    tool_calls: Dict[str, int] = field(default_factory=dict)
    tool_result_bytes: int = 0
    cleared_tokens: int = 0
    recalls: int = 0
    cost_usd: float = 0.0
    wall_s: float = 0.0
    error: str = ""


def cost_of(model: str, r: Result) -> float:
    pin, pout, pread, pwrite = PRICES[model]
    return (r.input_tokens * pin + r.output_tokens * pout + r.cache_read * pread + r.cache_write * pwrite) / 1e6


def head_tail(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    return f"{text[:half]}\n[... {len(text) - cap} characters truncated ...]\n{text[-half:]}"


class Sandbox:
    """Tools confined (for file access) to one working directory."""

    def __init__(self, workdir: Path) -> None:
        self.dir = workdir.resolve()

    def _path(self, rel: str) -> Path:
        p = (self.dir / rel).resolve()
        if self.dir not in p.parents and p != self.dir:
            raise ValueError(f"path outside the project: {rel}")
        return p

    def bash(self, command: str, timeout: int = 120) -> tuple[str, int]:
        try:
            p = subprocess.run(command, shell=True, cwd=self.dir, capture_output=True, text=True, timeout=timeout)
            return (p.stdout or "") + (("\n" + p.stderr) if p.stderr else ""), p.returncode
        except subprocess.TimeoutExpired:
            return f"[timeout after {timeout}s]", 124

    def read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        lines = self._path(path).read_text(errors="replace").splitlines()
        lo, hi = (start_line or 1), (end_line or len(lines))
        return "\n".join(f"{i}\t{lines[i - 1]}" for i in range(max(1, lo), min(len(lines), hi) + 1))

    def edit_file(self, path: str, old: str, new: str) -> str:
        p = self._path(path)
        text = p.read_text()
        if old not in text:
            return "error: `old` not found"
        if text.count(old) > 1:
            return "error: `old` is not unique"
        p.write_text(text.replace(old, new, 1))
        return "ok"


def run_agent(client: Any, model: str, prompt: str, workdir: Path, arm: Arm, *, max_turns: int = 60,
              max_cost: float = 3.0, max_tokens: int = 8000) -> Result:
    res = Result(arm=arm.name)
    sb = Sandbox(workdir)
    store = ObservationPackStore(store_dir=Path(tempfile.mkdtemp(prefix="solpi_api_"))) if arm.solpi else None
    tools = TOOLS + ([READ_OBS_TOOL] if arm.solpi else [])
    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    t0 = time.time()

    def shape(tool: str, text: str, command: str, rc: int) -> str:
        if arm.solpi and store is not None:
            return store.pack(text, exit_code=rc, command=command).formatted_output
        if arm.trim:
            return head_tail(text, TRIM_CAP)
        return head_tail(text, RAW_CAP)

    for _ in range(max_turns):
        kwargs: Dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=SYSTEM, tools=tools, messages=messages,
                                      cache_control={"type": "ephemeral"}, output_config={"effort": "medium"})
        if arm.context_editing:
            edit = {"type": "clear_tool_uses_20250919", "trigger": {"type": "input_tokens", "value": arm.trigger_tokens},
                    "keep": {"type": "tool_uses", "value": arm.keep_tool_uses},
                    "clear_at_least": {"type": "input_tokens", "value": arm.clear_at_least}}
            resp = client.beta.messages.create(betas=[CONTEXT_EDIT_BETA], context_management={"edits": [edit]}, **kwargs)
        else:
            resp = client.messages.create(**kwargs)
        res.turns += 1
        u = resp.usage
        res.input_tokens += getattr(u, "input_tokens", 0) or 0
        res.output_tokens += getattr(u, "output_tokens", 0) or 0
        res.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        res.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        cm = getattr(resp, "context_management", None)
        for e in (getattr(cm, "applied_edits", None) or []):
            res.cleared_tokens += getattr(e, "cleared_input_tokens", 0) or 0
        res.cost_usd = cost_of(model, res)
        messages.append({"role": "assistant", "content": resp.content})  # append-only, thinking blocks untouched
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            res.stop = resp.stop_reason or "end"
            break
        results = []
        for b in uses:
            res.tool_calls[b.name] = res.tool_calls.get(b.name, 0) + 1
            inp = b.input or {}
            try:
                if b.name == "bash":
                    out, rc = sb.bash(inp["command"])
                    text = shape("bash", out, inp["command"], rc)
                elif b.name == "read_file":
                    text = shape("read_file", sb.read_file(inp["path"], inp.get("start_line"), inp.get("end_line")),
                                 f"read_file {inp['path']}", 0)
                elif b.name == "edit_file":
                    text = sb.edit_file(inp["path"], inp["old"], inp["new"])
                elif b.name == "solpi_read_obs" and store is not None:
                    res.recalls += 1
                    text = head_tail(store.read_obs(inp["obs_id"], page=inp.get("page", 1), grep_pattern=inp.get("grep_pattern"),
                                                    around_line=inp.get("around_line"), radius=inp.get("radius", 20)), TRIM_CAP)
                else:
                    text = f"error: unknown tool {b.name}"
            except Exception as exc:  # tool errors go back to the model, they do not stop the run
                text = f"error: {exc}"
            res.tool_result_bytes += len(text.encode("utf-8"))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text})
        messages.append({"role": "user", "content": results})  # all results of one turn in ONE message
        if res.cost_usd >= max_cost:
            res.stop = "budget"
            break
    else:
        res.stop = "max_turns"
    res.wall_s = round(time.time() - t0, 1)
    return res
