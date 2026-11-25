"""Unified CLI (`solpi`) for the SoL-Pi Multi-Harness Toolkit.

Subcommands:
- `solpi fuse`: Mechanism 1 (Action Fusion: Edit file + run validation command in 1 turn).
- `solpi run`: Mechanism 2 & 3 (Run command with ObservationPack & Evidence-Preserving Reducer).
- `solpi read`: Mechanism 2 (Paged recall, line range, or regex search over `obs_<id>`).
- `solpi milestone`: Mechanism 4 (Online Context Compaction economic evaluator & state receipt).
- `solpi tune`: Mechanism 5 (Auto-Research RSI loop to calibrate thresholds).
- `solpi stats`: Show session token/byte savings and fail-open counters.
- `solpi install`: One-command installer for AGY, Claude Code, Codex, OpenCode, and Cursor.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .action_fusion import fuse_edit_and_verify
from .auto_research import run_auto_research_loop
from .compactor import OnlineContextCompactor
from .config import load_config
from .installer import install_for_target
from .observation_pack import ObservationPackStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="solpi",
        description="SoL-Pi (arXiv:2609.20519) & Harness2 Multi-Harness Efficiency Toolkit",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # 1. fuse
    p_fuse = sub.add_parser("fuse", help="Action Fusion: Atomically edit file and run validation command in 1 turn")
    p_fuse.add_argument("--file", required=True, help="Target file path to modify")
    p_fuse.add_argument("--target", required=True, help="Exact substring to replace")
    p_fuse.add_argument("--replacement", required=True, help="Replacement string")
    p_fuse.add_argument("--cmd", required=True, help="Immediate validation shell command (e.g. 'pytest')")
    p_fuse.add_argument("--cwd", help="Optional working directory for command")
    p_fuse.add_argument("--revert-on-fail", action="store_true", help="Revert file if validation command fails")

    # 2. run
    p_run = sub.add_parser("run", help="ObservationPack + Evidence-Preserving Reducer command runner")
    p_run.add_argument("--cmd", required=True, help="Shell command to execute")
    p_run.add_argument("--cwd", help="Optional working directory")
    p_run.add_argument("--timeout", type=int, default=120, help="Timeout in seconds (default: 120)")

    # 3. read
    p_read = sub.add_parser("read", help="Paged recall or regex search over an archived ObservationPack handle")
    p_read.add_argument("obs_id", help="ObservationPack handle ID (e.g. obs_8f3a1b2c9d)")
    p_read.add_argument("--page", type=int, default=1, help="1-based page number (default: 1)")
    p_read.add_argument("--page-size", type=int, help="Lines per page override")
    p_read.add_argument("--grep", help="Regex pattern to filter matching lines")
    p_read.add_argument("--around-line", type=int, help="Inspect neighborhood around specific line number")
    p_read.add_argument("--radius", type=int, default=20, help="Radius around --around-line (default: 20)")

    # 4. milestone
    p_mile = sub.add_parser("milestone", help="Online Context Compaction economic evaluator & state checkpoint")
    p_mile.add_argument("--step", required=True, help="Completed subtask / plan step description")
    p_mile.add_argument("--tokens", type=int, required=True, help="Estimated active context tokens")
    p_mile.add_argument("--remaining-turns", type=int, default=None, help="Remaining model requests (default: estimated from --unfinished-steps and observed history)")
    p_mile.add_argument("--unfinished-steps", type=int, default=None, help="Plan steps still to do (enables automatic estimation)")
    p_mile.add_argument("--requests-since-last", type=int, default=None, help="Model requests spent since the previous milestone")
    p_mile.add_argument("--files", nargs="*", default=[], help="Modified files in this step")
    p_mile.add_argument("--obs", nargs="*", default=[], help="Relevant obs_* handles")
    p_mile.add_argument("--findings", default="", help="Verified key findings from this milestone")
    p_mile.add_argument("--next-step", default="", help="Next subtask to start")

    # 5. tune
    p_tune = sub.add_parser("tune", help="Run the SoL-Pi / Harness2 Auto-Research RSI loop")
    p_tune.add_argument("--floor", type=float, default=0.95, help="Capability floor for evidence recall (default: 0.95)")
    p_tune.add_argument("--real", action="store_true", help="Also validate on REAL tool runs (gcc, g++, cargo, python, node) when installed")
    p_tune.add_argument("--quick", action="store_true", help="Smaller candidate grid (8 instead of 36)")
    p_tune.add_argument("--seed", type=int, default=1337, help="Corpus seed (default: 1337)")
    p_tune.add_argument("--verbose", action="store_true", help="Include every candidate in the printed report")
    p_tune.add_argument("--write-config", help="Optional path to save winning sol-pi.json")

    # 6. stats
    p_stats = sub.add_parser("stats", help="Show session token/byte savings and fail-open statistics")
    p_stats.add_argument("--reset", action="store_true", help="Reset telemetry counters after printing")

    # 7. install
    p_inst = sub.add_parser("install", help="Install SoL-Pi Skill, MCP, Hooks, and Rules into agent harnesses")
    p_inst.add_argument(
        "--target",
        choices=["agy", "claude", "codex", "opencode", "cursor", "all"],
        default="all",
        help="Target agent harness (default: all)",
    )
    p_inst.add_argument(
        "--scope",
        choices=["workspace", "global"],
        default="workspace",
        help="Install into current workspace or user home global config (default: workspace)",
    )
    p_inst.add_argument("--workspace", help="Explicit workspace directory path")
    p_inst.add_argument(
        "--opencode-schema",
        choices=["auto", "v1", "v2"],
        default="auto",
        help="OpenCode config schema: v1 `mcp.<name>` or v2 `mcp.servers.<name>` (default: auto-detect)",
    )
    p_inst.add_argument(
        "--hook-only",
        action="store_true",
        help="Lean install: only the PreToolUse hook + an MCP server exposing just solpi_read_obs "
        "(~140 tokens/request instead of ~735). Skips skill, rules and the compaction notice; "
        "skipped for OpenCode/Cursor (no hook API).",
    )
    p_inst.add_argument(
        "--hook-mode",
        choices=["safe", "prefixed"],
        default=None,
        help="Which commands the hook rewrites: safe = one simple build/test command; prefixed = also "
        "`cd <dir> && cmd` and a few harmless `VAR=val` prefixes (default: prefixed)",
    )
    p_inst.add_argument("--dry-run", action="store_true", help="Print actions without writing files")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    store = ObservationPackStore(config=cfg)

    if args.subcommand == "fuse":
        res = fuse_edit_and_verify(
            file_path=args.file,
            verify_cmd=args.cmd,
            target=args.target,
            replacement=args.replacement,
            cwd=args.cwd,
            revert_on_test_failure=args.revert_on_fail,
            config=cfg,
            store=store,
        )
        print(res.combined_report)
        return 0 if res.edit_applied else 1

    elif args.subcommand == "run":
        cwd = args.cwd or str(Path.cwd())
        try:
            proc = subprocess.run(
                args.cmd,
                shell=True,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=args.timeout,
            )
            exit_code = proc.returncode
            raw_out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            raw_out = f"[Timeout after {args.timeout}s]\n{exc.stdout or ''}\n{exc.stderr or ''}"

        packed = store.pack(raw_out.strip(), exit_code=exit_code, command=args.cmd)
        print(packed.formatted_output)
        return exit_code

    elif args.subcommand == "read":
        out = store.read_obs(
            args.obs_id,
            page=args.page,
            page_size=args.page_size,
            grep_pattern=args.grep,
            around_line=args.around_line,
            radius=args.radius,
        )
        print(out)
        return 0

    elif args.subcommand == "milestone":
        compactor = OnlineContextCompactor(config=cfg, store_dir=store.store_dir)
        decision = compactor.evaluate_milestone(
            completed_step=args.step,
            current_tokens_est=args.tokens,
            expected_remaining_turns=args.remaining_turns,
            unfinished_steps=args.unfinished_steps,
            requests_since_last_milestone=args.requests_since_last,
            files_modified=args.files,
            obs_handles=args.obs,
            key_findings=args.findings,
            next_step=args.next_step,
        )
        print(decision.checkpoint_markdown)
        return 0

    elif args.subcommand == "tune":
        report = run_auto_research_loop(
            capability_floor=args.floor, base_config=cfg, include_real=args.real, seed=args.seed, quick=args.quick
        )
        if args.write_config:
            out_path = Path(args.write_config).expanduser().resolve()
            out_path.write_text(json.dumps(report["recommended_config"], indent=2) + "\n", encoding="utf-8")
        shown = report if args.verbose else {k: v for k, v in report.items() if k not in ("candidates", "recommended_config")}
        if not args.verbose:
            shown["candidates_passed_floor"] = f"{sum(c['passed_floor'] for c in report['candidates'])}/{len(report['candidates'])}"
        print(json.dumps(shown, indent=2))
        return 0

    elif args.subcommand == "stats":
        summary = store.telemetry.summary()
        print(json.dumps(summary, indent=2))
        if args.reset:
            store.telemetry.reset()
        return 0

    elif args.subcommand == "install":
        ws = Path(args.workspace).expanduser().resolve() if args.workspace else None
        actions = install_for_target(
            args.target,
            scope=args.scope,
            workspace_dir=ws,
            dry_run=args.dry_run,
            opencode_schema=args.opencode_schema,
            hook_only=args.hook_only,
            hook_mode=args.hook_mode,
        )
        prefix = "[DRY-RUN] " if args.dry_run else "[OK] "
        failed = False
        for act in actions:
            if act.startswith("[ERROR]"):
                failed = True
                print(act, file=sys.stderr)
            else:
                print(f"{prefix}{act}")
        return 1 if failed else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
