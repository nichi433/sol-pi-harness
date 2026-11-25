#!/usr/bin/env python3
"""Would sol-pi-harness have paid off in YOUR Claude Code sessions?  (`python benchmarks/analyze_sessions.py`)

Reads Claude Code session transcripts (JSONL, default ~/.claude/projects) LOCALLY and prints aggregates only
(no commands, paths or outputs are printed or stored). For every Bash tool result it estimates what a
receipt would have saved, using the fact that a tool result stays in the context - and is re-read from the
prompt cache - on every later request:

    saved cache-read tokens  =  result_tokens x reduction x later_requests
    net                      =  saved  -  (MCP schema tokens x total requests)      # fixed cost of having the tools
                                 (~735 tokens full install, ~143 with `solpi install --hook-only`)

Scenarios:
  hook_only      Bash results the hook rewrites in `safe` mode (one simple build/test command)
  hook_prefixed  Bash results the hook rewrites in `prefixed` mode (also `cd <dir> && cmd`, harmless `VAR=val`)
  reducible      every build/test-looking Bash command >= 4 KiB (what the paper's Reducer targets)
  all_big_bash   every Bash result >= 10 KiB, whatever the command  (what a generic ObservationPack could archive)
  all_big_any    every tool result >= 10 KiB: Bash, Read, Grep, WebFetch, MCP...  (broadest upper bound; the
                 `all_big_any_by_tool` line says which tools it comes from)

Caveats: token counts are chars/4. `later_requests` stops at the next detected compaction (a sharp drop in
cache-read between two requests; Claude Code also clears old tool results itself) - both figures are printed so
you can see the sensitivity. It does NOT model the extra turns an agent may spend recalling archived output,
nor any change in task quality. Sub-agent (`isSidechain`) entries are skipped.
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

LEAN_SCHEMA_TOKENS = 143  # `solpi install --hook-only`: only solpi_read_obs is advertised
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


COMPACTION_DROP = 0.6  # cache_read falling below 60 % of the previous request = history was rewritten
MIN_CTX_FOR_DROP = 20_000


def compaction_boundaries(cache_reads: List[int]) -> List[int]:
    """Request indices where the context shrank sharply (compaction / clearing of old tool results).

    Format-agnostic: uses only `cache_read_input_tokens`. A result inserted before a boundary is not re-read
    after it, so counting its savings past the boundary would overstate them.
    """
    out = []
    for i in range(1, len(cache_reads)):
        if cache_reads[i - 1] >= MIN_CTX_FOR_DROP and cache_reads[i] < COMPACTION_DROP * cache_reads[i - 1]:
            out.append(i)
    return out


def analyze_session(path: Path) -> Dict[str, Any]:
    """Per-session facts: request count, cache-read tokens, compaction boundaries and ALL tool results.

    Sub-agent (`isSidechain`) entries are skipped: they run in their own, separate contexts.
    """
    tool_of: Dict[str, str] = {}
    commands: Dict[str, str] = {}
    cache_reads: List[int] = []
    results: List[Dict[str, Any]] = []
    sidechain_requests = 0
    seen_msg = set()
    for obj in iter_session(path):
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        side = bool(obj.get("isSidechain"))
        if obj.get("type") == "assistant":
            mid = msg.get("id")
            if mid is None or mid not in seen_msg:  # one API response may be split over several lines
                if side:
                    sidechain_requests += 1
                else:
                    cache_reads.append(int((msg.get("usage") or {}).get("cache_read_input_tokens") or 0))
                if mid is not None:
                    seen_msg.add(mid)
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and not side:
                    tool_of[b.get("id", "")] = str(b.get("name", "?"))
                    if b.get("name") == "Bash":
                        commands[b.get("id", "")] = str((b.get("input") or {}).get("command", ""))
        elif obj.get("type") == "user" and not side:
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in tool_of:
                    tid = b["tool_use_id"]
                    results.append({"chars": len(_result_text(b.get("content"))), "tool": tool_of[tid],
                                    "cmd": commands.get(tid, ""), "at_request": len(cache_reads)})
    bounds = compaction_boundaries(cache_reads)
    n = len(cache_reads)
    for r in results:
        nxt = next((i for i in bounds if i >= r["at_request"]), n)
        r["later_naive"] = max(0, n - r["at_request"])
        r["later"] = max(0, nxt - r["at_request"])
    return {"requests": n, "cache_read": sum(cache_reads), "results": results,
            "compactions": len(bounds), "sidechain_requests": sidechain_requests}


def analyze(paths: List[Path], reduction: float, price_per_mtok: float, schema_tokens: int,
            lean_schema_tokens: int = LEAN_SCHEMA_TOKENS) -> Dict[str, Any]:
    tot_requests = tot_cache = n_results = n_bash = compactions = sidechain = 0
    result_bytes = 0
    names = ("hook_only", "hook_prefixed", "reducible", "all_big_bash", "all_big_any")
    big = {n: [0, 0.0, 0.0] for n in names}  # [count, saved tokens (compaction-aware), saved tokens (naive)]
    by_tool: Dict[str, List[float]] = {}
    for p in paths:
        s = analyze_session(p)
        tot_requests += s["requests"]
        tot_cache += s["cache_read"]
        compactions += s["compactions"]
        sidechain += s["sidechain_requests"]
        for r in s["results"]:
            n_results += 1
            result_bytes += r["chars"]
            tokens = r["chars"] / 4
            sv, sv_naive = tokens * reduction * r["later"], tokens * reduction * r["later_naive"]

            def add(name: str) -> None:
                big[name][0] += 1
                big[name][1] += sv
                big[name][2] += sv_naive

            if r["tool"] == "Bash":
                n_bash += 1
                if r["chars"] >= BIG_REDUCIBLE and is_reducible_command(r["cmd"]):
                    add("reducible")
                    if should_wrap_command(r["cmd"], mode="safe"):
                        add("hook_only")
                    if should_wrap_command(r["cmd"], mode="prefixed"):
                        add("hook_prefixed")
                if r["chars"] >= BIG_ANY:
                    add("all_big_bash")
            if r["chars"] >= BIG_ANY:
                add("all_big_any")
                t = by_tool.setdefault(r["tool"], [0, 0.0])
                t[0] += 1
                t[1] += sv
    overhead_tokens = schema_tokens * tot_requests
    lean_overhead = lean_schema_tokens * tot_requests
    out: Dict[str, Any] = {
        "sessions": len(paths), "requests": tot_requests, "tool_results": n_results, "bash_results": n_bash,
        "tool_result_mb": round(result_bytes / 1e6, 2), "total_cache_read_mtok": round(tot_cache / 1e6, 1),
        "compaction_boundaries_detected": compactions, "sidechain_requests_skipped": sidechain,
        "fixed_overhead_mtok": round(overhead_tokens / 1e6, 2),
        "lean_overhead_mtok": round(lean_overhead / 1e6, 2), "scenarios": {},
        "all_big_any_by_tool": {k: {"results": int(v[0]), "saved_mtok": round(v[1] / 1e6, 2),
                                    "pct_of_cache_read": round(100 * v[1] / tot_cache, 2) if tot_cache else 0.0}
                                for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1][1])},
    }
    for name, (cnt, saved, saved_naive) in big.items():
        net = saved - overhead_tokens
        out["scenarios"][name] = {
            "results": cnt,
            "saved_mtok": round(saved / 1e6, 2),
            "pct_of_cache_read": round(100 * saved / tot_cache, 2) if tot_cache else 0.0,
            "pct_of_cache_read_if_no_compaction": round(100 * saved_naive / tot_cache, 2) if tot_cache else 0.0,
            "net_mtok_after_overhead": round(net / 1e6, 2),
            "net_usd_at_price": round(net / 1e6 * price_per_mtok, 2),
            "pays_off": net > 0,
            # `solpi install --hook-only` advertises just solpi_read_obs (smaller fixed cost per request)
            "net_mtok_hook_only_install": round((saved - lean_overhead) / 1e6, 2),
            "net_usd_hook_only_install": round((saved - lean_overhead) / 1e6 * price_per_mtok, 2),
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
    print(f"{res['sessions']} sessions | {res['requests']} requests | {res['tool_results']} tool results "
          f"({res['tool_result_mb']} MB, {res['bash_results']} Bash) | cache-read {res['total_cache_read_mtok']} Mtok | "
          f"{res['compaction_boundaries_detected']} compactions detected | tool-schema overhead "
          f"{res['fixed_overhead_mtok']} Mtok full / {res['lean_overhead_mtok']} Mtok hook-only")
    for name, m in res["scenarios"].items():
        print(f"  {name:13} results={m['results']:5}  saved={m['saved_mtok']:8} Mtok = {m['pct_of_cache_read']:5}% of cache-read "
              f"({m['pct_of_cache_read_if_no_compaction']}% if no compaction)  net(hook-only)=${m['net_usd_hook_only_install']:8}")
    if res["all_big_any_by_tool"]:
        print("  all_big_any by tool: " + ", ".join(f"{k} {v['results']} results/{v['pct_of_cache_read']}%"
                                                 for k, v in res["all_big_any_by_tool"].items()))
    print("\nUpper-bound estimate: see the caveats at the top of this file (no extra recall turns, no quality change).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
