"""Comprehensive test suite for the `sol-pi-harness` toolkit.

Verifies:
1. Mechanism 1: Action Fusion (file mutation + validation command + syntax pre-check rollback).
2. Mechanism 2: ObservationPack (CAS archiving, paged recall, line neighborhood, regex grep, deduplication).
3. Mechanism 3: Evidence-Preserving Reducer (100% verbatim substring verification, Fail-Open on 1-char hallucination, secret redaction).
4. Mechanism 4: Online Context Compaction (KV-cache economic break-even & window pressure gate).
5. Mechanism 5: Auto-Research RSI Loop (`solpi tune` capability floor & efficiency selection).
6. Universal MCP Server (`initialize`, `tools/list`, `tools/call` JSON-RPC 2.0 protocol).
7. Lifecycle Hook Runner (`PreToolUse` command overwrite & `PreInvocation` pressure notice).
8. Multi-Harness Installer (`solpi install` for AGY, Claude Code, Codex, and Cursor).
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from solpi.action_fusion import fuse_edit_and_verify
from solpi.auto_research import run_auto_research_loop
from solpi.compactor import OnlineContextCompactor
from solpi.config import DEFAULT_CONFIG, load_config
from solpi.hook_runner import dispatch_hook, should_wrap_command
from solpi.installer import install_for_target
from solpi.mcp_server import handle_jsonrpc_message
from solpi.observation_pack import ObservationPackStore
from solpi.reducer import DiagnosticReceipt, reduce_log_fail_open, verify_receipt


class TestSoLPiMechanisms(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.store = ObservationPackStore(store_dir=self.tmp_path / "obs_store")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_mechanism_1_action_fusion_success_and_syntax_gate(self) -> None:
        target_py = self.tmp_path / "calc.py"
        target_py.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

        # 1. Valid edit + immediate python verification command
        res = fuse_edit_and_verify(
            file_path=target_py,
            target="return a - b",
            replacement="return a + b",
            verify_cmd=f"{sys.executable} -c \"import calc; assert calc.add(2, 3) == 5; print('PASS_OK')\"",
            cwd=self.tmp_path,
            store=self.store,
        )
        self.assertTrue(res.success)
        self.assertTrue(res.edit_applied)
        self.assertEqual(res.validation_exit_code, 0)
        self.assertIn("PASS_OK", res.combined_report)
        self.assertIn("return a + b", target_py.read_text(encoding="utf-8"))

        # 2. Broken syntax edit -> blocked by syntax gate before touching disk or running verify_cmd
        bad_res = fuse_edit_and_verify(
            file_path=target_py,
            target="return a + b",
            replacement="return a + (b",
            verify_cmd="echo SHOULD_NOT_RUN",
            cwd=self.tmp_path,
            store=self.store,
        )
        self.assertFalse(bad_res.success)
        self.assertFalse(bad_res.syntax_valid)
        self.assertTrue(bad_res.rolled_back)
        self.assertEqual(bad_res.validation_exit_code, -2)
        # File on disk remains valid
        self.assertIn("return a + b", target_py.read_text(encoding="utf-8"))

    def test_mechanism_2_observation_pack_and_paged_recall(self) -> None:
        # Generate a 16 KB log with a buried error at line 150
        lines = [f"INFO [worker-{i}] Processing batch item {i} successfully..." for i in range(1, 320)]
        lines[149] = 'File "src/engine/processor.py", line 204, in run_batch\nValueError: Invalid tensor dimension 512 != 256'
        big_log = "\n".join(lines)

        packed = self.store.pack(big_log, exit_code=1, command="pytest tests/test_engine.py")
        self.assertTrue(packed.was_packed)
        self.assertIsNotNone(packed.obs_id)
        self.assertLess(packed.transmitted_bytes, packed.raw_bytes * 0.4)
        self.assertIn("ValueError: Invalid tensor dimension 512 != 256", packed.formatted_output)
        self.assertIn("src/engine/processor.py:204", packed.formatted_output)

        # Test Paged Recall: page 2
        page_2 = self.store.read_obs(packed.obs_id, page=2)
        self.assertIn("Page 2/", page_2)
        self.assertIn("L81:", page_2)

        # Test Grep Recall
        grep_out = self.store.read_obs(packed.obs_id, grep_pattern="ValueError")
        self.assertIn("Invalid tensor dimension", grep_out)

        # Test Line Neighborhood Recall
        around_out = self.store.read_obs(packed.obs_id, around_line=150, radius=3)
        self.assertIn("L150:", around_out)

        # Test Deduplication after grace window (seen 3 times)
        self.store.pack(big_log, exit_code=1, command="pytest tests/test_engine.py")
        packed_third = self.store.pack(big_log, exit_code=1, command="pytest tests/test_engine.py")
        self.assertTrue(packed_third.was_deduplicated)
        self.assertIn("100% identical to previously archived", packed_third.formatted_output)

    def test_mechanism_3_evidence_reducer_fail_open_on_hallucination(self) -> None:
        raw_log = "\n".join(
            [f"Compiling file_{i}.c..." for i in range(200)]
            + ['File "src/main.py", line 45, in main', "KeyError: 'missing_user_id'"]
        )
        source_hash = hashlib.sha256(raw_log.encode("utf-8")).hexdigest()[:12]

        # Valid receipt (exact substring)
        valid_receipt = DiagnosticReceipt(
            source_hash=source_hash,
            exit_code=1,
            root_cause_summary="Missing user id key",
            failing_locations=["src/main.py:45"],
            verbatim_quotes=["KeyError: 'missing_user_id'"],
        )
        v_ok = verify_receipt(raw_log, 1, valid_receipt)
        self.assertTrue(v_ok.valid)

        # Hallucinated receipt (even 1 character changed: 'missing_user_id_2')
        hallucinated_receipt = DiagnosticReceipt(
            source_hash=source_hash,
            exit_code=1,
            root_cause_summary="Missing user id key",
            failing_locations=["src/main.py:45"],
            verbatim_quotes=["KeyError: 'missing_user_id_2'"],
        )
        v_bad = verify_receipt(raw_log, 1, hallucinated_receipt)
        self.assertFalse(v_bad.valid)
        self.assertIn("Hallucinated or altered quote", v_bad.reason)

        # Test Fail-Open in pure LLM mode when custom LLM returns a hallucinated quote
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["evidence_reducer"]["mode"] = "llm"
        cfg["evidence_reducer"]["threshold_bytes"] = 1000

        def mock_hallucinating_llm(log_text: str, code: int):
            return {
                "source_hash": source_hash,
                "exit_code": code,
                "root_cause_summary": "Hallucinated summary",
                "failing_locations": ["src/fake.py:999"],
                "verbatim_quotes": ["THIS LINE DOES NOT EXIST IN THE RAW LOG"],
            }

        out_text, was_reduced, failed_open, _ = reduce_log_fail_open(
            raw_log,
            1,
            config=cfg,
            custom_llm_fn=mock_hallucinating_llm,
        )
        self.assertFalse(was_reduced)
        self.assertTrue(failed_open)
        self.assertEqual(out_text, raw_log)

    def test_mechanism_3_secret_redaction(self) -> None:
        log_with_secret = "Error connecting with key sk-proj1234567890abcdefghijklmnop"
        packed = self.store.pack(log_with_secret, exit_code=1)
        self.assertNotIn("sk-proj1234567890", packed.formatted_output)
        self.assertIn("[REDACTED_SECRET]", packed.formatted_output)

    def test_mechanism_4_online_context_compaction(self) -> None:
        compactor = OnlineContextCompactor(store_dir=self.tmp_path / "obs_store")

        # Low token count (10k tokens) -> should defer compaction to keep KV cache warm
        d_low = compactor.evaluate_milestone(
            completed_step="Initial repo exploration",
            current_tokens_est=10000,
            expected_remaining_turns=3,
        )
        self.assertFalse(d_low.should_compact)
        self.assertIn("DEFERRED", d_low.checkpoint_markdown)

        # High token count (145k tokens / 200k limit = 72.5% pressure + positive economic break-even over 8 turns)
        d_high = compactor.evaluate_milestone(
            completed_step="Refactored database migrations and verified schema",
            current_tokens_est=145000,
            expected_remaining_turns=8,
            files_modified=["src/db/migrate.py"],
            obs_handles=["obs_1234567890"],
            key_findings="All 12 migrations applied cleanly",
            next_step="Write API integration tests",
        )
        self.assertTrue(d_high.should_compact)
        self.assertIn("RECOMMENDED NOW", d_high.checkpoint_markdown)
        self.assertIn("src/db/migrate.py", d_high.checkpoint_markdown)

    def test_mechanism_5_auto_research_rsi_loop(self) -> None:
        report = run_auto_research_loop(capability_floor=0.95, temp_store_dir=self.tmp_path / "tune_work", quick=True)
        self.assertGreaterEqual(report["winner_evidence_recall"], 0.95)
        self.assertGreater(report["winner_reduction_percent"], 50.0)
        self.assertTrue(report["validated_on_holdout"])
        self.assertGreaterEqual(report["selected_holdout"]["recall"], 0.95)
        self.assertGreaterEqual(report["selected_holdout"]["worst_family"], 0.85)
        self.assertEqual(len(report["candidates"]), 8)
        # holdout families are never part of the selection corpus
        self.assertGreater(report["corpus"]["holdout_cases"], 0)
        # the tuner must actually discriminate: aggressive settings lose evidence and are rejected
        self.assertTrue(any(not c["passed_floor"] for c in report["candidates"]))
        rej = [c for c in report["candidates"] if "q15_r0" in c["name"]]
        self.assertTrue(rej and all(not c["passed_floor"] for c in rej))

    def test_tune_corpus_is_diverse_and_deterministic(self) -> None:
        from solpi.tune_corpus import generate_corpus, TRAIN_FAMILIES, HOLDOUT_FAMILIES
        a, b = generate_corpus(seed=5), generate_corpus(seed=5)
        self.assertEqual([c["log"] for c in a], [c["log"] for c in b])
        self.assertGreaterEqual(len({c["family"] for c in a}), 16)
        self.assertTrue(set(TRAIN_FAMILIES).isdisjoint(HOLDOUT_FAMILIES))
        for c in a:
            self.assertTrue(c["truth"], c["id"])
            for t in c["truth"]:
                self.assertIn(t.strip(), c["log"], c["id"])  # ground truth really is in the raw log

    def test_reducer_keeps_every_failure_when_context_would_exhaust_budget(self) -> None:
        # 8 failures x (core + 2 context lines each side) > 35 quotes: core lines must win over context
        noise = [f"test_ok_{i} ... ok" for i in range(600)]
        log, truth = list(noise), []
        for k in range(8):
            blk = ["=" * 30, f"FAIL: test_bad_{k}", f'  File "t.py", line {k + 10}, in test_bad_{k}', f"AssertionError: {k} != {k + 1}", "-" * 30]
            log[50 + k * 60:50 + k * 60] = blk
            truth.append(f"AssertionError: {k} != {k + 1}")
        store = ObservationPackStore(store_dir=self.tmp_path / "budget_store")
        out = store.pack("\n".join(log), exit_code=1, command="python3 -m unittest -v").formatted_output
        for t in truth:
            self.assertIn(t, out)
        # failures that use none of the usual keywords are still kept
        silent = "\n".join([f"step {i} ok" for i in range(800)] + ["timed out after 300 seconds waiting for lock"])
        out = store.pack(silent, exit_code=2, command="make test").formatted_output
        self.assertIn("timed out after 300 seconds waiting for lock", out)

    def test_mcp_jsonrpc_server(self) -> None:
        # 1. initialize
        init_resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "sol-pi-harness")

        # 2. tools/list
        list_resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = {t["name"] for t in list_resp["result"]["tools"]}
        self.assertIn("solpi_fuse", tool_names)
        self.assertIn("solpi_run", tool_names)
        self.assertIn("solpi_read_obs", tool_names)
        self.assertIn("solpi_milestone", tool_names)

        # 3. tools/call -> solpi_run
        call_resp = handle_jsonrpc_message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "solpi_run", "arguments": {"cmd": "echo MCP_TEST_HELLO"}},
        })
        self.assertFalse(call_resp["result"]["isError"])
        self.assertIn("MCP_TEST_HELLO", call_resp["result"]["content"][0]["text"])

    def test_lifecycle_hooks_pre_tool_use_and_pre_invocation(self) -> None:
        self.assertTrue(should_wrap_command("pytest tests/"))
        self.assertTrue(should_wrap_command("cargo test --all"))
        self.assertFalse(should_wrap_command("ls -la"))
        self.assertFalse(should_wrap_command("solpi run --cmd 'pytest'"))
        # Compound / piped / redirected commands must never be rewritten or auto-approved
        for risky in ("pytest | tail", "pytest; rm -rf x", "cd x && pytest", "pytest > out.txt",
                      "pytest $(whoami)", "pytest `id`", "pytest\nrm x", "FOO=1 pytest"):
            self.assertFalse(should_wrap_command(risky), risky)
        # a filename merely containing "solpi" is not an excuse to skip wrapping
        self.assertTrue(should_wrap_command("pytest tests/test_solpi.py"))

        # AGY wire format
        hook_out = dispatch_hook(
            "PreToolUse",
            {"toolCall": {"name": "run_command", "args": {"CommandLine": "pytest -v tests/"}}},
            solpi_bin="solpi",
        )
        self.assertEqual(hook_out["decision"], "allow")
        self.assertIn("solpi run --cmd", hook_out["overwrite"]["CommandLine"])

        # Claude Code / Codex wire format: updatedInput keeps sibling fields
        cc = dispatch_hook(
            "PreToolUse",
            {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "pytest -q", "timeout": 5000, "description": "t"}},
            solpi_bin="solpi",
        )
        spec = cc["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["permissionDecision"], "allow")
        self.assertEqual(spec["updatedInput"]["command"], "solpi run --cmd 'pytest -q'")
        self.assertEqual(spec["updatedInput"]["timeout"], 5000)
        self.assertEqual(spec["updatedInput"]["description"], "t")
        # non-matching command / other tool -> empty reply (no decision, normal permission flow)
        self.assertEqual(dispatch_hook("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}), {})
        self.assertEqual(dispatch_hook("PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "x"}}), {})

        # UserPromptSubmit context-pressure notice (Claude Code / Codex)
        big = self.tmp_path / "big_transcript.jsonl"
        big.write_text("x" * (4 * 200000), encoding="utf-8")
        ups = dispatch_hook("UserPromptSubmit", {"transcript_path": str(big)})
        self.assertIn("Compaction Notice", ups["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(dispatch_hook("UserPromptSubmit", {"transcript_path": "/nonexistent"}), {})

    def test_secret_redaction_covers_common_credentials_without_failing_open(self) -> None:
        from solpi.reducer import scan_and_redact_secrets
        secrets = ["AKIAIOSFODNN7EXAMPLE", "hunter2hunter2", "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
                   "MIIEowIBAAKCAQEA", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"]
        text = (
            "aws_access_key_id=AKIAIOSFODNN7EXAMPLE\npassword=hunter2hunter2\n" + secrets[2] + "\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"
            "jwt " + secrets[4] + "\nsrc/auth.py:12: TypeError: token is None\n"
        )
        out, n = scan_and_redact_secrets(text)
        for sec in secrets:
            self.assertNotIn(sec, out)
        self.assertIn("src/auth.py:12: TypeError: token is None", out)  # diagnostics untouched
        self.assertEqual(scan_and_redact_secrets(out)[1], 0)  # idempotent: no re-trigger on redacted text
        # end-to-end: big log with secrets is packed, reduced (not failed-open) and stored redacted
        big = "\n".join(f"INFO {i} ok" for i in range(2000)) + "\npassword=hunter2hunter2\nERROR a/b.py:9 boom\n"
        store = ObservationPackStore(store_dir=self.tmp_path / "secret_store")
        res = store.pack(big, exit_code=1, command="pytest -q")
        self.assertTrue(res.was_reduced and not res.failed_open)
        self.assertNotIn("hunter2hunter2", (self.tmp_path / "secret_store" / f"{res.obs_id}.log").read_text())

    def test_reducer_only_for_build_test_commands_per_paper(self) -> None:
        from solpi.reducer import is_reducible_command
        for c in ("pytest -q", "cd app && npm test", "cargo build --release", "make test", "python3 -m unittest -v"):
            self.assertTrue(is_reducible_command(c), c)
        for c in ("cat big.log", "grep -rn foo src", "rg TODO", "find . -name '*.py'", "git log", "ls -R"):
            self.assertFalse(is_reducible_command(c), c)
        store = ObservationPackStore(store_dir=self.tmp_path / "bypass_store")
        # 6 KiB search result: below the 10 KiB ObservationPack threshold -> untouched, no reducer
        mid = "\n".join(f"src/a{i}.py:{i}:def f{i}(): error_handler()" for i in range(180))
        self.assertTrue(4096 < len(mid) < 10240)
        res = store.pack(mid, exit_code=0, command="grep -rn error src")
        self.assertFalse(res.was_packed)
        self.assertEqual(res.formatted_output, mid)
        # same size from a test command IS reduced/packed (>= 4 KiB)
        res = store.pack(mid, exit_code=1, command="pytest -q")
        self.assertTrue(res.was_packed)
        # 40 KiB search result: archived with handle + head/tail excerpt, but no reducer quotes
        big = "\n".join(f"src/a{i}.py:{i}:def f{i}(): error_handler()" for i in range(1200))
        res = store.pack(big, exit_code=0, command="grep -rn error src")
        self.assertTrue(res.was_packed and not res.was_reduced)
        self.assertNotIn("VERIFIED VERBATIM QUOTES", res.formatted_output)

    def test_compaction_estimates_requests_and_raises_margin_after_first_compaction(self) -> None:
        comp = OnlineContextCompactor(store_dir=self.tmp_path / "comp_store")
        d1 = comp.evaluate_milestone("step1", current_tokens_est=80_000, unfinished_steps=4,
                                     requests_since_last_milestone=6)
        self.assertEqual(d1.remaining_turns_est, 24)
        self.assertTrue(d1.should_compact)  # 6 req/step * 4 steps = 24 req: savings beat rewrite cost
        # immediately after, same size: unrecovered rewrite cost + larger margin must block a re-compaction
        d2 = comp.evaluate_milestone("step2", current_tokens_est=80_000, unfinished_steps=1,
                                     requests_since_last_milestone=1)
        self.assertFalse(d2.should_compact)
        self.assertLess(d2.net_benefit_usd, d1.net_benefit_usd)
        # window-growth cap: 68k tokens over 8 requests (8.5k/req) -> only 8 more requests fit in the window
        comp3 = OnlineContextCompactor(store_dir=self.tmp_path / "comp_store3")
        comp3.evaluate_milestone("a", current_tokens_est=60_000, unfinished_steps=10, requests_since_last_milestone=1)
        d3 = comp3.evaluate_milestone("b", current_tokens_est=128_000, unfinished_steps=10,
                                      requests_since_last_milestone=7)
        self.assertEqual(d3.remaining_turns_est, 8)  # uncapped estimate would be 4 req/step * 10 = 40

    def test_reproducible_benchmark_passes_its_regression_guards(self) -> None:
        import subprocess
        script = Path(__file__).resolve().parent.parent / "benchmarks" / "run_benchmark.py"
        proc = subprocess.run([sys.executable, str(script), "--check", "--json"], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp_path)})
        self.assertEqual(proc.returncode, 0, msg=proc.stdout[-800:] + proc.stderr[-800:])
        rep = json.loads(proc.stdout)
        self.assertEqual(rep["failures"], [])
        self.assertGreaterEqual(rep["summary"]["reduction_pct"], 80)

    def test_tune_is_repeatable_across_runs(self) -> None:
        first = run_auto_research_loop(capability_floor=0.95, quick=True)
        second = run_auto_research_loop(capability_floor=0.95, quick=True)
        self.assertEqual(first["winner"], second["winner"])
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertGreaterEqual(second["winner_evidence_recall"], 0.95)

    def test_hook_runner_fails_open_on_garbage_stdin(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "solpi.hook_runner", "PreToolUse"],
            input="{not json", text=True, capture_output=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp_path), "PYTHONPATH": str(SRC_DIR)},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {})

    def test_multi_harness_installer(self) -> None:
        import subprocess
        ws_dir = self.tmp_path / "mock workspace"  # space on purpose: paths must be quoted
        ws_dir.mkdir(parents=True, exist_ok=True)
        actions = install_for_target("all", scope="workspace", workspace_dir=ws_dir, dry_run=False)
        self.assertFalse([a for a in actions if a.startswith("[ERROR]")], actions)
        for rel in (
            ".agents/skills/sol-pi-efficiency/SKILL.md",
            ".agents/skills/sol-pi-efficiency/scripts/vendor/solpi/cli.py",
            ".agents/mcp_config.json", ".agents/hooks.json",
            ".claude/skills/sol-pi-efficiency/SKILL.md", ".claude/settings.local.json", ".mcp.json",
            ".codex/config.toml", ".codex/hooks.json", "AGENTS.md",
            ".opencode/skills/sol-pi-efficiency/SKILL.md", "opencode.json", ".cursor/mcp.json",
        ):
            self.assertTrue((ws_dir / rel).is_file(), rel)
        self.assertIn("alwaysApply: true", (ws_dir / ".cursor/rules/sol-pi.mdc").read_text())

        # Claude Code settings: hook command must actually run and produce updatedInput
        settings = json.loads((ws_dir / ".claude/settings.local.json").read_text())
        pre = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(pre["matcher"], "Bash")
        hook_cmd = pre["hooks"][0]["command"]
        proc = subprocess.run(
            hook_cmd, shell=True, text=True, capture_output=True, cwd=str(ws_dir),
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}),
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp_path)},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        wrapped = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("-m solpi.cli run --cmd 'pytest -q'", wrapped)
        self.assertEqual(json.loads((ws_dir / ".codex/hooks.json").read_text())["hooks"]["PreToolUse"][0]["matcher"], "^Bash$")

        # Codex TOML + OpenCode JSON shape
        toml = (ws_dir / ".codex/config.toml").read_text()
        self.assertIn("[mcp_servers.sol-pi]", toml)
        self.assertIn("[mcp_servers.sol-pi.env]", toml)
        oc = json.loads((ws_dir / "opencode.json").read_text())
        server = (oc["mcp"].get("servers") or oc["mcp"])["sol-pi"]
        self.assertEqual(server["type"], "local")
        self.assertEqual(server["command"][1:], ["-m", "solpi.mcp_server"])

        # Idempotent: second run changes nothing and creates no duplicate hooks/blocks
        snapshot = {p: p.read_text() for p in ws_dir.rglob("*") if p.is_file() and p.suffix in (".json", ".toml", ".md")}
        install_for_target("all", scope="workspace", workspace_dir=ws_dir, dry_run=False)
        for p, txt in snapshot.items():
            self.assertEqual(p.read_text(), txt, f"not idempotent: {p}")
        self.assertEqual((ws_dir / "AGENTS.md").read_text().count("sol-pi (managed"), 1)

        # Verify the installed skill script works standalone in a clean subprocess without PYTHONPATH
        installed_script = ws_dir / ".agents" / "skills" / "sol-pi-efficiency" / "scripts" / "solpi_tool.py"
        proc = subprocess.run(
            [sys.executable, str(installed_script), "stats"],
            cwd=str(ws_dir),
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp_path)},
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, msg=f"Standalone skill script failed: {proc.stderr}")
        self.assertIn("fused_actions_count", json.loads(proc.stdout))

    def test_installer_never_clobbers_or_loses_user_config(self) -> None:
        ws = self.tmp_path / "ws_user_cfg"
        ws.mkdir()
        # 1. corrupt user file is refused, left byte-identical, and other targets still install
        bad = ws / ".mcp.json"
        bad.write_text('{"mcpServers": {"mine": {"command": "x"}', encoding="utf-8")
        acts = install_for_target("claude", workspace_dir=ws)
        self.assertTrue(any(a.startswith("[ERROR]") and ".mcp.json" in a for a in acts), acts)
        self.assertEqual(bad.read_text(), '{"mcpServers": {"mine": {"command": "x"}')
        # 2. valid user data is preserved + one-time backup
        bad.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}, "other": 1}), encoding="utf-8")
        install_for_target("claude", workspace_dir=ws)
        merged = json.loads(bad.read_text())
        self.assertEqual(merged["mcpServers"]["mine"], {"command": "x"})
        self.assertIn("sol-pi", merged["mcpServers"])
        self.assertEqual(merged["other"], 1)
        self.assertTrue((ws / ".mcp.json.solpi.bak").is_file())
        # 3. user's own Claude hooks + Codex TOML content survive
        sl = ws / ".claude" / "settings.local.json"
        sl.parent.mkdir(exist_ok=True)
        sl.write_text(json.dumps({"permissions": {"allow": ["Bash(ls:*)"]},
                                  "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "echo hi"}]}]}}))
        (ws / ".codex").mkdir()
        (ws / ".codex" / "config.toml").write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "o"\n')
        install_for_target("claude", workspace_dir=ws)
        install_for_target("codex", workspace_dir=ws)
        s = json.loads(sl.read_text())
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertEqual(len(s["hooks"]["PreToolUse"]), 2)
        toml = (ws / ".codex" / "config.toml").read_text()
        self.assertTrue(toml.startswith('model = "gpt-5"'))
        self.assertIn("[mcp_servers.other]", toml)
        # 4. user-defined [mcp_servers.sol-pi] is not duplicated (would be invalid TOML)
        (ws / ".codex" / "config.toml").write_text('[mcp_servers.sol-pi]\ncommand = "mine"\n')
        acts = install_for_target("codex", workspace_dir=ws)
        self.assertTrue(any(a.startswith("[ERROR]") for a in acts), acts)
        # 5. OpenCode v1 vs v2 layouts follow the existing file
        v1 = ws / "oc1"; v1.mkdir()
        (v1 / "opencode.json").write_text(json.dumps({"mcp": {"x": {"type": "local", "command": ["x"]}}}))
        install_for_target("opencode", workspace_dir=v1)
        j = json.loads((v1 / "opencode.json").read_text())
        self.assertIn("sol-pi", j["mcp"]); self.assertTrue(j["mcp"]["sol-pi"]["enabled"])
        v2 = ws / "oc2"; v2.mkdir()
        (v2 / "opencode.json").write_text(json.dumps({"mcp": {"servers": {"x": {"type": "local", "command": ["x"]}}}}))
        install_for_target("opencode", workspace_dir=v2)
        j = json.loads((v2 / "opencode.json").read_text())
        self.assertIn("sol-pi", j["mcp"]["servers"]); self.assertNotIn("sol-pi", j["mcp"])
        # 6. dry-run writes nothing
        dr = self.tmp_path / "ws_dry"; dr.mkdir()
        install_for_target("all", workspace_dir=dr, dry_run=True)
        self.assertEqual(list(dr.iterdir()), [])

    def test_end_to_end_stdio_mcp_and_hook_processes(self) -> None:
        import subprocess
        # 1. Spawn real MCP stdio process and exchange JSON-RPC messages over pipes
        requests = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/list"}),
            json.dumps({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "solpi_telemetry", "arguments": {}},
            }),
        ]) + "\n"
        mcp_proc = subprocess.run(
            [sys.executable, "-m", "solpi.mcp_server"],
            input=requests,
            cwd=str(REPO_ROOT),
            env={"PYTHONPATH": str(SRC_DIR), "HOME": str(self.tmp_path)},
            text=True,
            capture_output=True,
        )
        self.assertEqual(mcp_proc.returncode, 0, msg=mcp_proc.stderr)
        resp_lines = [json.loads(line) for line in mcp_proc.stdout.strip().splitlines()]
        self.assertEqual(len(resp_lines), 3)  # notification produces no response
        self.assertEqual(resp_lines[0]["id"], 10)
        self.assertEqual(resp_lines[1]["id"], 11)
        self.assertEqual(resp_lines[2]["id"], 12)

        # 2. Spawn real Hook Runner process for PreInvocation with large transcript
        fake_transcript = self.tmp_path / "transcript.jsonl"
        fake_transcript.write_text("x" * (150000 * 4), encoding="utf-8")  # ~150k estimated tokens (75% of 200k)
        hook_input = json.dumps({"transcriptPath": str(fake_transcript)})
        hook_proc = subprocess.run(
            [sys.executable, "-m", "solpi.hook_runner", "PreInvocation"],
            input=hook_input,
            cwd=str(REPO_ROOT),
            env={"PYTHONPATH": str(SRC_DIR), "HOME": str(self.tmp_path)},
            text=True,
            capture_output=True,
        )
        self.assertEqual(hook_proc.returncode, 0, msg=hook_proc.stderr)
        hook_resp = json.loads(hook_proc.stdout)
        self.assertIn("injectSteps", hook_resp)
        self.assertIn("Online Context Compaction Notice", hook_resp["injectSteps"][0]["ephemeralMessage"])

    def test_official_nvlabs_sol_pi_json_compatibility(self) -> None:
        official_cfg_file = self.tmp_path / "official_sol_pi.json"
        official_cfg_file.write_text(
            json.dumps({
                "version": 1,
                "actionFusion": True,
                "observationPack": True,
                "evidencePreservingReducer": False,
                "onlineContextCompact": False,
                "cacheWriteReadRatio": 12.5,
            }),
            encoding="utf-8",
        )
        loaded = load_config(official_cfg_file)
        self.assertTrue(loaded["action_fusion"]["enabled"])
        self.assertTrue(loaded["observation_pack"]["enabled"])
        self.assertFalse(loaded["evidence_reducer"]["enabled"])
        self.assertFalse(loaded["online_compaction"]["enabled"])
        self.assertAlmostEqual(loaded["online_compaction"]["cache_write_cost_per_mtok"], 0.30 * 12.5)


if __name__ == "__main__":
    unittest.main()
