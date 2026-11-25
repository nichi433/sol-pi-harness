"""Diverse evaluation corpus for `solpi tune` (anti-overfitting).

Every case carries ground-truth `truth` lines: the diagnostic lines an agent needs to act. They are known
by construction (synthetic generators) or derived from a *real* tool run by a rule that does not use the
reducer's own regexes (e.g. every `: error:` line of gcc).

Families are split into TRAIN (used to select a configuration) and HOLDOUT (never used for selection; only
to validate that the winner generalizes). Real tool logs (gcc, g++, cargo, python, node) are HOLDOUT-only and
are produced on demand when the tools exist (`include_real=True`).
"""

from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

Case = Dict[str, Any]

TRAIN_FAMILIES = [
    "pytest_verbose", "unittest_verbose", "gcc_warning_noise", "tsc_errors", "cargo_build",
    "cargo_test", "jest", "lint_many", "passing_run", "python_traceback_chain",
]
HOLDOUT_FAMILIES = ["go_test", "maven", "npm_install", "linker", "silent_fail", "docker_pip"]
VARIANTS_PER_FAMILY = 4


def _place(rng: random.Random, noise: List[str], blocks: List[List[str]]) -> List[str]:
    """Insert each block at a random position of the NOISE (start / middle / end / anywhere).

    Positions refer to the original noise so a block is never spliced into another block.
    """
    n = len(noise)
    spots = []
    for block in blocks:
        where = rng.choice(["start", "middle", "end", "any", "any"])
        pos = {"start": rng.randint(0, min(3, n)), "middle": max(0, min(n, n // 2 + rng.randint(-5, 5))),
               "end": n, "any": rng.randint(0, n)}[where]
        spots.append((pos, block))
    lines = list(noise)
    for pos, block in sorted(spots, key=lambda x: -x[0]):
        lines[pos:pos] = block
    return lines


def _noise_count(rng: random.Random) -> int:
    return rng.choice([40, 120, 400, 900, 1600])


# ----------------------------------------------------------------------------- TRAIN families


def f_pytest_verbose(rng: random.Random, v: int) -> Case:
    n_fail = rng.randint(1, 6)
    noise = [f"tests/unit/test_mod{i}.py::test_case_{j} PASSED [{(i * 7) % 100:3d}%]"
             for i in range(1, 1 + _noise_count(rng) // 8) for j in range(1, 9)]
    blocks, truth, shorts = [], [], []
    for k in range(n_fail):
        f, ln, a, b = f"src/pkg/mod{k}.py", rng.randint(5, 400), rng.randint(1, 99), rng.randint(100, 199)
        blocks.append([f"{'_' * 18} test_case_fail_{k} {'_' * 18}", f"    def test_case_fail_{k}():",
                       f">       assert compute({a}) == {b}", f"E       assert {a * 2} == {b}", f"{f}:{ln}: AssertionError"])
        truth += [f"E       assert {a * 2} == {b}", f"{f}:{ln}: AssertionError"]
        shorts.append(f"FAILED tests/unit/test_fail_{k}.py::test_case_fail_{k} - assert {a * 2} == {b}")
    summary = f"===== {n_fail} failed, {len(noise)} passed in {rng.randint(2, 90)}.{rng.randint(10, 99)}s ====="
    lines = _place(rng, noise, blocks) + ["=== short test summary info ==="] + shorts + [summary]
    return dict(cmd="pytest -v", exit_code=1, log="\n".join(lines), truth=truth + shorts + [summary])


def f_unittest_verbose(rng: random.Random, v: int) -> Case:
    n_fail = rng.randint(1, 5)
    noise = [f"test_ok_{i} (tests.test_mod.Case.test_ok_{i}) ... ok" for i in range(_noise_count(rng))]
    blocks, truth = [], []
    for k in range(n_fail):
        ln, a = rng.randint(10, 300), rng.randint(1, 50)
        blocks += [["=" * 70, f"FAIL: test_bad_{k} (tests.test_mod.Case.test_bad_{k})", "-" * 70,
                    "Traceback (most recent call last):", f'  File "/app/tests/test_mod.py", line {ln}, in test_bad_{k}',
                    f"    self.assertEqual(value({a}), {a + 1})", f"AssertionError: {a} != {a + 1}"]]
        truth += [f'File "/app/tests/test_mod.py", line {ln}, in test_bad_{k}', f"AssertionError: {a} != {a + 1}"]
    tail = ["-" * 70, f"Ran {len(noise) + n_fail} tests in 1.203s", "", f"FAILED (failures={n_fail})"]
    return dict(cmd="python3 -m unittest -v", exit_code=1, log="\n".join(_place(rng, noise, blocks) + tail),
                truth=truth + [f"FAILED (failures={n_fail})"])


def f_gcc_warning_noise(rng: random.Random, v: int) -> Case:
    """Hundreds of file:line WARNINGS (noise) bury a few real errors: stresses location extraction."""
    noise = []
    for i in range(_noise_count(rng) // 2):
        noise += [f"[{i % 100:3d}%] Building C object src/m{i % 90}/f{i}.c.o",
                  f"src/m{i % 90}/f{i}.c:{i % 300 + 1}:{i % 40 + 1}: warning: unused variable 'tmp{i}' [-Wunused-variable]"]
    blocks, truth = [], []
    for k in range(rng.randint(1, 4)):
        e = f"src/core/e{k}.c:{rng.randint(5, 900)}:{rng.randint(1, 60)}: error: '{rng.choice(['size_t', 'foo_t', 'buf'])}' undeclared (first use in this function)"
        blocks.append([e]); truth.append(e)
    end = "make[2]: *** [CMakeFiles/app.dir/build.make:77: all] Error 1"
    return dict(cmd="make", exit_code=2, log="\n".join(_place(rng, noise, blocks) + [end]), truth=truth + [end])


def f_tsc_errors(rng: random.Random, v: int) -> Case:
    noise = [f"info  - Compiling module src/components/Widget{i}.tsx..." for i in range(_noise_count(rng) // 2)]
    codes = [("TS2345", "Argument of type 'string | null' is not assignable to parameter of type 'string'."),
             ("TS2322", "Type 'undefined' is not assignable to type 'UserSession'."),
             ("TS7006", "Parameter 'x' implicitly has an 'any' type."), ("TS2339", "Property 'id' does not exist on type '{}'.")]
    blocks, truth = [], []
    for k in range(rng.randint(1, 6)):
        c, m = rng.choice(codes)
        e = f"src/mod{k}/file{k}.ts:{rng.randint(1, 500)}:{rng.randint(1, 80)} - error {c}: {m}"
        blocks.append([e]); truth.append(e)
    end = f"Found {len(truth)} errors."
    return dict(cmd="tsc --noEmit", exit_code=2, log="\n".join(_place(rng, noise, blocks) + [end]), truth=truth + [end])


def f_cargo_build(rng: random.Random, v: int) -> Case:
    noise = [f"   Compiling crate_dep_{i} v0.4.{i}" for i in range(_noise_count(rng) // 2)]
    blocks, truth = [], []
    for k in range(rng.randint(1, 4)):
        e1 = f"error[E0382]: borrow of moved value: `buf{k}`"
        e2 = f"  --> src/parser/lexer{k}.rs:{rng.randint(10, 600)}:{rng.randint(5, 40)}"
        blocks.append([e1, e2, "   |", f"{rng.randint(10, 600)} |     let a = buf{k};", "   |         ^^^^ value moved here"])
        truth += [e1, e2.strip()]
    end = f"error: could not compile `parser` (lib) due to {len(truth) // 2} previous errors"
    return dict(cmd="cargo build", exit_code=101, log="\n".join(_place(rng, noise, blocks) + [end]), truth=truth + [end])


def f_cargo_test(rng: random.Random, v: int) -> Case:
    n = _noise_count(rng) // 2
    noise = [f"test parser::tests::case_{i} ... ok" for i in range(n)]
    blocks, truth = [], []
    for k in range(rng.randint(1, 4)):
        p = f"thread 'parser::tests::bad_{k}' panicked at src/parser/mod.rs:{rng.randint(10, 800)}:9:"
        blocks.append([f"---- parser::tests::bad_{k} stdout ----", p, "assertion `left == right` failed", "  left: 1", " right: 2"])
        truth.append(p)
    end = f"test result: FAILED. {n} passed; {len(truth)} failed; 0 ignored"
    return dict(cmd="cargo test", exit_code=101, log="\n".join(_place(rng, noise, blocks) + [end]), truth=truth + [end])


def f_jest(rng: random.Random, v: int) -> Case:
    n = _noise_count(rng)
    noise = [f"  ✓ renders component {i} correctly ({i % 30} ms)" for i in range(n)]
    blocks, truth = [], []
    for k in range(rng.randint(1, 4)):
        ln = rng.randint(5, 300)
        e = f"    TypeError: Cannot read properties of undefined (reading 'total{k}')"
        a = f"      at applyCoupon{k} (src/cart/coupon{k}.ts:{ln}:21)"
        blocks.append([f"  ● Checkout › applies coupon {k}", "", e, "", a])
        truth += [e.strip(), a.strip()]
    end = f"Tests:       {len(truth) // 2} failed, {n} passed, {n + len(truth) // 2} total"
    return dict(cmd="npm test", exit_code=1, log="\n".join(_place(rng, noise, blocks) + [end]), truth=truth + [end])


def f_lint_many(rng: random.Random, v: int) -> Case:
    """Many findings: stresses `max_verbatim_quotes`."""
    n = rng.choice([8, 15, 25, 35])
    kinds = ["Incompatible types in assignment", "Argument 1 has incompatible type", "Name 'x' is not defined",
             "Missing return statement"]
    findings = [f"src/app/m{i % 11}.py:{rng.randint(1, 700)}: error: {rng.choice(kinds)}  [assignment]" for i in range(n)]
    noise = [f"Checking src/app/module_{i}.py" for i in range(_noise_count(rng) // 4)]
    end = f"Found {n} errors in {min(n, 11)} files (checked {len(noise)} source files)"
    return dict(cmd="mypy src", exit_code=1, log="\n".join(_place(rng, noise, [[f] for f in findings]) + [end]),
                truth=findings + [end])


def f_passing_run(rng: random.Random, v: int) -> Case:
    n = _noise_count(rng)
    lines = [f"test_ok_{i} (tests.test_mod.Case.test_ok_{i}) ... ok" for i in range(n)]
    end = f"Ran {n} tests in 3.1s\n\nOK"
    return dict(cmd="python3 -m unittest -v", exit_code=0, log="\n".join(lines + [end]), truth=[f"Ran {n} tests in 3.1s", "OK"])


def f_python_traceback_chain(rng: random.Random, v: int) -> Case:
    noise = [f"INFO worker-{i % 8} processed batch {i} in {rng.randint(1, 90)}ms" for i in range(_noise_count(rng))]
    ln1, ln2 = rng.randint(10, 200), rng.randint(10, 200)
    block = ["Traceback (most recent call last):", f'  File "/srv/app/db.py", line {ln1}, in connect',
             "    sock.connect(addr)", "ConnectionRefusedError: [Errno 111] Connection refused", "",
             "The above exception was the direct cause of the following exception:", "",
             "Traceback (most recent call last):", f'  File "/srv/app/main.py", line {ln2}, in <module>',
             "    run()", "RuntimeError: database unavailable"]
    truth = [f'File "/srv/app/db.py", line {ln1}, in connect', "ConnectionRefusedError: [Errno 111] Connection refused",
             f'File "/srv/app/main.py", line {ln2}, in <module>', "RuntimeError: database unavailable"]
    return dict(cmd="pytest -q tests/integration", exit_code=1, log="\n".join(_place(rng, noise, [block])), truth=truth)


# ----------------------------------------------------------------------------- HOLDOUT families


def f_go_test(rng: random.Random, v: int) -> Case:
    n = _noise_count(rng) // 2
    noise = [x for i in range(n) for x in (f"=== RUN   TestCase{i}", f"--- PASS: TestCase{i} (0.00s)")]
    blocks, truth = [], []
    for k in range(rng.randint(1, 4)):
        a, b = rng.randint(1, 9), rng.randint(10, 99)
        e = f"    store_test.go:{rng.randint(10, 300)}: expected {a}, got {b}"
        blocks.append([f"--- FAIL: TestBad{k} (0.00s)", e]); truth += [f"--- FAIL: TestBad{k} (0.00s)", e.strip()]
    end = "FAIL\nFAIL\tgithub.com/acme/store\t0.412s"
    return dict(cmd="go test ./...", exit_code=1, log="\n".join(_place(rng, noise, blocks) + [end]),
                truth=truth + ["FAIL\tgithub.com/acme/store\t0.412s"])


def f_maven(rng: random.Random, v: int) -> Case:
    noise = [f"[INFO] Compiling {i} source files to /app/target/classes/m{i}" for i in range(_noise_count(rng) // 2)]
    blocks, truth = [], []
    for k in range(rng.randint(1, 4)):
        e = f"[ERROR] /app/src/main/java/com/acme/Svc{k}.java:[{rng.randint(5, 300)},{rng.randint(5, 60)}] cannot find symbol"
        blocks.append([e, f"[ERROR]   symbol:   class Missing{k}"]); truth += [e, f"[ERROR]   symbol:   class Missing{k}"]
    end = ["[INFO] BUILD FAILURE", "[ERROR] Failed to execute goal compile (default-compile) on project acme: Compilation failure"]
    return dict(cmd="mvn package", exit_code=1, log="\n".join(_place(rng, noise, blocks) + end), truth=truth + end)


def f_npm_install(rng: random.Random, v: int) -> Case:
    noise = [f"npm http fetch GET 200 https://registry.npmjs.org/pkg-{i} {rng.randint(5, 900)}ms" for i in range(_noise_count(rng))]
    block = ["npm ERR! code ERESOLVE", "npm ERR! ERESOLVE could not resolve", "npm ERR! Found: react@17.0.2",
             "npm ERR! Could not resolve dependency: peer react@\"^18.0.0\" from react-dom@18.2.0"]
    return dict(cmd="npm install", exit_code=1, log="\n".join(_place(rng, noise, [block])), truth=block)


def f_linker(rng: random.Random, v: int) -> Case:
    noise = [f"gcc -O2 -c src/mod{i}.c -o build/mod{i}.o" for i in range(_noise_count(rng) // 2)]
    syms = [f"foo{k}" for k in range(rng.randint(1, 4))]
    blocks = [[f"/usr/bin/ld: main.o: in function `main': main.c:(.text+0x{k}a): undefined reference to `{s}'"] for k, s in enumerate(syms)]
    truth = [b[0] for b in blocks]
    end = ["collect2: error: ld returned 1 exit status", "make: *** [Makefile:12: app] Error 1"]
    return dict(cmd="make", exit_code=2, log="\n".join(_place(rng, noise, blocks) + end), truth=truth + end)


def f_silent_fail(rng: random.Random, v: int) -> Case:
    """Failures that use none of the usual keywords (no 'error', 'fail', 'exception' ...)."""
    noise = [f"step {i}: processing shard {i % 16} ok" for i in range(_noise_count(rng))]
    msg = rng.choice(["Segmentation fault (core dumped)", "Killed", "bash: ./run_suite.sh: No such file or directory",
                      "Permission denied (publickey).", "Process exited with code 137 (OOMKilled)",
                      "timed out after 300 seconds waiting for lock"])
    return dict(cmd="make test", exit_code=2, log="\n".join(_place(rng, noise, [[msg]])), truth=[msg])


def f_docker_pip(rng: random.Random, v: int) -> Case:
    noise = [f"Step {i % 12 + 1}/12 : RUN pip install -r requirements-{i}.txt" for i in range(_noise_count(rng) // 2)]
    block = ["ERROR: Could not find a version that satisfies the requirement foo==9.9 (from versions: none)",
             "ERROR: No matching distribution found for foo==9.9",
             "The command '/bin/sh -c pip install foo==9.9' returned a non-zero code: 1"]
    return dict(cmd="docker build .", exit_code=1, log="\n".join(_place(rng, noise, [block])), truth=block)


FAMILIES: Dict[str, Callable[[random.Random, int], Case]] = {
    "pytest_verbose": f_pytest_verbose, "unittest_verbose": f_unittest_verbose,
    "gcc_warning_noise": f_gcc_warning_noise, "tsc_errors": f_tsc_errors, "cargo_build": f_cargo_build,
    "cargo_test": f_cargo_test, "jest": f_jest, "lint_many": f_lint_many, "passing_run": f_passing_run,
    "python_traceback_chain": f_python_traceback_chain, "go_test": f_go_test, "maven": f_maven,
    "npm_install": f_npm_install, "linker": f_linker, "silent_fail": f_silent_fail, "docker_pip": f_docker_pip,
}


# ----------------------------------------------------------------------------- REAL tool runs (HOLDOUT)


def _run(cmd: List[str], cwd: Path, timeout: int = 120) -> tuple[str, int]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (("\n" + p.stderr) if p.stderr else ""), p.returncode


def _real_gcc(work: Path, cxx: bool = False) -> Optional[Case]:
    cc = shutil.which("g++" if cxx else "gcc")
    if not cc:
        return None
    d = work / ("gxx" if cxx else "gcc"); d.mkdir()
    n = 60
    for i in range(n):
        body = f"int f{i}(int a) {{ int unused{i}; return a + {i}; }}\n"
        if i in (7, 31, 52):
            body = (f"int f{i}(int a) {{ return undeclared_{i} + a; }}\n" if i != 31 else
                    f"int f{i}(int a) {{ int x = ; return a; }}\n")
        (d / f"m{i}.c{'pp' if cxx else ''}").write_text(f"#include <stdio.h>\n{body}")
    if cxx:
        (d / "t.cpp").write_text('#include <vector>\nint main(){ std::vector<int> v; v.push_back("x"); return 0; }\n')
    srcs = sorted(d.glob("*.c*"))
    out, rc = "", 0
    for s in srcs:
        o, r = _run([cc, "-Wall", "-Wextra", "-c", str(s.name), "-o", s.name + ".o"], d)
        out += o; rc = rc or r
    out += f"make: *** [Makefile:9: all] Error {rc or 1}\n"
    truth = [l for l in out.splitlines() if ": error:" in l or ": fatal error:" in l] + [out.strip().splitlines()[-1]]
    if not truth[:-1]:
        return None
    return dict(cmd="make", exit_code=2, log=out, truth=truth)


def _real_cargo(work: Path, test: bool) -> Optional[Case]:
    if not shutil.which("cargo") or subprocess.run(["cargo", "--version"], capture_output=True).returncode != 0:
        return None
    d = work / ("ctest" if test else "cbuild"); (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text('[package]\nname="demo"\nversion="0.1.0"\nedition="2021"\n[lib]\npath="src/lib.rs"\n')
    if test:
        tests = "\n".join(f"#[test] fn ok_{i}() {{ assert_eq!({i}, {i}); }}" for i in range(80))
        tests += "\n#[test] fn bad_a() { assert_eq!(1 + 1, 3); }\n#[test] fn bad_b() { let v: Vec<i32> = vec![]; let _ = v[3]; }\n"
        (d / "src/lib.rs").write_text(tests)
        out, rc = _run(["cargo", "test", "--offline", "--color", "never"], d, 240)
        truth = [l.strip() for l in out.splitlines() if "panicked at" in l] + [l for l in out.splitlines() if l.startswith("test result: FAILED")]
    else:
        funcs = "\n".join(f"pub fn f{i}() {{ let unused{i} = {i}; }}" for i in range(80))
        bad = ("pub fn b1() { let s = String::new(); let t = s; println!(\"{}\", s); }\n"
               "pub fn b2() -> i32 { \"x\" }\n")
        (d / "src/lib.rs").write_text(funcs + "\n" + bad)
        out, rc = _run(["cargo", "build", "--offline", "--color", "never"], d, 240)
        truth = [l for l in out.splitlines() if l.startswith("error")] + [l.strip() for l in out.splitlines() if l.strip().startswith("--> ")]
    if rc == 0 or not truth or "rustup could not choose" in out:
        return None
    return dict(cmd="cargo test" if test else "cargo build", exit_code=rc, log=out, truth=truth)


def _real_python(work: Path) -> Optional[Case]:
    import sys
    d = work / "py"; d.mkdir()
    body = ["import unittest", "class T(unittest.TestCase):"]
    body += [f"    def test_ok_{i}(self): self.assertEqual({i}, {i})" for i in range(300)]
    body += [f"    def test_bad_{i}(self): self.assertEqual({i}, {i + 1}, 'mismatch {i}')" for i in range(3)]
    body += ["    def test_boom(self): {}['missing']"]
    (d / "test_real.py").write_text("\n".join(body) + "\n")
    out, rc = _run([sys.executable, "-B", "-m", "unittest", "-v", "test_real.py"], d)
    truth = [l for l in out.splitlines() if l.startswith("AssertionError:") or l.startswith("KeyError:")]
    truth += [l.strip() for l in out.splitlines() if l.startswith("FAILED (")]
    return dict(cmd="python3 -m unittest -v", exit_code=rc, log=out, truth=truth) if rc and truth else None


def _real_node(work: Path) -> Optional[Case]:
    node = shutil.which("node")
    if not node:
        return None
    d = work / "node"; d.mkdir()
    t = ["const test=require('node:test');const assert=require('node:assert');"]
    t += [f"test('ok {i}',()=>assert.strictEqual({i},{i}));" for i in range(200)]
    t += ["test('bad one',()=>assert.strictEqual(1,2));", "test('bad two',()=>{ null.x; });"]
    (d / "a.test.js").write_text("\n".join(t) + "\n")
    out, rc = _run([node, "--test", "a.test.js"], d)
    truth = [l.strip() for l in out.splitlines() if "AssertionError" in l or "TypeError" in l]
    truth += [l.strip() for l in out.splitlines() if l.lstrip().startswith("✖") and "bad" in l]
    return dict(cmd="npm test", exit_code=rc, log=out, truth=truth) if rc and truth else None


def generate_real_cases() -> List[Case]:
    cases: List[Case] = []
    with tempfile.TemporaryDirectory(prefix="solpi_real_") as tmp:
        work = Path(tmp)
        for fam, fn in (("real_gcc", lambda: _real_gcc(work)), ("real_gxx", lambda: _real_gcc(work, True)),
                        ("real_cargo_build", lambda: _real_cargo(work, False)), ("real_cargo_test", lambda: _real_cargo(work, True)),
                        ("real_python_unittest", lambda: _real_python(work)), ("real_node_test", lambda: _real_node(work))):
            try:
                c = fn()
            except (OSError, subprocess.SubprocessError):
                c = None
            if c:
                c.update(id=fam, family=fam, split="holdout", real=True)
                cases.append(c)
    return cases


# ----------------------------------------------------------------------------- public API


def generate_corpus(seed: int = 1337, include_real: bool = False) -> List[Case]:
    """Deterministic synthetic corpus (+ optional real tool logs). Each case: id, family, split, cmd, exit_code, log, truth."""
    cases: List[Case] = []
    for fam, fn in FAMILIES.items():
        for v in range(VARIANTS_PER_FAMILY):
            rng = random.Random(f"{seed}:{fam}:{v}")
            c = fn(rng, v)
            c.update(id=f"{fam}#{v}", family=fam, split="train" if fam in TRAIN_FAMILIES else "holdout", real=False)
            cases.append(c)
    if include_real:
        cases.extend(generate_real_cases())
    return cases
