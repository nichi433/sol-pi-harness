"""Mechanism 2: ObservationPack (Content-Addressed Observation Store & Paged Recall).

Implements arXiv:2609.20519 Section 3.2:
- Archives large command/tool outputs (> 10 KiB or reducer threshold) in a Content-Addressed Store (CAS)
  indexed by `obs_<10-char-sha256>`.
- Deduplicates identical consecutive/repeated outputs (e.g., running the same verbose test command twice).
- Combines seamlessly with Mechanism 3 (`Evidence-Preserving Reducer`) to return a compact ~1 KB handle
  containing Head, Verified Verbatim Evidence, Tail, and exact Paged Recall instructions.
- Supports `read_page()`, `search_obs()`, and `read_around_line()` so no evidence is ever permanently lost.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import TelemetryTracker, get_store_dir, load_config
from .reducer import is_reducible_command, reduce_log_fail_open, scan_and_redact_secrets


@dataclass
class PackedObservationResult:
    obs_id: Optional[str]
    formatted_output: str
    raw_bytes: int
    transmitted_bytes: int
    was_packed: bool
    was_reduced: bool
    was_deduplicated: bool
    failed_open: bool
    exit_code: int


MAX_EXCERPT_LINE = 300


def _clip(line: str) -> str:
    """Excerpt lines are shown, not quoted: cap them so one giant line (minified JSON, base64...) cannot defeat the pack."""
    return line if len(line) <= MAX_EXCERPT_LINE else f"{line[:MAX_EXCERPT_LINE]} [...+{len(line) - MAX_EXCERPT_LINE} chars]"


class ObservationPackStore:
    """Manages content-addressed observation storage, deduplication, and paged recall."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or load_config()
        self.pack_cfg = self.config.get("observation_pack", {})
        self.reducer_cfg = self.config.get("evidence_reducer", {})
        self.store_dir = store_dir or get_store_dir(self.config)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.telemetry = TelemetryTracker(self.store_dir)

    def _compute_obs_id(self, content: str) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
        return f"obs_{digest}"

    def _meta_path(self, obs_id: str) -> Path:
        return self.store_dir / f"{obs_id}.meta.json"

    def _log_path(self, obs_id: str) -> Path:
        return self.store_dir / f"{obs_id}.log"

    def pack(
        self,
        raw_output: str,
        exit_code: int = 0,
        command: str = "",
        custom_llm_fn: Optional[Callable[[str, int], Dict[str, Any]]] = None,
    ) -> PackedObservationResult:
        """Process a command or tool observation through ObservationPack & Evidence Reducer."""
        if not self.pack_cfg.get("enabled", True):
            b_len = len(raw_output.encode("utf-8"))
            return PackedObservationResult(
                obs_id=None,
                formatted_output=raw_output,
                raw_bytes=b_len,
                transmitted_bytes=b_len,
                was_packed=False,
                was_reduced=False,
                was_deduplicated=False,
                failed_open=False,
                exit_code=exit_code,
            )

        # Redact secrets early if configured
        working_output = raw_output
        secrets_redacted = 0
        if self.reducer_cfg.get("redact_secrets", True):
            working_output, secrets_redacted = scan_and_redact_secrets(raw_output)

        raw_bytes = len(working_output.encode("utf-8"))
        pack_threshold = int(self.pack_cfg.get("threshold_bytes", 10240))
        reducer_threshold = int(self.reducer_cfg.get("threshold_bytes", 4096))
        # Paper: the reducer handles build/test logs >= 4 KiB; every other large result (file reads,
        # searches, arbitrary commands) is only archived above the 10 KiB ObservationPack threshold.
        reducible = is_reducible_command(command, self.reducer_cfg)
        effective_threshold = min(pack_threshold, reducer_threshold) if reducible else pack_threshold

        if raw_bytes < effective_threshold:
            self.telemetry.record(
                raw_bytes=raw_bytes,
                transmitted_bytes=raw_bytes,
                secrets_redacted=secrets_redacted,
            )
            return PackedObservationResult(
                obs_id=None,
                formatted_output=working_output,
                raw_bytes=raw_bytes,
                transmitted_bytes=raw_bytes,
                was_packed=False,
                was_reduced=False,
                was_deduplicated=False,
                failed_open=False,
                exit_code=exit_code,
            )

        obs_id = self._compute_obs_id(working_output)
        log_file = self._log_path(obs_id)
        meta_file = self._meta_path(obs_id)
        lines = working_output.splitlines()
        total_lines = len(lines)
        page_size = int(self.pack_cfg.get("page_size_lines", 80))
        total_pages = max(1, (total_lines + page_size - 1) // page_size)

        # Check if this exact output was already archived in the session (Deduplication!)
        was_deduplicated = False
        seen_count = 1
        if log_file.is_file() and meta_file.is_file():
            try:
                old_meta = json.loads(meta_file.read_text(encoding="utf-8"))
                seen_count = int(old_meta.get("seen_count", 1)) + 1
                was_deduplicated = True
            except Exception:
                pass
        else:
            log_file.write_text(working_output, encoding="utf-8")

        meta = {
            "obs_id": obs_id,
            "command": command,
            "exit_code": exit_code,
            "raw_bytes": raw_bytes,
            "total_lines": total_lines,
            "page_size": page_size,
            "total_pages": total_pages,
            "seen_count": seen_count,
            "timestamp": time.time(),
        }
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # If seen >= 3 times (beyond 2-turn grace window for identical output), emit compact delta ref
        grace_turns = int(self.pack_cfg.get("grace_turns", 2))
        if was_deduplicated and seen_count > grace_turns:
            head_preview = "\n".join(_clip(l) for l in lines[:5])
            tail_preview = "\n".join(_clip(l) for l in lines[-5:])
            compact_ref = (
                f"[SoL-Pi ObservationPack Deduplicated Handle: {obs_id} | Seen #{seen_count} | "
                f"Exit: {exit_code} | Raw: {raw_bytes} B ({total_lines} lines, {total_pages} pages)]\n"
                f"Output is 100% identical to previously archived `{obs_id}`.\n"
                f"HEAD (5 lines):\n{head_preview}\n...\nTAIL (5 lines):\n{tail_preview}\n"
                f"Recall pages on-demand: `solpi read {obs_id} --page 1` or MCP `solpi_read_obs(obs_id='{obs_id}', page=1)`"
            )
            trans_bytes = len(compact_ref.encode("utf-8"))
            self.telemetry.record(
                raw_bytes=raw_bytes,
                transmitted_bytes=trans_bytes,
                packed=True,
                deduplicated=True,
                secrets_redacted=secrets_redacted,
            )
            return PackedObservationResult(
                obs_id=obs_id,
                formatted_output=compact_ref,
                raw_bytes=raw_bytes,
                transmitted_bytes=trans_bytes,
                was_packed=True,
                was_reduced=True,
                was_deduplicated=True,
                failed_open=False,
                exit_code=exit_code,
            )

        # Run Evidence-Preserving Reducer
        if reducible:
            reduced_text, was_reduced, failed_open, _ = reduce_log_fail_open(
                working_output,
                exit_code,
                obs_id=obs_id,
                config=self.config,
                custom_llm_fn=custom_llm_fn,
            )
        else:
            reduced_text, was_reduced, failed_open = working_output, False, False

        head_n = int(self.pack_cfg.get("head_lines", 15))
        tail_n = int(self.pack_cfg.get("tail_lines", 20))
        head_block = "\n".join(f"L{i+1}: {_clip(lines[i])}" for i in range(min(head_n, total_lines)))
        tail_start = max(min(head_n, total_lines), total_lines - tail_n)
        tail_block = "\n".join(f"L{i+1}: {_clip(lines[i])}" for i in range(tail_start, total_lines))

        sections = [
            f"[SoL-Pi ObservationPack Handle: {obs_id} | Exit: {exit_code} | "
            f"Raw: {raw_bytes} B ({total_lines} lines, {total_pages} pages)]",
            f"--- HEAD (Lines 1-{min(head_n, total_lines)}) ---",
            head_block,
        ]
        if was_reduced:
            sections.append(reduced_text)
        sections.extend([
            f"--- TAIL (Lines {tail_start+1}-{total_lines}) ---",
            tail_block,
            f"[Paged Recall] Inspect any page or search regex without bloating context:\n"
            f"  CLI: `solpi read {obs_id} --page <1..{total_pages}>` or `solpi read {obs_id} --grep \"<pattern>\"`\n"
            f"  MCP: `solpi_read_obs(obs_id=\"{obs_id}\", page=1)`",
        ])

        packed_payload = "\n".join(sections)
        trans_bytes = len(packed_payload.encode("utf-8"))

        # Safety check: if packed payload is somehow larger than raw, return raw
        if trans_bytes >= raw_bytes:
            self.telemetry.record(
                raw_bytes=raw_bytes,
                transmitted_bytes=raw_bytes,
                fail_open=True,
                secrets_redacted=secrets_redacted,
            )
            return PackedObservationResult(
                obs_id=obs_id,
                formatted_output=working_output,
                raw_bytes=raw_bytes,
                transmitted_bytes=raw_bytes,
                was_packed=False,
                was_reduced=False,
                was_deduplicated=was_deduplicated,
                failed_open=True,
                exit_code=exit_code,
            )

        self.telemetry.record(
            raw_bytes=raw_bytes,
            transmitted_bytes=trans_bytes,
            packed=True,
            deduplicated=was_deduplicated,
            reduced=was_reduced,
            fail_open=failed_open,
            secrets_redacted=secrets_redacted,
        )
        return PackedObservationResult(
            obs_id=obs_id,
            formatted_output=packed_payload,
            raw_bytes=raw_bytes,
            transmitted_bytes=trans_bytes,
            was_packed=True,
            was_reduced=was_reduced,
            was_deduplicated=was_deduplicated,
            failed_open=failed_open,
            exit_code=exit_code,
        )

    def read_obs(
        self,
        obs_id: str,
        *,
        page: int = 1,
        page_size: Optional[int] = None,
        grep_pattern: Optional[str] = None,
        around_line: Optional[int] = None,
        radius: int = 20,
    ) -> str:
        """Exact paged recall, line neighborhood lookup, or regex search over an archived handle."""
        clean_id = obs_id.strip()
        log_file = self._log_path(clean_id)
        if not log_file.is_file():
            return f"[ObservationPack Error] Handle `{clean_id}` not found in {self.store_dir}"

        content = log_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        total_lines = len(lines)

        if around_line is not None:
            center = max(1, min(total_lines, around_line)) - 1
            start = max(0, center - radius)
            end = min(total_lines, center + radius + 1)
            snippet = "\n".join(f"L{i+1}: {lines[i]}" for i in range(start, end))
            return f"[{clean_id} | Lines {start+1}-{end} of {total_lines} (around L{around_line})]\n{snippet}"

        if grep_pattern:
            try:
                regex = re.compile(grep_pattern, re.IGNORECASE)
            except re.error as err:
                return f"[ObservationPack Regex Error] Invalid pattern {grep_pattern!r}: {err}"
            matches: List[str] = []
            for idx, line in enumerate(lines):
                if regex.search(line):
                    matches.append(f"L{idx+1}: {line}")
                    if len(matches) >= 80:
                        matches.append("... (truncated at 80 matches; refine pattern or use --around-line)")
                        break
            if not matches:
                return f"[{clean_id} | Grep {grep_pattern!r}] No matching lines found across {total_lines} lines."
            return f"[{clean_id} | Grep {grep_pattern!r} | {len(matches)} match(es)]\n" + "\n".join(matches)

        p_size = page_size or int(self.pack_cfg.get("page_size_lines", 80))
        total_pages = max(1, (total_lines + p_size - 1) // p_size)
        p_num = max(1, min(total_pages, page))
        start = (p_num - 1) * p_size
        end = min(total_lines, start + p_size)
        body = "\n".join(f"L{i+1}: {lines[i]}" for i in range(start, end))
        return f"[{clean_id} | Page {p_num}/{total_pages} (Lines {start+1}-{end} of {total_lines})]\n{body}"

    def list_handles(self, limit: int = 20) -> List[Dict[str, Any]]:
        metas = sorted(self.store_dir.glob("obs_*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for mp in metas[:limit]:
            try:
                out.append(json.loads(mp.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out
