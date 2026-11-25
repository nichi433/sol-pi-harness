"""Long-horizon tasks (tens of tool calls) for the raw-API A/B. Deterministic; each has a verify command and
protected files restored before verification. `REFERENCE_FIXES` solve each task so the suite can prove they are
solvable and that verification flips from fail to pass."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

PY_PROMPT = ("The test suite (`python3 -m unittest -v`) has failures in several modules of this project. Fix all the bugs "
             "in the source code under pkg/ (do not edit anything under tests/) until the whole suite passes.")


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# (module name, buggy source, test body, (buggy snippet, fixed snippet))
BUGS: List[Tuple[str, str, str, Tuple[str, str]]] = [
    ("stats", "def median(xs):\n    s = sorted(xs)\n    return s[len(s) // 2]\n",
     "self.assertEqual(median([3, 1, 2]), 2)\n        self.assertEqual(median([1, 2, 3, 4]), 2.5)",
     ("return s[len(s) // 2]", "n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2")),
    ("roman", "def to_roman(n):\n    out = ''\n    for v, s in ((1000, 'M'), (500, 'D'), (100, 'C'), (50, 'L'), (10, 'X'), (5, 'V'), (1, 'I')):\n        while n >= v:\n            out += s\n            n -= v\n    return out\n",
     "self.assertEqual(to_roman(3), 'III')\n        self.assertEqual(to_roman(4), 'IV')\n        self.assertEqual(to_roman(1994), 'MCMXCIV')",
     ("for v, s in ((1000, 'M'), (500, 'D'), (100, 'C'), (50, 'L'), (10, 'X'), (5, 'V'), (1, 'I')):",
      "for v, s in ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')):")),
    ("rle", "def rle_encode(s):\n    out, i = [], 0\n    while i < len(s) - 1:\n        j = i\n        while j + 1 < len(s) and s[j + 1] == s[i]:\n            j += 1\n        out.append(f'{j - i + 1}{s[i]}')\n        i = j + 1\n    return ''.join(out)\n",
     "self.assertEqual(rle_encode('aaabcc'), '3a1b2c')\n        self.assertEqual(rle_encode('x'), '1x')",
     ("while i < len(s) - 1:", "while i < len(s):")),
    ("flat", "def flatten(items):\n    out = []\n    for x in items:\n        if isinstance(x, list):\n            out.extend(x)\n        else:\n            out.append(x)\n    return out\n",
     "self.assertEqual(flatten([1, [2, [3, [4]]]]), [1, 2, 3, 4])",
     ("out.extend(x)", "out.extend(flatten(x))")),
    ("version", "def version_key(v):\n    return tuple(v.split('.'))\n",
     "self.assertTrue(version_key('1.10.0') > version_key('1.9.3'))\n        self.assertEqual(version_key('2.0'), (2, 0))",
     ("return tuple(v.split('.'))", "return tuple(int(p) for p in v.split('.'))")),
    ("chunks", "def chunk(items, n):\n    return [items[i:i + n] for i in range(0, len(items) - n + 1, n)]\n",
     "self.assertEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])",
     ("len(items) - n + 1", "len(items)")),
    ("slug", "import re\n\ndef slugify(text):\n    return re.sub(r'[^a-z0-9]', '-', text.lower())\n",
     "self.assertEqual(slugify('Hello  World!'), 'hello-world')",
     ("return re.sub(r'[^a-z0-9]', '-', text.lower())", "return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')")),
    ("leap", "def is_leap(y):\n    return y % 4 == 0 or y % 400 == 0\n",
     "self.assertTrue(is_leap(2000))\n        self.assertFalse(is_leap(1900))\n        self.assertTrue(is_leap(2024))",
     ("return y % 4 == 0 or y % 400 == 0", "return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0")),
    ("freq", "def word_freq(text):\n    out = {}\n    for w in text.split():\n        out[w] = out.get(w, 0) + 1\n    return out\n",
     "self.assertEqual(word_freq('a A b'), {'a': 2, 'b': 1})",
     ("for w in text.split():", "for w in text.lower().split():")),
    ("search", "def binary_search(xs, t):\n    lo, hi = 0, len(xs) - 1\n    while lo < hi:\n        mid = (lo + hi) // 2\n        if xs[mid] == t:\n            return mid\n        if xs[mid] < t:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1\n",
     "self.assertEqual(binary_search([1, 3, 5, 7, 9], 9), 4)\n        self.assertEqual(binary_search([1, 3, 5], 1), 0)\n        self.assertEqual(binary_search([1, 3, 5], 4), -1)",
     ("while lo < hi:", "while lo <= hi:")),
    ("avg", "def moving_avg(xs, k):\n    return [sum(xs[i:i + k - 1]) / k for i in range(len(xs) - k + 1)]\n",
     "self.assertEqual(moving_avg([1, 2, 3, 4], 2), [1.5, 2.5, 3.5])",
     ("xs[i:i + k - 1]", "xs[i:i + k]")),
    ("snake", "import re\n\ndef camel_to_snake(s):\n    return re.sub(r'([A-Z])', r'_\\1', s).lower()\n",
     "self.assertEqual(camel_to_snake('camelCaseName'), 'camel_case_name')\n        self.assertEqual(camel_to_snake('Name'), 'name')",
     ("return re.sub(r'([A-Z])', r'_\\1', s).lower()", "return re.sub(r'(?<!^)([A-Z])', r'_\\1', s).lower()")),
]

PADDING = '''

# --- helpers kept for backwards compatibility -------------------------------------------------------------
def _describe(value):
    """Human readable description used by the legacy report generator."""
    return "%s(%r)" % (type(value).__name__, value)


def _legacy_wrapper(fn, *args, **kwargs):
    """Call `fn` and swallow nothing; kept so old callers that imported it keep working."""
    return fn(*args, **kwargs)
''' + "\n".join(f"# legacy note {i}: this module has been in production since release {i}.{i % 7}" for i in range(40)) + "\n"


def py_many_bugs(root: Path) -> Dict[str, Any]:
    w(root / "pkg" / "__init__.py", "")
    w(root / "tests" / "__init__.py", "")
    for name, src, test, _ in BUGS:
        w(root / "pkg" / f"{name}.py", src.rstrip("\n") + "\n" + PADDING)
        fn = [l.split("(")[0].replace("def ", "") for l in src.splitlines() if l.startswith("def ")][0]
        extra = "\n".join(f"    def test_smoke_{k}(self):\n        self.assertTrue(callable({fn}))" for k in range(14))
        w(root / "tests" / f"test_{name}.py",
          f"import unittest\nfrom pkg.{name} import {fn}\n\nclass Test{name.title()}(unittest.TestCase):\n    def test_behaviour(self):\n        {test}\n{extra}\n")
    for i in range(14):  # unrelated passing modules: a verbose suite
        w(root / "pkg" / f"util{i}.py", "".join(f"def f{k}(x):\n    return x * {k + 1} + {i}\n" for k in range(12)))
        w(root / "tests" / f"test_util{i}.py", f"import unittest\nfrom pkg import util{i} as m\n\nclass TestUtil{i}(unittest.TestCase):\n" +
          "".join(f"    def test_f{k}(self):\n        self.assertEqual(m.f{k}(3), {3 * (k + 1) + i})\n" for k in range(12)))
    return dict(prompt=PY_PROMPT, verify="python3 -m unittest", protected=["tests"])


def c_many_errors(root: Path) -> Dict[str, Any]:
    w(root / "Makefile", "CC = gcc\nCFLAGS = -Wall -Wextra -O0\nOBJS = $(patsubst src/%.c,build/%.o,$(wildcard src/*.c))\n\n"
      "all: app\n\napp: $(OBJS)\n\t$(CC) -o $@ $^\n\nbuild/%.o: src/%.c\n\t@mkdir -p build\n\t$(CC) $(CFLAGS) -c $< -o $@\n\n"
      "test: app tests/runner.c\n\t$(CC) -o test_runner tests/runner.c $(filter-out build/main.o,$(OBJS))\n\t./test_runner\n\nclean:\n\trm -rf build app test_runner\n")
    n = 30
    for i in range(n):
        junk = "".join(f"static int helper{i}_{j}(int unused) {{ return {j}; }}\n" for j in range(6))
        w(root / "src" / f"m{i:02d}.c", f"{junk}\nint m{i:02d}(int a, int b) {{\n    return a + b + {i};\n}}\n")
    # six independent defects
    def patch(name: str, a: str, b: str) -> None:
        p = root / "src" / name
        p.write_text(p.read_text().replace(a, b, 1))
    patch("m04.c", "return a + b + 4;", "int total = a + b + 4;\n    return totl;")
    patch("m09.c", "return a + b + 9;", "return a + b + 9 + m08(1);")
    patch("m14.c", "return a + b + 14;", "return a + b + 14")
    patch("m19.c", "return a + b + 19;", "const char *s = a + b;\n    return a + b + 19;")
    patch("m24.c", "return a + b + 24;", "return a - b + 24;")
    patch("m27.c", "return a + b + 27;", "return a + b + 28;")
    decls = "".join(f"int m{i:02d}(int a, int b);\n" for i in range(n))
    calls = " + ".join(f"m{i:02d}(1, 2)" for i in range(n))
    w(root / "src" / "main.c", f'#include <stdio.h>\n{decls}\nint main(void) {{\n    printf("SUM=%d\\n", {calls});\n    return 0;\n}}\n')
    checks = "".join(f'    CHECK(m{i:02d}({i % 5}, {i % 3}) == {i % 5 + i % 3 + i}, "m{i:02d} case {c}");\n' for c in range(6) for i in range(n))
    w(root / "tests" / "runner.c", "#include <stdio.h>\n" + decls + "static int n = 0, failed = 0;\n"
      '#define CHECK(c, name) do { n++; if (c) printf("ok %d - %s\\n", n, name); else { failed++; printf("not ok %d - %s (%s:%d)\\n", n, name, __FILE__, __LINE__); } } while (0)\n'
      f"int main(void) {{\n{checks}    printf(\"# %d tests, %d failed\\n\", n, failed);\n    return failed ? 1 : 0;\n}}\n")
    return dict(prompt="`make` fails to build this C project and, once it builds, `make test` reports failing checks. Fix the "
                "defects in src/ (warnings may stay; do not edit tests/ or the Makefile) until `make && make test` both succeed.",
                verify="make clean >/dev/null && make >/dev/null 2>&1 && make test >/dev/null 2>&1", protected=["tests", "Makefile"])


def py_api_migration(root: Path) -> Dict[str, Any]:
    w(root / "pkg" / "__init__.py", "")
    w(root / "pkg" / "client.py", "class Response:\n    def __init__(self, value, items=None):\n        self.value = value\n        self.items = items or []\n\n\n"
      "class Client:\n    \"\"\"Key-value client. `get` used to return a dict; it now returns a Response object.\"\"\"\n\n"
      "    def __init__(self, data):\n        self._data = data\n\n    def get(self, key):\n        return Response(self._data.get(key, 0), [self._data.get(key, 0)])\n")
    w(root / "tests" / "__init__.py", "")
    patterns = ['    return client.get(key)["value"] + {i}\n', '    resp = client.get(key)\n    return resp["value"] * {i}\n',
                '    return client.get(key).get("value", 0) - {i}\n', '    resp = client.get(key)\n    return resp["items"][0] + {i}\n']
    expected = ['{v} + {i}', '{v} * {i}', '{v} - {i}', '{v} + {i}']
    for i in range(24):
        p = i % 4
        w(root / "pkg" / f"svc{i:02d}.py", f"def load_{i:02d}(client, key):\n    \"\"\"Load service {i}.\"\"\"\n" + patterns[p].format(i=i) +
          "\n" + "\n".join(f"# service {i} note {k}" for k in range(25)) + "\n")
        w(root / "tests" / f"test_svc{i:02d}.py", "import unittest\nfrom pkg.client import Client\n"
          f"from pkg.svc{i:02d} import load_{i:02d}\n\nclass TestSvc{i:02d}(unittest.TestCase):\n    def setUp(self):\n        self.c = Client({{'k': 7}})\n" +
          "".join(f"    def test_value_{k}(self):\n        self.assertEqual(load_{i:02d}(self.c, 'k'), {eval(expected[p].format(v=7, i=i))})\n" for k in range(8)))
    return dict(prompt="`pkg/client.py` changed: `Client.get` now returns a `Response` object (with `.value` and `.items`) instead of "
                "a dict. Update every caller under pkg/ so the whole suite (`python3 -m unittest -v`) passes again. Do not edit "
                "tests/ or pkg/client.py.", verify="python3 -m unittest", protected=["tests", "pkg/client.py"])


TASKS = [("l1_many_bugs", py_many_bugs), ("l2_c_many_errors", c_many_errors), ("l3_api_migration", py_api_migration)]


def reference_fix(task_id: str, work: Path) -> None:
    """Apply a known-good solution (used by tests to prove each task is solvable)."""
    if task_id == "l1_many_bugs":
        for name, _, _, (a, b) in BUGS:
            p = work / "pkg" / f"{name}.py"
            t = p.read_text()
            assert a in t, (name, a)
            p.write_text(t.replace(a, b, 1))
    elif task_id == "l2_c_many_errors":
        def patch(name: str, a: str, b: str) -> None:
            p = work / "src" / name
            t = p.read_text()
            assert a in t, (name, a)
            p.write_text(t.replace(a, b, 1))
        patch("m04.c", "return totl;", "return total;")
        patch("m09.c", " + m08(1)", "")
        patch("m14.c", "return a + b + 14\n", "return a + b + 14;\n")
        patch("m19.c", "const char *s = a + b;\n    ", "")
        patch("m24.c", "return a - b + 24;", "return a + b + 24;")
        patch("m27.c", "return a + b + 28;", "return a + b + 27;")
    elif task_id == "l3_api_migration":
        for p in (work / "pkg").glob("svc*.py"):
            t = p.read_text().replace('client.get(key)["value"]', "client.get(key).value").replace('resp["value"]', "resp.value") \
                .replace('client.get(key).get("value", 0)', "client.get(key).value").replace('resp["items"][0]', "resp.items[0]")
            p.write_text(t)


def generate(root: Path) -> List[Dict[str, Any]]:
    metas = []
    for tid, fn in TASKS:
        base = root / tid
        if base.exists():
            shutil.rmtree(base)
        work = base / "work"
        work.mkdir(parents=True)
        meta = fn(work)
        meta["id"] = tid
        (base / "protected").mkdir()
        for rel in meta["protected"]:
            src, dst = work / rel, base / "protected" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        (base / "meta.json").write_text(json.dumps(meta, indent=1))
        metas.append(meta)
    return metas
