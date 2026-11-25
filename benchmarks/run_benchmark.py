#!/usr/bin/env python3
"""Reproducible SoL-Pi benchmark (`python benchmarks/run_benchmark.py`).

Independent of the corpus used by `solpi tune`. Every log has planted ground-truth diagnostic lines.
Per log it reports raw/transmitted bytes, estimated tokens (bytes/4), reduction %, and

  direct      planted lines visible verbatim in the receipt the agent sees
  recoverable planted lines retrievable through the handle (`read_obs` grep) - the "no evidence lost" claim
  tail40      what a naive `tail -n 40` would have kept (baseline)

It also checks Action Fusion (syntax gate, revert, 2->1 round trips), dedup, hook classification,
secret redaction (receipt AND stored log), and the compaction cost gate.

Options:  --config PATH   use a sol-pi.json (e.g. the output of `solpi tune --write-config`)
          --json          print raw JSON instead of tables
          --check         exit 1 if a regression guard fails (recall, secrets, fail-open, reduction)
Token counts are a bytes/4 estimate, NOT provider tokens; this measures tool-output traffic only,
not end-to-end session cost.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--config")
ap.add_argument("--json", action="store_true")
ap.add_argument("--check", action="store_true")
ARGS = ap.parse_args()

_TMP = tempfile.TemporaryDirectory(prefix="solpi_bench_")
ROOT = Path(_TMP.name)
STORE = ROOT / "store"
os.environ["SOLPI_STORE_DIR"] = str(STORE)
if ARGS.config:
    os.environ["SOLPI_CONFIG"] = str(Path(ARGS.config).resolve())

from solpi.action_fusion import fuse_edit_and_verify  # noqa: E402
from solpi.compactor import OnlineContextCompactor  # noqa: E402
from solpi.config import load_config  # noqa: E402
from solpi.hook_runner import should_wrap_command  # noqa: E402
from solpi.observation_pack import ObservationPackStore  # noqa: E402

random.seed(7)
cfg = load_config()
store = ObservationPackStore(config=cfg)
tok = lambda s: len(s.encode()) // 4


def gcc_log():
    lines, truth = [], []
    for i in range(1, 2600):
        lines.append(f"[{i%100:3d}%] Building CXX object src/mod{i%97}/file{i}.cpp.o")
    for n, (f, l, c, m) in enumerate([("src/core/alloc.cpp", 412, 17, "error: 'size_t' was not declared in this scope"),
                                       ("src/net/socket.cpp", 88, 5, "error: no matching function for call to 'bind(int&, sockaddr*)'"),
                                       ("include/util/ring.hpp", 203, 9, "error: expected ';' before '}' token"),
                                       ("src/gfx/mesh.cpp", 1290, 33, "fatal error: glm/glm.hpp: No such file or directory")]):
        t = f"{f}:{l}:{c}: {m}"
        lines.insert(300 + n * 600, t); truth.append(t)
    lines.append("make: *** [Makefile:77: all] Error 2")
    truth.append("make: *** [Makefile:77: all] Error 2")
    return "gcc/make build, 4 errors", "make", "\n".join(lines), 2, truth


def pytest_log():
    lines = [f"tests/unit/test_mod{i}.py::test_case_{j} PASSED" for i in range(1, 60) for j in range(1, 12)]
    truth = []
    blocks = [
        ("src/billing/invoice.py", 142, "AssertionError: assert 1099 == 1100"),
        ("src/auth/session.py", 57, "KeyError: 'refresh_token'"),
    ]
    for k, (f, l, e) in enumerate(blocks):
        blk = ["=" * 20 + f" FAILURE test_{k} " + "=" * 20, "Traceback (most recent call last):",
               f'  File "{f}", line {l}, in handler', "    return compute(x)", e]
        pos = 200 + k * 250
        lines[pos:pos] = blk
        truth += [f'File "{f}", line {l}, in handler', e]
    lines.append("=== 2 failed, 646 passed in 41.20s ===")
    truth.append("=== 2 failed, 646 passed in 41.20s ===")
    return "pytest -v, 2 failures", "pytest -v", "\n".join(lines), 1, truth


def node_log():
    lines = [f"  ✓ renders component {i} correctly (12 ms)" for i in range(1800)]
    err = ["  ● Checkout › applies coupon", "    TypeError: Cannot read properties of undefined (reading 'total')",
           "      at applyCoupon (src/cart/coupon.ts:73:21)", "      at Object.<anonymous> (src/cart/coupon.test.ts:19:5)"]
    lines[900:900] = err
    lines.append("Tests:       1 failed, 1800 passed, 1801 total")
    truth = [err[1].strip(), "at applyCoupon (src/cart/coupon.ts:73:21)", "Tests:       1 failed, 1800 passed, 1801 total"]
    return "jest, 1 failure", "npm test", "\n".join(lines), 1, truth


def cargo_log():
    lines = [f"   Compiling crate-{i} v0.{i}.0" for i in range(900)]
    err = ["error[E0382]: borrow of moved value: `buf`", "  --> src/parser/lexer.rs:231:18",
           "error: could not compile `parser` (lib) due to 1 previous error"]
    lines[700:700] = err
    return "cargo build, 1 error", "cargo build", "\n".join(lines), 101, [err[0], err[1].strip(), err[2]]


def secrets_log():
    lines = [f"INFO request {i} handled in {random.randint(1,90)}ms" for i in range(1500)]
    secret1 = "AKIAIOSFODNN7EXAMPLE"
    secret2 = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    lines.insert(500, f"DEBUG aws_access_key_id={secret1}")
    lines.insert(900, f"DEBUG Authorization: Bearer {secret2}")
    err = "ERROR payments/gateway.py:310 ConnectionResetError: upstream closed"
    lines.insert(1200, err)
    return "test log w/ secrets", "pytest -q", "\n".join(lines), 1, [err], [secret1, secret2]


def real_unittest_failing():
    """REAL run: this repo's test-suite with 3 injected failures, verbose."""
    tmp = Path(tempfile.mkdtemp(dir=ROOT))
    t = tmp / "test_injected.py"
    body = ["import unittest", "class T(unittest.TestCase):"]
    for i in range(300):
        body.append(f"    def test_ok_{i}(self): self.assertEqual({i}, {i})")
    for i in range(3):
        body.append(f"    def test_bad_{i}(self): self.assertEqual({i}, {i+1}, 'injected mismatch {i}')")
    t.write_text("\n".join(body) + "\n")
    p = subprocess.run([sys.executable, "-m", "unittest", "-v", str(t)], capture_output=True, text=True, cwd=tmp)
    raw = (p.stdout or "") + "\n" + (p.stderr or "")
    truth = [f"AssertionError: {i} != {i+1} : injected mismatch {i}" for i in range(3)] + ["FAILED (failures=3)"]
    return "REAL unittest -v (3 injected failures)", "python3 -m unittest -v", raw, p.returncode, truth


