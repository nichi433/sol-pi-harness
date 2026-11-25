#!/usr/bin/env python3
"""Generate the 10 A/B tasks (deterministic). Each task:

    <root>/<id>/work/        project the agent edits (identical for both conditions)
    <root>/<id>/protected/   pristine copies of files the agent must not change (tests, Makefile); restored
                             before verification so editing the tests cannot "pass" a task
    <root>/<id>/meta.json    {id, group, prompt, verify, protected}

Groups:  noisy   (6) the test/build command prints 8-60 KB - where the toolkit is supposed to help
         control (4) tiny outputs - where it can only cost (tool schemas / hook overhead)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List

PY_PROMPT = ("The unit tests of this project fail. Run them with `python3 -m unittest -v`, find the cause and fix "
             "the SOURCE code under pkg/ (do not edit anything under tests/). Stop once all tests pass.")


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def noise_package(root: Path, n_mods: int, tests_per_mod: int) -> None:
    """Many unrelated modules with passing tests -> verbose `unittest -v` output."""
    for i in range(n_mods):
        funcs = "\n".join(f"def f{k}(x):\n    return x * {k + 1} + {i}\n" for k in range(tests_per_mod))
        w(root / "pkg" / f"noise{i}.py", funcs)
        tests = ["import unittest", f"from pkg import noise{i} as m", "", f"class TestNoise{i}(unittest.TestCase):"]
        tests += [f"    def test_f{k}(self):\n        self.assertEqual(m.f{k}(3), {3 * (k + 1) + i})" for k in range(tests_per_mod)]
        w(root / "tests" / f"test_noise{i}.py", "\n".join(tests) + "\n")


def py_base(root: Path) -> None:
    w(root / "pkg" / "__init__.py", "")
    w(root / "tests" / "__init__.py", "")


def t_off_by_one(root: Path) -> Dict:
    py_base(root); noise_package(root, 30, 20)
    w(root / "pkg" / "pagination.py", '''def paginate(items, page, size):
    """Return the items of the 1-based page `page`."""
    start = page * size
    return items[start:start + size]
''')
    w(root / "tests" / "test_pagination.py", '''import unittest
from pkg.pagination import paginate

class TestPagination(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(paginate(list(range(10)), 1, 3), [0, 1, 2])
    def test_second_page(self):
        self.assertEqual(paginate(list(range(10)), 2, 3), [3, 4, 5])
    def test_last_partial_page(self):
        self.assertEqual(paginate(list(range(10)), 4, 3), [9])
''')
    return dict(group="noisy", prompt=PY_PROMPT, verify="python3 -m unittest", protected=["tests"])


def t_multi_bug(root: Path) -> Dict:
    py_base(root); noise_package(root, 25, 20)
    w(root / "pkg" / "pricing.py", "def apply_discount(price, pct):\n    return round(price * (1 - pct / 100))\n")
    w(root / "pkg" / "slug.py", 'def slugify(text):\n    return text.strip().replace(" ", "-")\n')
    w(root / "pkg" / "dates.py", "from datetime import date\n\ndef days_between(a, b):\n    return (b - a).days\n")
    w(root / "pkg" / "merge.py", "def deep_merge(a, b):\n    out = dict(a)\n    out.update(b)\n    return out\n")
    w(root / "tests" / "test_features.py", '''import unittest
from datetime import date
from pkg.pricing import apply_discount
from pkg.slug import slugify
from pkg.dates import days_between
from pkg.merge import deep_merge

class TestFeatures(unittest.TestCase):
    def test_discount_keeps_cents(self):
        self.assertEqual(apply_discount(19.99, 10), 17.99)
    def test_slugify_lowercases(self):
        self.assertEqual(slugify("  Hello World "), "hello-world")
    def test_days_between_is_absolute(self):
        self.assertEqual(days_between(date(2024, 3, 10), date(2024, 3, 1)), 9)
    def test_deep_merge_nested(self):
        self.assertEqual(deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}}), {"a": {"x": 1, "y": 3}, "b": 1})
''')
    return dict(group="noisy", prompt=PY_PROMPT, verify="python3 -m unittest", protected=["tests"])


def t_deep_trace(root: Path) -> Dict:
    py_base(root); noise_package(root, 10, 10)
    stages = ["def normalize(value):\n    print('DEBUG normalize', value)\n    return value.strip().lower()\n"]
    for i in range(1, 9):
        inner = "normalize(value)" if i == 1 else f"stage{i - 1}(value)"
        stages.append(f"def stage{i}(value):\n    print('DEBUG stage{i}', value)\n    return {inner}\n")
    w(root / "pkg" / "pipeline.py", "\n".join(stages) + '''
def process(value):
    """Run a value through the 8-stage pipeline; must accept strings and numbers."""
    return stage8(value)
''')
    w(root / "tests" / "test_pipeline.py", '''import unittest
from pkg.pipeline import process

class TestPipeline(unittest.TestCase):
''' + "".join(f"    def test_text_{i}(self):\n        self.assertEqual(process('  Item {i} '), 'item {i}')\n" for i in range(150)) + '''    def test_numbers_are_accepted(self):
        self.assertEqual(process(42), "42")
''')
    return dict(group="noisy", prompt=PY_PROMPT, verify="python3 -m unittest", protected=["tests"])


def c_project(root: Path, n_files: int) -> None:
    w(root / "Makefile", '''CC = gcc
CFLAGS = -Wall -Wextra -O0
OBJS = $(patsubst src/%.c,build/%.o,$(wildcard src/*.c))

all: app

app: $(OBJS)
\t$(CC) -o $@ $^

build/%.o: src/%.c
\t@mkdir -p build
\t$(CC) $(CFLAGS) -c $< -o $@

clean:
\trm -rf build app test_runner
''')
    for i in range(n_files):
        junk = "".join(f"static int helper{i}_{j}(int unused) {{ return {j}; }}\n" for j in range(6))
        w(root / "src" / f"m{i}.c", f"{junk}\nint m{i}(int a, int b) {{\n    return a + b + {i};\n}}\n")
    calls = " + ".join(f"m{i}(1, 2)" for i in range(n_files))
    decls = "".join(f"int m{i}(int a, int b);\n" for i in range(n_files))
    w(root / "src" / "main.c", f'#include <stdio.h>\n{decls}\nint main(void) {{\n    printf("SUM=%d\\n", {calls});\n    return 0;\n}}\n')


def t_c_compile(root: Path) -> Dict:
    c_project(root, 40)
    p = root / "src" / "m7.c"; p.write_text(p.read_text().replace("return a + b + 7;", "int total = a + b + 7;\n    return totl;"))
    p = root / "src" / "m23.c"; p.write_text(p.read_text().replace("return a + b + 23;", "return a + b + 23 + m22(1);"))
    expected = sum(1 + 2 + i for i in range(40))
    return dict(group="noisy", prompt="`make` fails to build this C project. Run `make`, fix the compile errors in src/ "
                "(warnings can stay), and stop once `make && ./app` prints SUM=" + str(expected) + ".",
                verify=f"make clean >/dev/null && make >/dev/null 2>&1 && ./app | grep -qx 'SUM={expected}'", protected=["Makefile"])


def t_c_logic(root: Path) -> Dict:
    w(root / "src" / "clamp.c", "int clamp(int v, int lo, int hi) {\n    if (v < lo) return lo;\n    if (v > hi) return hi - 1;\n    return v;\n}\n")
    cases = "".join(f"    CHECK(clamp({i}, 0, 100) == {min(max(i, 0), 100)}, \"clamp {i}\");\n" for i in range(-150, 350))
    w(root / "tests" / "runner.c", f'''#include <stdio.h>
int clamp(int v, int lo, int hi);
static int n = 0, failed = 0;
#define CHECK(cond, name) do {{ n++; if (cond) printf("ok %d - %s\\n", n, name); else {{ failed++; printf("not ok %d - %s (%s:%d)\\n", n, name, __FILE__, __LINE__); }} }} while (0)
int main(void) {{
{cases}    printf("# %d tests, %d failed\\n", n, failed);
    return failed ? 1 : 0;
}}
''')
    w(root / "Makefile", "test: test_runner\n\t./test_runner\n\ntest_runner: src/clamp.c tests/runner.c\n\tgcc -Wall -o $@ $^\n\nclean:\n\trm -f test_runner\n")
    return dict(group="noisy", prompt="`make test` reports failing checks. Run it, find the bug in src/clamp.c and fix it. "
                "Do not edit tests/ or the Makefile. Stop once `make test` passes.", verify="make clean >/dev/null && make test >/dev/null 2>&1",
                protected=["tests", "Makefile"])


def t_feature(root: Path) -> Dict:
    py_base(root); noise_package(root, 20, 20)
    w(root / "pkg" / "text.py", '''def wrap_text(text, width):
    """Greedy word wrap: return a list of lines, each at most `width` characters."""
    raise NotImplementedError


def titlecase(text):
    """Capitalise each word but keep the small words 'a', 'an', 'the', 'of' lowercase (except first)."""
    raise NotImplementedError
''')
    w(root / "tests" / "test_text.py", '''import unittest
from pkg.text import wrap_text, titlecase

class TestText(unittest.TestCase):
    def test_wrap(self):
        self.assertEqual(wrap_text("the quick brown fox jumps", 10), ["the quick", "brown fox", "jumps"])
    def test_wrap_long_word(self):
        self.assertEqual(wrap_text("abcdefghijkl mn", 5), ["abcdefghijkl", "mn"])
    def test_titlecase(self):
        self.assertEqual(titlecase("the lord of the rings"), "The Lord of the Rings")
''')
    return dict(group="noisy", prompt="Some tests fail because `pkg/text.py` is not implemented. Run `python3 -m unittest -v`, implement the two "
                "functions according to their docstrings and the tests (do not edit tests/). Stop once all tests pass.",
                verify="python3 -m unittest", protected=["tests"])


def t_rename(root: Path) -> Dict:
    py_base(root)
    for i in range(3):
        w(root / "pkg" / f"shop{i}.py", f"def calc_total(items):\n    return sum(items) + {i}\n\ndef label{i}(items):\n    return 'total=%d' % calc_total(items)\n")
        w(root / "tests" / f"test_shop{i}.py", f"import unittest\nfrom pkg.shop{i} import compute_total, label{i}\n\nclass T{i}(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(compute_total([1, 2]), {3 + i})\n    def test_label(self):\n        self.assertEqual(label{i}([1, 2]), 'total={3 + i}')\n")
    return dict(group="control", prompt="Rename the function `calc_total` to `compute_total` everywhere in pkg/ (the tests already use the new "
                "name). Run `python3 -m unittest` to check. Do not edit tests/.",
                verify="python3 -m unittest && ! grep -rq calc_total pkg", protected=["tests"])


def t_palindrome(root: Path) -> Dict:
    py_base(root)
    w(root / "pkg" / "words.py", 'def is_palindrome(s):\n    return s == s[::-1]\n')
    w(root / "tests" / "test_words.py", 'import unittest\nfrom pkg.words import is_palindrome\n\nclass T(unittest.TestCase):\n    def test_plain(self):\n        self.assertTrue(is_palindrome("level"))\n    def test_case_and_punct(self):\n        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama"))\n    def test_negative(self):\n        self.assertFalse(is_palindrome("hello"))\n')
    return dict(group="control", prompt=PY_PROMPT, verify="python3 -m unittest", protected=["tests"])


def t_fizz(root: Path) -> Dict:
    py_base(root)
    w(root / "pkg" / "fizz.py", "def fizzbuzz(n):\n    \"\"\"Return the list of strings for 1..n (Fizz, Buzz, FizzBuzz or the number).\"\"\"\n    raise NotImplementedError\n")
    w(root / "tests" / "test_fizz.py", 'import unittest\nfrom pkg.fizz import fizzbuzz\n\nclass T(unittest.TestCase):\n    def test_15(self):\n        self.assertEqual(fizzbuzz(15)[-1], "FizzBuzz")\n        self.assertEqual(fizzbuzz(5), ["1", "2", "Fizz", "4", "Buzz"])\n')
    return dict(group="control", prompt="Implement `fizzbuzz` in pkg/fizz.py so that `python3 -m unittest` passes. Do not edit tests/.",
                verify="python3 -m unittest", protected=["tests"])


def t_config(root: Path) -> Dict:
    py_base(root)
    w(root / "pkg" / "config.py", 'import json\n\nDEFAULTS = {"debug": False, "retries": 3, "name": "app"}\n\ndef load_config(path):\n    with open(path) as fh:\n        data = json.load(fh)\n    cfg = dict(data)\n    cfg.update(DEFAULTS)\n    return cfg\n')
    w(root / "tests" / "test_config.py", 'import json, os, tempfile, unittest\nfrom pkg.config import load_config\n\nclass T(unittest.TestCase):\n    def _load(self, data):\n        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:\n            json.dump(data, fh)\n        try:\n            return load_config(fh.name)\n        finally:\n            os.unlink(fh.name)\n    def test_file_overrides_defaults(self):\n        self.assertEqual(self._load({"retries": 9})["retries"], 9)\n    def test_defaults_fill_missing(self):\n        self.assertEqual(self._load({})["name"], "app")\n    def test_extra_keys_kept(self):\n        self.assertEqual(self._load({"x": 1})["x"], 1)\n')
    return dict(group="control", prompt=PY_PROMPT, verify="python3 -m unittest", protected=["tests"])


TASKS = [("t01_off_by_one", t_off_by_one), ("t02_multi_bug", t_multi_bug), ("t03_deep_trace", t_deep_trace),
         ("t04_c_compile", t_c_compile), ("t05_c_logic", t_c_logic), ("t06_feature", t_feature),
         ("t07_rename", t_rename), ("t08_palindrome", t_palindrome), ("t09_fizz", t_fizz), ("t10_config", t_config)]


def generate(root: Path) -> List[Dict]:
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
            src = work / rel
            dst = base / "protected" / rel
            shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        (base / "meta.json").write_text(json.dumps(meta, indent=1))
        metas.append(meta)
    return metas


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ab_tasks")
    for m in generate(out):
        print(m["id"], m["group"])
