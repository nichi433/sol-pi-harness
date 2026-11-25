#!/usr/bin/env python3
"""Would sol-pi-harness have paid off in YOUR Claude Code sessions?  (`python benchmarks/analyze_sessions.py`)

Reads Claude Code session transcripts (JSONL, default ~/.claude/projects) LOCALLY and prints aggregates only
(no commands, paths or outputs are printed or stored). For every Bash tool result it estimates what a
receipt would have saved, using the fact that a tool result stays in the context - and is re-read from the
prompt cache - on every later request:

    saved cache-read tokens  =  result_tokens x reduction x later_requests
    net                      =  saved  -  (MCP schema tokens x total requests)      # fixed cost of having the tools

Scenarios:
  hook_only   results whose command the hook rewrites today (a *simple* build/test command, no `&&`/pipes)
  reducible   every build/test-looking command (what `solpi_run` / a looser hook could cover)
  all_big     every result >= 10 KiB regardless of command (upper bound; ignores that the agent may need
              extra `solpi_read_obs` calls and that file reads/searches are only head/tail-packed)

Caveats: token counts are chars/4; `later_requests` ignores auto-compaction (an upper bound); it does NOT
model the extra turns an agent may spend recalling archived output, nor any change in task quality.
The paper's own result is a ~3-6 % score drop for the full stack - this script cannot measure that.

Transcript format is the one Claude Code writes today (assistant `tool_use` + user `tool_result` blocks,
`message.usage.cache_read_input_tokens`); unknown lines are skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from solpi.hook_runner import should_wrap_command  # noqa: E402
from solpi.reducer import is_reducible_command  # noqa: E402

BIG_REDUCIBLE = 4096
BIG_ANY = 10240


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def iter_session(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def analyze_session(path: Path) -> Dict[str, Any]:
    """Return per-session facts: request count, cache-read tokens, and Bash results with their position."""
    commands: Dict[str, str] = {}
    requests = 0
    cache_read = 0
    results: List[Dict[str, Any]] = []
    seen_msg = set()
    for obj in iter_session(path):
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        if obj.get("type") == "assistant":
            mid = msg.get("id")
            usage = msg.get("usage") or {}
            if mid is None or mid not in seen_msg:  # one API response may be split over several lines
                requests += 1
                cache_read += int(usage.get("cache_read_input_tokens") or 0)
                if mid is not None:
                    seen_msg.add(mid)
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                    commands[b.get("id", "")] = str((b.get("input") or {}).get("command", ""))
        elif obj.get("type") == "user":
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in commands:
                    cmd = commands[b["tool_use_id"]]
                    results.append({"chars": len(_result_text(b.get("content"))), "cmd": cmd, "at_request": requests})
    return {"requests": requests, "cache_read": cache_read, "results": results}


def analyze(paths: List[Path], reduction: float, price_per_mtok: float, schema_tokens: int) -> Dict[str, Any]:
    tot_requests = tot_cache = n_bash = 0
    big = {"hook_only": [0, 0.0], "reducible": [0, 0.0], "all_big": [0, 0.0]}  # [count, saved tokens]
    big_bytes = bash_bytes = 0
    for p in paths:
        s = analyze_session(p)
        tot_requests += s["requests"]
        tot_cache += s["cache_read"]
        for r in s["results"]:
            n_bash += 1
            bash_bytes += r["chars"]
            later = max(0, s["requests"] - r["at_request"])
            tokens = r["chars"] / 4
            if r["chars"] >= BIG_ANY:
                big_bytes += r["chars"]
                big["all_big"][0] += 1
                big["all_big"][1] += tokens * reduction * later
            if r["chars"] >= BIG_REDUCIBLE and is_reducible_command(r["cmd"]):
                big["reducible"][0] += 1
                big["reducible"][1] += tokens * reduction * later
                if should_wrap_command(r["cmd"]):
                    big["hook_only"][0] += 1
                    big["hook_only"][1] += tokens * reduction * later
    overhead_tokens = schema_tokens * tot_requests
    out: Dict[str, Any] = {
        "sessions": len(paths), "requests": tot_requests, "bash_results": n_bash,
        "bash_result_mb": round(bash_bytes / 1e6, 2), "total_cache_read_mtok": round(tot_cache / 1e6, 1),
        "fixed_overhead_mtok": round(overhead_tokens / 1e6, 2), "scenarios": {},
    }
    for name, (cnt, saved) in big.items():
        net = saved - overhead_tokens
        out["scenarios"][name] = {
            "results": cnt,
            "saved_mtok": round(saved / 1e6, 2),
            "pct_of_cache_read": round(100 * saved / tot_cache, 1) if tot_cache else 0.0,
            "net_mtok_after_overhead": round(net / 1e6, 2),
            "net_usd_at_price": round(net / 1e6 * price_per_mtok, 2),
            "pays_off": net > 0,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--reduction", type=float, default=0.90, help="share of a big result a receipt removes (benchmark: ~0.9)")
    ap.add_argument("--cache-read-price", type=float, default=0.30, help="USD per million cached-read tokens")
    ap.add_argument("--schema-tokens", type=int, default=735, help="MCP tool-schema tokens added to every request")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.path).expanduser()
    paths = sorted(root.rglob("*.jsonl")) if root.is_dir() else [root]
    if not paths:
        print(f"No .jsonl transcripts under {root}", file=sys.stderr)
        return 1
    res = analyze(paths, a.reduction, a.cache_read_price, a.schema_tokens)
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    print(f"{res['sessions']} sessions | {res['requests']} requests | {res['bash_results']} Bash results "
          f"({res['bash_result_mb']} MB) | cache-read {res['total_cache_read_mtok']} Mtok | "
          f"tool-schema overhead {res['fixed_overhead_mtok']} Mtok")
    for name, m in res["scenarios"].items():
        print(f"  {name:10} results={m['results']:5}  saved={m['saved_mtok']:8} Mtok ({m['pct_of_cache_read']:5}% of cache-read)"
              f"  net={m['net_mtok_after_overhead']:8} Mtok = ${m['net_usd_at_price']:8}  {'PAYS OFF' if m['pays_off'] else 'does not pay off'}")
    print("\nUpper-bound estimate: see the caveats at the top of this file (no extra recall turns, no quality change).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