def real_grep():
    p = subprocess.run(["grep", "-rn", "def ", str(REPO_SRC)], capture_output=True, text=True)
    return "REAL grep -rn 'def ' (search: bypasses reducer)", "grep -rn 'def ' src", p.stdout, 0, \
        [l for l in p.stdout.splitlines() if "def verify_receipt" in l]


def small_log():
    return "small output (below threshold)", "pytest -q", "PASSED 12 tests\nOK", 0, ["PASSED 12 tests"]


def big_cat():
    """40 KiB file read: not a build/test log -> archived with handle + head/tail only (no reducer)."""
    lines = [f"row {i}: lorem ipsum dolor sit amet consectetur {i * 7919 % 1000}" for i in range(900)]
    needle = "row 450: NEEDLE-VALUE-4242 ledger mismatch"
    lines[450] = needle
    return "file read (40 KiB, bypasses reducer)", "cat data/ledger.txt", "\n".join(lines), 0, [needle]


# (case fn, expect needle visible directly in the receipt?)
CASES = [(gcc_log, True), (pytest_log, True), (node_log, True), (cargo_log, True), (secrets_log, True),
         (real_unittest_failing, True), (real_grep, False), (small_log, True), (big_cat, False)]

rows, secret_leaks = [], 0
t_overhead = []
for fn, expect_direct in CASES:
    res = fn()
    name, cmd, raw, code, truth = res[:5]
    secrets = res[5] if len(res) > 5 else []
    t0 = time.perf_counter()
    packed = store.pack(raw, exit_code=code, command=cmd)
    dt = (time.perf_counter() - t0) * 1000
    t_overhead.append(dt)
    seen = packed.formatted_output
    direct = sum(1 for t in truth if t in seen)
    # second hop: agent greps the handle for the lines the receipt did not quote
    recov = direct
    if packed.obs_id:
        for t in truth:
            if t in seen:
                continue
            key = re.escape(t[:40])
            out = store.read_obs(packed.obs_id, grep_pattern=key)
            if t in out:
                recov += 1
    else:
        recov = sum(1 for t in truth if t in raw)
    naive_tail = "\n".join(raw.splitlines()[-40:])
    naive = sum(1 for t in truth if t in naive_tail)
    leak = [s for s in secrets if s in seen]
    stored = (STORE / f"{packed.obs_id}.log").read_text() if packed.obs_id and (STORE / f"{packed.obs_id}.log").exists() else ""
    leak_store = [s for s in secrets if s in stored]
    secret_leaks += len(leak) + len(leak_store)
    rows.append(dict(case=name, raw_B=packed.raw_bytes, tx_B=packed.transmitted_bytes,
                     raw_tok=tok(raw), tx_tok=tok(seen),
                     reduction=round(100 * (1 - tok(seen) / max(1, tok(raw))), 1),
                     truth=len(truth), expect_direct=expect_direct, direct=direct, recoverable=recov, naive_tail40=naive,
                     packed=packed.was_packed, reduced=packed.was_reduced, failed_open=packed.failed_open,
                     secret_leak_receipt=len(leak), secret_leak_store=len(leak_store), ms=round(dt, 1)))

