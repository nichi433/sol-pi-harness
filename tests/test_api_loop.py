"""Offline tests for the raw-API agent loop (scripted fake client: no network, no API key)."""
from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks" / "api_loop"))

from agent import ARMS, PRICES, head_tail, run_agent  # noqa: E402
import tasks as long_tasks  # noqa: E402
import run_ab  # noqa: E402


def tool_use(i: str, name: str, **inp):
    return NS(type="tool_use", id=i, name=name, input=inp)


def response(blocks, stop="tool_use", usage=(1000, 100, 0, 0), edits=None):
    u = NS(input_tokens=usage[0], output_tokens=usage[1], cache_read_input_tokens=usage[2], cache_creation_input_tokens=usage[3])
    cm = NS(applied_edits=[NS(cleared_input_tokens=n) for n in edits]) if edits else None
    return NS(content=blocks, stop_reason=stop, usage=u, context_management=cm)


class FakeClient:
    """Plays a script: `script(call_no, kwargs) -> response`. Records a deep copy of every request."""

    def __init__(self, script):
        self.script, self.calls = script, []
        self.messages = NS(create=lambda **kw: self._call("messages", kw))
        self.beta = NS(messages=NS(create=lambda **kw: self._call("beta", kw)))

    def _call(self, kind, kw):
        self.calls.append((kind, copy.deepcopy({k: v for k, v in kw.items() if k != "tools"})))
        return self.script(len(self.calls), kw)


def last_results(kw):
    return kw["messages"][-1]["content"]


class TestApiLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="solpi_apitest_"))
        (self.tmp / "f.txt").write_text("line1\nline2\n")

    def run_arm(self, arm, script, **kw):
        client = FakeClient(script)
        res = run_agent(client, "claude-sonnet-5-5", "do it", self.tmp, ARMS[arm], **kw)
        return client, res

    def big_then_done(self, call, kw):
        if call == 1:  # two parallel tool calls, one of them a 50 KB output
            return response([tool_use("a", "bash", command="python3 -c \"print('x' * 50000)\""),
                             tool_use("b", "read_file", path="f.txt")])
        return response([NS(type="text", text="done")], stop="end_turn")

    def test_arms_shape_tool_output_and_results_share_one_message(self):
        sizes = {}
        for arm in "NTSC":
            client, res = self.run_arm(arm, self.big_then_done)
            msgs = client.calls[1][1]["messages"]
            results = msgs[-1]["content"]
            self.assertEqual(msgs[-1]["role"], "user")
            self.assertEqual([r["tool_use_id"] for r in results], ["a", "b"])  # both results, ONE message
            sizes[arm] = len(results[0]["content"])
            self.assertEqual(res.stop, "end_turn")
            self.assertEqual(res.turns, 2)
        self.assertGreater(sizes["N"], 49_000)                       # naive: raw
        self.assertEqual(sizes["C"], sizes["N"])                     # context editing does not shrink at source
        self.assertLess(sizes["T"], 8_500)                           # trimmed to head+tail
        self.assertLess(sizes["S"], 4_000)                           # handle/receipt
        client, _ = self.run_arm("S", self.big_then_done)
        self.assertIn("obs_", client.calls[1][1]["messages"][-1]["content"][0]["content"])

    def test_context_editing_arm_uses_beta_endpoint_and_reports_cleared_tokens(self):
        def script(call, kw):
            if call == 1:
                return response([tool_use("a", "bash", command="echo hi")], edits=None)
            return response([NS(type="text", text="ok")], stop="end_turn", edits=[12_000, 3_000])
        client, res = self.run_arm("C", script)
        kinds = [k for k, _ in client.calls]
        self.assertEqual(kinds, ["beta", "beta"])
        kw = client.calls[0][1]
        self.assertEqual(kw["betas"], ["context-management-2025-06-27"])
        edit = kw["context_management"]["edits"][0]
        self.assertEqual(edit["type"], "clear_tool_uses_20250919")
        self.assertEqual(edit["trigger"], {"type": "input_tokens", "value": 30_000})
        self.assertEqual(edit["keep"], {"type": "tool_uses", "value": 3})
        self.assertEqual(res.cleared_tokens, 15_000)
        self.assertEqual(kw["cache_control"], {"type": "ephemeral"})  # caching on in every arm
        # the other arms never touch the beta endpoint
        for arm in "NTS":
            client, _ = self.run_arm(arm, script)
            self.assertEqual({k for k, _ in client.calls}, {"messages"})

    def test_history_is_append_only_in_every_arm(self):
        def script(call, kw):
            if call <= 3:
                return response([tool_use(f"t{call}", "bash", command=f"python3 -c \"print('y' * {30_000 * call})\"")])
            return response([NS(type="text", text="done")], stop="end_turn")
        for arm in "NTSC":
            client, _ = self.run_arm(arm, script)
            snaps = [c[1]["messages"] for c in client.calls]
            for a, b in zip(snaps, snaps[1:]):
                # the later request must start with exactly the earlier request (thinking blocks, results, all)
                self.assertEqual(repr(b[:len(a)]), repr(a), f"history edited in arm {arm}")

    def test_solpi_recall_roundtrip_and_cost_accounting(self):
        def script(call, kw):
            if call == 1:
                return response([tool_use("a", "bash", command="python3 -c \"[print('row', i) for i in range(4000)]; print('NEEDLE-7731')\"")],
                                usage=(2000, 100, 0, 5000))
            if call == 2:
                text = last_results(kw)[0]["content"]
                obs = text[text.index("obs_"):][:14]
                return response([tool_use("b", "solpi_read_obs", obs_id=obs, grep_pattern="NEEDLE")], usage=(500, 80, 5000, 600))
            recalled = last_results(kw)[0]["content"]
            assert "NEEDLE-7731" in recalled, recalled[:200]
            return response([NS(type="text", text="found")], stop="end_turn", usage=(300, 50, 5600, 400))
        client, res = self.run_arm("S", script)
        self.assertEqual(res.recalls, 1)
        self.assertEqual((res.input_tokens, res.output_tokens, res.cache_read, res.cache_write), (2800, 230, 10600, 6000))
        pin, pout, pread, pwrite = PRICES["claude-sonnet-5-5"]
        self.assertAlmostEqual(res.cost_usd, (2800 * pin + 230 * pout + 10600 * pread + 6000 * pwrite) / 1e6, places=9)

    def test_budget_and_turn_limits_and_sandbox(self):
        def pricey(call, kw):
            return response([tool_use(f"t{call}", "bash", command="true")], usage=(1_000_000, 100_000, 0, 0))
        _, res = self.run_arm("N", pricey, max_cost=1.0)
        self.assertEqual(res.stop, "budget")
        self.assertEqual(res.turns, 1)
        _, res = self.run_arm("N", lambda c, kw: response([tool_use(f"t{c}", "bash", command="true")]), max_turns=3)
        self.assertEqual((res.stop, res.turns), ("max_turns", 3))
        # file tools cannot leave the project directory
        def escape(call, kw):
            if call == 1:
                return response([tool_use("a", "read_file", path="../../../../etc/passwd")])
            return response([NS(type="text", text="x")], stop="end_turn")
        client, _ = self.run_arm("N", escape)
        self.assertIn("outside the project", client.calls[1][1]["messages"][-1]["content"][0]["content"])

    def test_head_tail(self):
        self.assertEqual(head_tail("abc", 10), "abc")
        t = head_tail("a" * 100 + "b" * 100, 20)
        self.assertTrue(t.startswith("a" * 10) and t.endswith("b" * 10) and "180 characters truncated" in t)

    def test_long_tasks_are_solvable_and_verification_flips(self):
        root = Path(tempfile.mkdtemp(prefix="solpi_lt_"))
        for meta in long_tasks.generate(root):
            work = root / meta["id"] / "work"
            v = lambda: subprocess.run(meta["verify"], shell=True, cwd=work, capture_output=True).returncode  # noqa: E731
            self.assertNotEqual(v(), 0, meta["id"])
            long_tasks.reference_fix(meta["id"], work)
            self.assertEqual(v(), 0, meta["id"])

    def test_report_summarizes_arms_relative_to_naive(self):
        rows = [dict(task="t", arm=a, rep=0, success=True, cost_usd=c, turns=10, cache_read=2_000_000, tool_result_bytes=50_000,
                     recalls=0, cleared_tokens=0, error="") for a, c in (("N", 1.0), ("T", 0.8), ("S", 0.5), ("C", 0.9))]
        text = run_ab.summarize(rows)
        self.assertIn("-50%", text)
        self.assertIn("S $0.50 (-50% vs N)", text)


if __name__ == "__main__":
    unittest.main()
