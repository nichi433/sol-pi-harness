#!/usr/bin/env python3
"""Raw-API A/B on long tasks: naive vs trimmed vs sol-pi receipts vs server-side context editing.

    export ANTHROPIC_API_KEY=...            # YOU run this; it spends your API credit
    python3 benchmarks/api_loop/run_ab.py --out api_ab --reps 1 --total-budget 15

Needs the official SDK (`pip install anthropic`). Prints per-task and overall tables (cost, turns, tokens, success)
and writes results.jsonl / report.md. Arms: N naive, T trim (head+tail 8 KB), S sol-pi (receipts + handles +
solpi_read_obs), C context editing (clear_tool_uses_20250919). See benchmarks/api_loop/agent.py for why S is
append-only. A run is *successful* only if the task's verify command passes after the tests are restored.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import json
import random
import shutil
import statistics
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from agent import ARMS, PRICES, run_agent  # noqa: E402
from tasks import generate  # noqa: E402


def one_run(client: Any, model: str, task_dir: Path, meta: Dict[str, Any], arm_name: str, rep: int, args: argparse.Namespace,
            out_dir: Path) -> Dict[str, Any]:
    run_dir = out_dir / "runs" / f"{meta['id']}__{arm_name}__{rep}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    work = run_dir / "work"
    shutil.copytree(task_dir / "work", work)
    try:
        res = run_agent(client, model, meta["prompt"], work, ARMS[arm_name], max_turns=args.max_turns,
                        max_cost=args.max_cost_per_run)
        err = ""
    except Exception as exc:  # API errors etc.: record, do not abort the whole experiment
        from agent import Result
        res, err = Result(arm=arm_name, error=repr(exc)[:300]), repr(exc)[:300]
    for rel in meta["protected"]:
        dst, src = work / rel, task_dir / "protected" / rel
        if dst.is_dir():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    ok = subprocess.run(meta["verify"], shell=True, cwd=work, capture_output=True, text=True, timeout=300).returncode == 0
    row = dataclasses.asdict(res)
    row.update(task=meta["id"], rep=rep, success=ok, model=model, error=row.get("error") or err)
    return row


def summarize(rows: List[Dict[str, Any]]) -> str:
    by: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for r in rows:
        by.setdefault(r["task"], {}).setdefault(r["arm"], []).append(r)
    m = lambda rs, k: statistics.mean(x[k] for x in rs)  # noqa: E731
    out = ["| task | arm | n | ok | cost $ | vs N | turns | cache-read Mtok | tool-result KB | recalls | cleared ktok |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    totals: Dict[str, float] = {}
    for task in sorted(by):
        base = m(by[task]["N"], "cost_usd") if "N" in by[task] else None
        for arm in ("N", "T", "S", "C"):
            rs = by[task].get(arm)
            if not rs:
                continue
            c = m(rs, "cost_usd")
            totals[arm] = totals.get(arm, 0.0) + c
            rel = f"{100 * (c / base - 1):+.0f}%" if base else "-"
            out.append(f"| {task} | {arm} | {len(rs)} | {sum(x['success'] for x in rs)}/{len(rs)} | {c:.3f} | {rel} | "
                       f"{m(rs, 'turns'):.1f} | {m(rs, 'cache_read') / 1e6:.2f} | {m(rs, 'tool_result_bytes') / 1000:.0f} | "
                       f"{m(rs, 'recalls'):.1f} | {m(rs, 'cleared_tokens') / 1000:.0f} |")
    out.append("")
    if "N" in totals:
        out.append("**Total mean cost per arm** (sum over tasks): " + ", ".join(
            f"{a} ${v:.2f}" + (f" ({100 * (v / totals['N'] - 1):+.0f}% vs N)" if a != "N" else "") for a, v in sorted(totals.items())))
    fails = [r for r in rows if r.get("error")]
    if fails:
        out.append(f"\n{len(fails)} runs errored (first: {fails[0]['error']})")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="claude-sonnet-5-5", choices=sorted(PRICES))
    ap.add_argument("--arms", nargs="*", default=["N", "T", "S", "C"], choices=sorted(ARMS))
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--max-cost-per-run", type=float, default=3.0)
    ap.add_argument("--total-budget", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rf = out_dir / "results.jsonl"
    rows: List[Dict[str, Any]] = [json.loads(l) for l in rf.read_text().splitlines()] if rf.exists() else []
    if not args.report_only:
        try:
            import anthropic
        except ImportError:
            print("pip install anthropic   (official SDK) and export ANTHROPIC_API_KEY", file=sys.stderr)
            return 2
        client = anthropic.Anthropic()
        metas = {m["id"]: m for m in generate(out_dir / "tasks")}
        ids = [i for i in sorted(metas) if not args.tasks or i in args.tasks]
        done = {(r["task"], r["arm"], r["rep"]) for r in rows}
        jobs = [(t, a, k) for t in ids for a in args.arms for k in range(args.reps) if (t, a, k) not in done]
        random.Random(args.seed).shuffle(jobs)
        spent, lock = [sum(r["cost_usd"] for r in rows)], threading.Lock()
        print(f"{len(jobs)} runs, model={args.model}, budget cap ${args.total_budget}", flush=True)

        def work(job):
            t, a, k = job
            with lock:
                if spent[0] >= args.total_budget:
                    return
            r = one_run(client, args.model, out_dir / "tasks" / t, metas[t], a, k, args, out_dir)
            with lock:
                spent[0] += r["cost_usd"]
                rows.append(r)
                with rf.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(f"{t} {a}#{k}: ${r['cost_usd']:.3f} turns={r['turns']} ok={r['success']} stop={r['stop']} "
                      f"(spent ${spent[0]:.2f})", flush=True)

        with cf.ThreadPoolExecutor(max_workers=args.parallel) as ex:
            list(ex.map(work, jobs))
    report = summarize(rows) if rows else "no results"
    (out_dir / "report.md").write_text(report + "\n")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