# ---- dedup: same verbose output twice
_, _c, raw, code, _ = gcc_log()
r1 = store.pack(raw, exit_code=code, command="make"); r2 = store.pack(raw, exit_code=code, command="make"); r3 = store.pack(raw, exit_code=code, command="make")
dedup = dict(first_B=r1.transmitted_bytes, second_B=r2.transmitted_bytes, third_B=r3.transmitted_bytes, dedup_flag=r3.was_deduplicated)

# ---- hook classification
should = ["pytest -q", "python3 -m unittest discover -s tests", "cargo test --all", "npm test", "go test ./...",
          "tsc --noEmit", "make test", "pnpm run build"]
shouldnt = ["ls -la", "git status", "pytest | tail", "cd x && pytest", "pytest; rm -rf /", "cat Makefile",
            "pytest > out.txt", "echo $(pytest)", "solpi run --cmd pytest", "python3 app.py"]
hook = dict(wrapped_ok=sum(should_wrap_command(c) for c in should), n_should=len(should),
            wrongly_wrapped=sum(should_wrap_command(c) for c in shouldnt), n_shouldnt=len(shouldnt))

# ---- Action Fusion: 2-turn vs fused, on a real file + real test command
d = Path(tempfile.mkdtemp(dir=ROOT))
(d / "calc.py").write_text("def add(a, b):\n    return a - b\n")
(d / "test_calc.py").write_text("import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n")
# same-size edits within one second can reuse a stale .pyc -> disable bytecode for a deterministic check
cmd = f"PYTHONDONTWRITEBYTECODE=1 {sys.executable} -m unittest -q"
fused = fuse_edit_and_verify(d / "calc.py", cmd, target="a - b", replacement="a + b", cwd=d, config=cfg, store=store)
# syntax gate
(d / "calc.py").write_text("def add(a, b):\n    return a + b\n")
before = (d / "calc.py").read_text()
gate = fuse_edit_and_verify(d / "calc.py", cmd, target="a + b", replacement="a +* b", cwd=d, config=cfg, store=store)
gate_intact = (d / "calc.py").read_text() == before
# revert on failure
rev = fuse_edit_and_verify(d / "calc.py", cmd, target="a + b", replacement="a * b", cwd=d, revert_on_test_failure=True, config=cfg, store=store)
rev_restored = (d / "calc.py").read_text() == before
# missing target
miss = fuse_edit_and_verify(d / "calc.py", cmd, target="NOPE", replacement="x", cwd=d, config=cfg, store=store)
fusion = dict(fused_success=fused.success, fused_exit=fused.validation_exit_code,
              round_trips_two_step=2, round_trips_fused=1,
              syntax_gate_blocked=(not gate.edit_applied and gate.rolled_back), syntax_gate_file_intact=gate_intact,
              revert_on_fail_restored=(rev.rolled_back and rev_restored), missing_target_error=("not found" in miss.combined_report))

