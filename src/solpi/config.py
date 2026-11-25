"""Configuration loader and telemetry tracker for SoL-Pi."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "action_fusion": {
        "enabled": True,
        "default_timeout_sec": 120,
        "syntax_precheck": True,
        "revert_on_syntax_error": True,
    },
    "observation_pack": {
        "enabled": True,
        "threshold_bytes": 10240,
        "grace_turns": 2,
        "head_lines": 15,
        "tail_lines": 20,
        "page_size_lines": 80,
        "store_dir": "~/.cache/sol-pi/obs",
    },
    "evidence_reducer": {
        "enabled": True,
        "threshold_bytes": 4096,
        "mode": "hybrid",
        "delegate_model": "gemini-2.5-flash",
        "max_verbatim_quotes": 35,
        "context_radius_lines": 2,
        "strict_verbatim_check": True,
        "redact_secrets": True,
        "max_compression_ratio": 0.85,
    },
    "online_compaction": {
        "enabled": True,
        "window_limit_tokens": 200000,
        "pressure_ratio_trigger": 0.65,
        "min_expected_turns_remaining": 4,
        "cached_read_cost_per_mtok": 0.30,
        "cache_write_cost_per_mtok": 3.75,
        "uncompressed_input_cost_per_mtok": 3.00,
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def normalize_official_solpi_keys(user_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Translate official `NVlabs/SoL-Pi` camelCase keys (`actionFusion`, `cacheWriteReadRatio`, etc.)
    into the structured dictionary schema while preserving backwards compatibility.
    """
    out = dict(user_cfg)
    if isinstance(out.get("actionFusion"), bool):
        out.setdefault("action_fusion", {})["enabled"] = out["actionFusion"]
    if isinstance(out.get("observationPack"), bool):
        out.setdefault("observation_pack", {})["enabled"] = out["observationPack"]
    if isinstance(out.get("evidencePreservingReducer"), bool):
        out.setdefault("evidence_reducer", {})["enabled"] = out["evidencePreservingReducer"]
    if isinstance(out.get("onlineContextCompact"), bool):
        out.setdefault("online_compaction", {})["enabled"] = out["onlineContextCompact"]
    if "cacheWriteReadRatio" in out:
        try:
            ratio = float(out["cacheWriteReadRatio"])
            cached_read = float(
                out.get("online_compaction", {}).get(
                    "cached_read_cost_per_mtok",
                    DEFAULT_CONFIG["online_compaction"]["cached_read_cost_per_mtok"],
                )
            )
            out.setdefault("online_compaction", {})["cache_write_cost_per_mtok"] = round(cached_read * ratio, 4)
        except (TypeError, ValueError):
            pass
    return out


def load_config(config_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Load `sol-pi.json` from explicit path, `.pi/sol-pi.json`, CWD, or `~/.config/sol-pi/sol-pi.json`."""
    candidates = []
    if config_path:
        candidates.append(Path(config_path).expanduser())
    if os.environ.get("SOLPI_CONFIG"):
        candidates.append(Path(os.environ["SOLPI_CONFIG"]).expanduser())
    candidates.extend([
        Path.cwd() / ".pi" / "sol-pi.json",
        Path.cwd() / "sol-pi.json",
        Path.cwd() / ".agents" / "sol-pi.json",
        Path.home() / ".pi" / "agent" / "sol-pi.json",
        Path.home() / ".config" / "sol-pi" / "sol-pi.json",
        Path(__file__).resolve().parent.parent.parent / "sol-pi.json",
    ])
    cfg = dict(DEFAULT_CONFIG)
    for path in candidates:
        if path.is_file():
            try:
                user_cfg = normalize_official_solpi_keys(json.loads(path.read_text(encoding="utf-8")))
                cfg = deep_merge(cfg, user_cfg)
                break
            except Exception:
                continue
    return cfg


def get_store_dir(cfg: Optional[Dict[str, Any]] = None) -> Path:
    if cfg is None:
        cfg = load_config()
    env_dir = os.environ.get("SOLPI_STORE_DIR")
    raw_dir = env_dir if env_dir else cfg.get("observation_pack", {}).get("store_dir", "~/.cache/sol-pi/obs")
    store_path = Path(raw_dir).expanduser().resolve()
    store_path.mkdir(parents=True, exist_ok=True)
    return store_path


class TelemetryTracker:
    """Tracks token/byte savings, fused actions, and fail-open events across sessions."""

    def __init__(self, store_dir: Optional[Path] = None) -> None:
        self.store_dir = store_dir or get_store_dir()
        self.telemetry_file = self.store_dir / "telemetry.json"

    def _read(self) -> Dict[str, Any]:
        if self.telemetry_file.is_file():
            try:
                return json.loads(self.telemetry_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "fused_actions_count": 0,
            "avoided_round_trips": 0,
            "observations_packed": 0,
            "deduplicated_hits": 0,
            "reducer_invocations": 0,
            "reducer_fail_open_count": 0,
            "secrets_redacted_count": 0,
            "compaction_milestones": 0,
            "compaction_triggered_count": 0,
            "total_raw_bytes": 0,
            "total_transmitted_bytes": 0,
            "estimated_tokens_saved": 0,
            "last_updated": 0.0,
        }

    def record(
        self,
        *,
        raw_bytes: int = 0,
        transmitted_bytes: int = 0,
        fused_action: bool = False,
        avoided_round_trip: bool = False,
        packed: bool = False,
        deduplicated: bool = False,
        reduced: bool = False,
        fail_open: bool = False,
        secrets_redacted: int = 0,
        milestone: bool = False,
        compaction_triggered: bool = False,
        extra_tokens_saved: int = 0,
    ) -> Dict[str, Any]:
        data = self._read()
        data["total_raw_bytes"] += max(0, raw_bytes)
        data["total_transmitted_bytes"] += max(0, transmitted_bytes)
        bytes_saved = max(0, raw_bytes - transmitted_bytes)
        # Standard heuristic: ~4 bytes per token + round-trip context replay savings
        tokens_saved = (bytes_saved // 4) + extra_tokens_saved
        if avoided_round_trip:
            tokens_saved += 2500  # Conservative estimate of avoided turn prompt replay
        data["estimated_tokens_saved"] += tokens_saved

        if fused_action:
            data["fused_actions_count"] += 1
        if avoided_round_trip:
            data["avoided_round_trips"] += 1
        if packed:
            data["observations_packed"] += 1
        if deduplicated:
            data["deduplicated_hits"] += 1
        if reduced:
            data["reducer_invocations"] += 1
        if fail_open:
            data["reducer_fail_open_count"] += 1
        if secrets_redacted > 0:
            data["secrets_redacted_count"] += secrets_redacted
        if milestone:
            data["compaction_milestones"] += 1
        if compaction_triggered:
            data["compaction_triggered_count"] += 1

        data["last_updated"] = time.time()
        self.telemetry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def summary(self) -> Dict[str, Any]:
        data = self._read()
        raw = data["total_raw_bytes"]
        trans = data["total_transmitted_bytes"]
        reduction_pct = round((1.0 - (trans / raw)) * 100.0, 2) if raw > 0 else 0.0
        data["byte_reduction_percent"] = max(0.0, reduction_pct)
        return data

    def reset(self) -> None:
        if self.telemetry_file.exists():
            self.telemetry_file.unlink()
