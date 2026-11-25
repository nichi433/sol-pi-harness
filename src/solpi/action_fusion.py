"""Mechanism 1: Action Fusion (Atomic File Mutation + Immediate Validation Command).

Implements arXiv:2609.20519 Section 3.1:
- Combines a file edit (exact string replacement or full write) with its follow-up
  validation command (e.g., `pytest`, `cargo check`, `tsc`, `ruff`) in a SINGLE tool call.
- Performs an optional zero-cost syntax pre-check (e.g. `ast.parse` for Python, `json.loads` for JSON)
  and automatically rolls back if syntax fails (when `revert_on_syntax_error=True`).
- Pipes the validation output directly through Mechanism 2 (`ObservationPack`) and
  Mechanism 3 (`Evidence-Preserving Reducer`) so the agent gets both the edit confirmation
  and the verified diagnostic receipt in one turn.
"""

from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import load_config
from .observation_pack import ObservationPackStore, PackedObservationResult


@dataclass
class ActionFusionResult:
    success: bool
    edit_applied: bool
    syntax_valid: bool
    rolled_back: bool
    validation_exit_code: int
    obs_id: Optional[str]
    combined_report: str
    packed_observation: Optional[PackedObservationResult] = None


def check_file_syntax(file_path: Path, content: str) -> Tuple[bool, str]:
    """Fast deterministic syntax pre-check for common file types before running heavy test suites."""
    suffix = file_path.suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(content, filename=str(file_path))
            return True, "Python AST syntax OK"
        except SyntaxError as err:
            return False, f"SyntaxError at line {err.lineno}, col {err.offset}: {err.msg}"
    elif suffix == ".json":
        try:
            json.loads(content)
            return True, "JSON syntax OK"
        except json.JSONDecodeError as err:
            return False, f"JSONDecodeError at line {err.lineno}, col {err.colno}: {err.msg}"
    return True, "No pre-check registered for extension"


def fuse_edit_and_verify(
    file_path: str | Path,
    verify_cmd: str,
    *,
    target: Optional[str] = None,
    replacement: Optional[str] = None,
    edits: Optional[List[Dict[str, str]]] = None,
    full_content: Optional[str] = None,
    cwd: Optional[str | Path] = None,
    timeout_sec: Optional[int] = None,
    revert_on_test_failure: bool = False,
    config: Optional[Dict[str, Any]] = None,
    store: Optional[ObservationPackStore] = None,
) -> ActionFusionResult:
    """Execute a file mutation and its immediate validation command in a single turn."""
    cfg = config or load_config()
    fusion_cfg = cfg.get("action_fusion", {})
    obs_store = store or ObservationPackStore(config=cfg)
    path = Path(file_path).expanduser().resolve()

    existed_before = path.is_file()
    original_content = path.read_text(encoding="utf-8") if existed_before else ""

    # 1. Compute new content
    if full_content is not None:
        new_content = full_content
        edit_summary = f"Wrote full file {path.name} ({len(new_content)} chars)"
    elif edits:
        new_content = original_content
        for idx, item in enumerate(edits):
            t_str = item.get("target", "")
            r_str = item.get("replacement", "")
            if not t_str or t_str not in new_content:
                return ActionFusionResult(
                    success=False,
                    edit_applied=False,
                    syntax_valid=True,
                    rolled_back=False,
                    validation_exit_code=-1,
                    obs_id=None,
                    combined_report=f"[SoL-Pi ActionFusion Error] Target hunk #{idx+1} not found in {path}. Validation command skipped.",
                )
            new_content = new_content.replace(t_str, r_str, 1)
        edit_summary = f"Applied {len(edits)} edit hunk(s) to {path.name}"
    elif target is not None and replacement is not None:
        if not existed_before:
            return ActionFusionResult(
                success=False,
                edit_applied=False,
                syntax_valid=True,
                rolled_back=False,
                validation_exit_code=-1,
                obs_id=None,
                combined_report=f"[SoL-Pi ActionFusion Error] File {path} does not exist. Validation command skipped.",
            )
        if target not in original_content:
            return ActionFusionResult(
                success=False,
                edit_applied=False,
                syntax_valid=True,
                rolled_back=False,
                validation_exit_code=-1,
                obs_id=None,
                combined_report=f"[SoL-Pi ActionFusion Error] Exact target string not found in {path}. Validation command skipped.",
            )
        new_content = original_content.replace(target, replacement, 1)
        edit_summary = f"Replaced target block ({len(target)} -> {len(replacement)} chars) in {path.name}"
    else:
        return ActionFusionResult(
            success=False,
            edit_applied=False,
            syntax_valid=True,
            rolled_back=False,
            validation_exit_code=-1,
            obs_id=None,
            combined_report="[SoL-Pi ActionFusion Error] Must provide (target & replacement), edits, or full_content.",
        )

    # 2. Optional syntax pre-check before touching disk or running slow test command
    if fusion_cfg.get("syntax_precheck", True):
        syntax_ok, syntax_msg = check_file_syntax(path, new_content)
        if not syntax_ok and fusion_cfg.get("revert_on_syntax_error", True):
            obs_store.telemetry.record(fused_action=True, avoided_round_trip=True)
            return ActionFusionResult(
                success=False,
                edit_applied=False,
                syntax_valid=False,
                rolled_back=True,
                validation_exit_code=-2,
                obs_id=None,
                combined_report=(
                    f"[SoL-Pi ActionFusion Syntax Gate Failed]\n"
                    f"Edit to `{path.name}` introduced a syntax error ({syntax_msg}).\n"
                    f"File was NOT modified and validation command `{verify_cmd}` was skipped."
                ),
            )

    # 3. Write mutated file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")

    # 4. Run validation command immediately in the same turn
    timeout = timeout_sec or int(fusion_cfg.get("default_timeout_sec", 120))
    work_dir = Path(cwd).expanduser().resolve() if cwd else path.parent
    try:
        proc = subprocess.run(
            verify_cmd,
            shell=True,
            cwd=str(work_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        exit_code = proc.returncode
        raw_out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        out_part = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err_part = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raw_out = f"[Timeout after {timeout}s]\n{out_part}\n{err_part}"

    rolled_back = False
    if exit_code != 0 and revert_on_test_failure:
        if existed_before:
            path.write_text(original_content, encoding="utf-8")
        elif path.exists():
            path.unlink()
        rolled_back = True

    # 5. Pack & reduce validation output
    packed = obs_store.pack(raw_out.strip(), exit_code=exit_code, command=verify_cmd)
    obs_store.telemetry.record(fused_action=True, avoided_round_trip=True)

    rollback_note = " (REVERTED file due to validation failure)" if rolled_back else ""
    header = (
        f"[SoL-Pi ActionFusion Turn | {edit_summary}{rollback_note} | "
        f"Verify: `{verify_cmd}` -> Exit {exit_code}]"
    )
    report = f"{header}\n{packed.formatted_output}"

    return ActionFusionResult(
        success=(exit_code == 0),
        edit_applied=(not rolled_back),
        syntax_valid=True,
        rolled_back=rolled_back,
        validation_exit_code=exit_code,
        obs_id=packed.obs_id,
        combined_report=report,
        packed_observation=packed,
    )