# ---- Compaction economics sweep
sweep = []
for tokens in (20_000, 60_000, 100_000, 140_000, 180_000):
    for turns in (1, 5, 20):
        # fresh state per cell: the gate is stateful (later compactions need a larger margin)
        comp = OnlineContextCompactor(config=cfg, store_dir=ROOT / f"comp_{tokens}_{turns}")
        dcs = comp.evaluate_milestone(f"m_{tokens}_{turns}", current_tokens_est=tokens, expected_remaining_turns=turns)
        sweep.append(dict(tokens=tokens, turns=turns, compact=dcs.should_compact, reason=dcs.trigger_reason,
                          net_usd=round(dcs.net_benefit_usd, 4), pressure=round(dcs.window_pressure_ratio, 2)))

report = dict(rows=rows, dedup=dedup, hook=hook, fusion=fusion, compaction=sweep,
              secret_leaks_total=secret_leaks, avg_pack_ms=round(sum(t_overhead) / len(t_overhead), 1))

# ---- regression guard: reducible logs only (direct recall is NOT expected for the file-read case)
big = [r for r in rows if r["raw_B"] > 4096 and r["expect_direct"]]
raw_t, tx_t = sum(r["raw_tok"] for r in big), sum(r["tx_tok"] for r in big)
summary = dict(
    reducible_logs=len(big), raw_tokens=raw_t, tx_tokens=tx_t, reduction_pct=round(100 * (1 - tx_t / raw_t), 1),
    direct=f"{sum(r['direct'] for r in big)}/{sum(r['truth'] for r in big)}",
    recoverable_all=f"{sum(r['recoverable'] for r in rows)}/{sum(r['truth'] for r in rows)}",
    tail40_baseline=f"{sum(r['naive_tail40'] for r in big)}/{sum(r['truth'] for r in big)}",
)
failures = []
if sum(r["recoverable"] for r in rows) != sum(r["truth"] for r in rows): failures.append("evidence not fully recoverable")
if sum(r["direct"] for r in big) < 0.95 * sum(r["truth"] for r in big): failures.append("direct recall < 95%")
if secret_leaks: failures.append("secret leaked")
if any(r["failed_open"] for r in rows): failures.append("unexpected fail-open")
if summary["reduction_pct"] < 80: failures.append("reduction < 80%")
if not (fusion["syntax_gate_blocked"] and fusion["syntax_gate_file_intact"] and fusion["revert_on_fail_restored"]): failures.append("action fusion safety")
if hook["wrapped_ok"] != hook["n_should"] or hook["wrongly_wrapped"]: failures.append("hook classification")
report["summary"], report["failures"] = summary, failures

if ARGS.json:
    print(json.dumps(report, indent=1))
else:
    print(f"{'case':50} {'rawB':>7} {'txB':>6} {'red%':>5} {'truth':>5} {'direct':>6} {'recov':>5} {'tail40':>6} {'ms':>6}")
    for r in rows:
        print(f"{r['case'][:50]:50} {r['raw_B']:7} {r['tx_B']:6} {r['reduction']:5} {r['truth']:5} {r['direct']:6} {r['recoverable']:5} {r['naive_tail40']:6} {r['ms']:6}")
    print("\nSUMMARY", json.dumps(summary))
    print("DEDUP  ", json.dumps(dedup))
    print("HOOK   ", json.dumps(hook))
    print("FUSION ", json.dumps(fusion))
    print("SECRETS leaked:", secret_leaks, "| avg pack latency ms:", report["avg_pack_ms"])
    print("COMPACTION (tokens, remaining turns -> compact?, net USD)")
    for c in sweep:
        print(f"  {c['tokens']:>7} {c['turns']:>3} -> {str(c['compact']):5} {c['net_usd']:>8}  p={c['pressure']}")
    print("\nRESULT:", "OK" if not failures else "FAIL: " + "; ".join(failures))
sys.exit(1 if (ARGS.check and failures) else 0)
