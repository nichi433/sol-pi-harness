#!/usr/bin/env python3
"""A/B: the same Claude Code agent on 10 tasks, with and without the sol-pi-harness hook-only install.

    python3 benchmarks/ab/run_ab.py --out ab_results --reps 1            # spends real API money
    python3 benchmarks/ab/run_ab.py --out ab_results --report-only        # just re-summarise

Condition A: plain `claude -p` (no hooks, no MCP).   Condition B: identical + `solpi install --hook-only`.
Both: same model, same prompt, `--setting-sources project,local` (your user settings/hooks are NOT loaded),
`--strict-mcp-config`, skills disabled, fresh copy of the task. Success = the task's verify command passes
after protected files (tests, Makefile) are restored. Cost/turns/tokens come from Claude Code's own result
event. Runs are interleaved in a seeded random order to spread drift (cache warmth, load).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))
from make_tasks import generate  # noqa: E402
from solpi.installer import install_for_target  # noqa: E402

RECEIPT_MARKERS = ("SoL-Pi ObservationPack", "SoL-Pi Evidence-Preserving Receipt")
SYSTEM_NOTE = "Work only inside the current working directory."


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def parse_stream(stdout: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"tools": {}, "bash_cmds": [], "receipts": 0, "recalls": 0, "bash_result_bytes": 0,
                           "bash_results": 0, "result": None}
    names: Dict[str, str] = {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if t == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n = b.get("name", "?")
                    names[b.get("id", "")] = n
                    out["tools"][n] = out["tools"].get(n, 0) + 1
                    if n == "Bash":
                        out["bash_cmds"].append(str((b.get("input") or {}).get("command", ""))[:200])
                    if "solpi_read_obs" in n:
                        out["recalls"] += 1
        elif t == "user":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    txt = _text(b.get("content"))
                    if names.get(b.get("tool_use_id")) == "Bash":
                        out["bash_results"] += 1
                        out["bash_result_bytes"] += len(txt.encode("utf-8"))
                        if any(m in txt for m in RECEIPT_MARKERS):
                            out["receipts"] += 1
        elif t == "result":
            out["result"] = ev
    return out


def run_one(task_dir: Path, meta: Dict[str, Any], cond: str, rep: int, args: argparse.Namespace, out_dir: Path) -> Dict[str, Any]:
    run_dir = out_dir / "runs" / f"{meta['id']}__{cond}__{rep}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    work = run_dir / "work"
    shutil.copytree(task_dir / "work", work)
    if cond == "B":
        acts = install_for_target("claude", workspace_dir=work, hook_only=True)
        assert not [a for a in acts if a.startswith("[ERROR]")], acts
        mcp_cfg = work / ".mcp.json"
        allowed = ["Bash", "Edit", "Write", "Read", "Glob", "Grep", "mcp__sol-pi__solpi_read_obs"]
    else:
        mcp_cfg = out_dir / "empty_mcp.json"
        allowed = ["Bash", "Edit", "Write", "Read", "Glob", "Grep"]
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", args.model,
           "--permission-mode", "acceptEdits", "--allowedTools", *allowed,
           "--setting-sources", "project,local", "--strict-mcp-config", "--mcp-config", str(mcp_cfg),
           "--disable-slash-commands", "--no-session-persistence", "--max-budget-usd", str(args.budget_per_run),
           "--append-system-prompt", SYSTEM_NOTE]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=meta["prompt"], cwd=work, capture_output=True, text=True, timeout=args.timeout)
        stdout, rc, err = p.stdout, p.returncode, p.stderr[-300:]
    except subprocess.TimeoutExpired as e:
        stdout, rc, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), 124, "timeout"
    wall = time.time() - t0
    (run_dir / "stream.jsonl").write_text(stdout)
    parsed = parse_stream(stdout)
    res = parsed["result"] or {}
    # restore protected files, then verify with the task's own command
    for rel in meta["protected"]:
        dst, src = work / rel, task_dir / "protected" / rel
        if dst.is_dir():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    v = subprocess.run(meta["verify"], shell=True, cwd=work, capture_output=True, text=True, timeout=300)
    usage = res.get("usage") or {}
    return {
        "task": meta["id"], "group": meta["group"], "cond": cond, "rep": rep, "success": v.returncode == 0,
        "cost_usd": float(res.get("total_cost_usd") or 0.0), "turns": res.get("num_turns"),
        "is_error": bool(res.get("is_error")) or rc not in (0,), "rc": rc, "stderr": err, "wall_s": round(wall, 1),
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0), "cache_write": usage.get("cache_creation_input_tokens", 0),
        "bash_results": parsed["bash_results"], "bash_result_bytes": parsed["bash_result_bytes"],
        "receipts": parsed["receipts"], "recalls": parsed["recalls"], "tools": parsed["tools"],
        "bash_cmds": parsed["bash_cmds"],
    }


def summarize(rows: List[Dict[str, Any]]) -> str:
    by: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for r in rows:
        by.setdefault(r["task"], {}).setdefault(r["cond"], []).append(r)
    lines = ["| task | grp | cost A | cost B | Δcost | turns A→B | ctx tok A→B (k) | bash KB A→B | receipts | recalls | ok A/B |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    pairs = {"noisy": [], "control": []}
    tot = {"A": 0.0, "B": 0.0}
    for task in sorted(by):
        a, b = by[task].get("A", []), by[task].get("B", [])
        if not a or not b:
            continue
        m = lambda rs, k: statistics.mean(x[k] or 0 for x in rs)  # noqa: E731
        ca, cb = m(a, "cost_usd"), m(b, "cost_usd")
        ctx = lambda rs: statistics.mean((x["cache_read"] + x["cache_write"] + x["input_tokens"]) for x in rs) / 1000  # noqa: E731
        grp = a[0]["group"]
        pairs[grp].append(cb / ca if ca else float("nan"))
        tot["A"] += ca; tot["B"] += cb
        lines.append(f"| {task} | {grp} | ${ca:.3f} | ${cb:.3f} | {100 * (cb / ca - 1):+.0f}% | {m(a, 'turns'):.1f}→{m(b, 'turns'):.1f} | "
                     f"{ctx(a):.0f}→{ctx(b):.0f} | {m(a, 'bash_result_bytes') / 1000:.1f}→{m(b, 'bash_result_bytes') / 1000:.1f} | "
                     f"{m(b, 'receipts'):.1f} | {m(b, 'recalls'):.1f} | {sum(x['success'] for x in a)}/{len(a)} {sum(x['success'] for x in b)}/{len(b)} |")
    out = ["\n".join(lines), ""]
    for grp, ratios in pairs.items():
        ratios = [r for r in ratios if r == r]
        if ratios:
            gm = math.exp(statistics.mean(math.log(r) for r in ratios))
            out.append(f"- **{grp}** ({len(ratios)} tasks): geometric-mean cost ratio B/A = **{gm:.3f}** "
                       f"({100 * (gm - 1):+.1f}%), per-task B/A: {', '.join(f'{r:.2f}' for r in ratios)}")
    if tot["A"]:
        out.append(f"- **total cost** A=${tot['A']:.2f} B=${tot['B']:.2f} ({100 * (tot['B'] / tot['A'] - 1):+.1f}%)")
    sa = sum(r["success"] for r in rows if r["cond"] == "A"); na = sum(1 for r in rows if r["cond"] == "A")
    sb = sum(r["success"] for r in rows if r["cond"] == "B"); nb = sum(1 for r in rows if r["cond"] == "B")
    out.append(f"- **success** A {sa}/{na}, B {sb}/{nb}; errored runs: {sum(r['is_error'] for r in rows)}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--budget-per-run", type=float, default=1.5)
    ap.add_argument("--total-budget", type=float, default=25.0)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tasks", nargs="*", help="subset of task ids")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results_file = out_dir / "results.jsonl"
    rows: List[Dict[str, Any]] = [json.loads(l) for l in results_file.read_text().splitlines()] if results_file.exists() else []

    if not args.report_only:
        (out_dir / "empty_mcp.json").write_text('{"mcpServers": {}}')
        tasks_root = out_dir / "tasks"
        metas = {m["id"]: m for m in generate(tasks_root)}
        ids = [i for i in sorted(metas) if not args.tasks or i in args.tasks]
        done = {(r["task"], r["cond"], r["rep"]) for r in rows}
        jobs = [(i, c, rep) for i in ids for c in ("A", "B") for rep in range(args.reps) if (i, c, rep) not in done]
        random.Random(args.seed).shuffle(jobs)
        spent = [sum(r["cost_usd"] for r in rows)]
        lock = threading.Lock()
        print(f"{len(jobs)} runs, model={args.model}, parallel={args.parallel}, budget cap ${args.total_budget}", flush=True)

        def work(job):
            tid, cond, rep = job
            with lock:
                if spent[0] >= args.total_budget:
                    return None
            r = run_one(tasks_root / tid, metas[tid], cond, rep, args, out_dir)
            with lock:
                spent[0] += r["cost_usd"]
                rows.append(r)
                with results_file.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(f"[{len(rows)}] {tid} {cond}#{rep}: ${r['cost_usd']:.3f} turns={r['turns']} ok={r['success']} "
                      f"receipts={r['receipts']} recalls={r['recalls']} (spent ${spent[0]:.2f})", flush=True)
            return r

        with cf.ThreadPoolExecutor(max_workers=args.parallel) as ex:
            list(ex.map(work, jobs))
    report = summarize(rows)
    (out_dir / "report.md").write_text(report + "\n")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
