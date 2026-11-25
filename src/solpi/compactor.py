"""Mechanism 4: Online Context Compaction (Economic Milestone Compactor).

Implements arXiv:2609.20519 Section 3.4:
- Evaluates context compaction dynamically at clean subtask/plan milestones rather than
  waiting blindly for a 95% hard context overflow.
- Uses an economic projection model that weighs:
    Projected Input Savings over remaining turns (N_rem)
      vs.
    Prompt KV-Cache invalidation / rewrite penalty + compaction summary cost.
- Produces a dense Milestone Checkpoint Receipt containing completed steps, files touched,
  active `obs_*` handles, and next subtask focus so the agent can safely `/compact` or
  continue in a fresh subagent turn without losing critical task state.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import TelemetryTracker, get_store_dir, load_config


@dataclass
class CompactionDecision:
    should_compact: bool
    trigger_reason: str
    current_tokens_est: int
    prunable_tokens_est: int
    retained_tokens_est: int
    projected_savings_usd: float
    cache_rewrite_cost_usd: float
    net_benefit_usd: float
    window_pressure_ratio: float
    checkpoint_markdown: str
    remaining_turns_est: int = 0


class OnlineContextCompactor:
    """Tracks subtask milestones and runs the SoL-Pi economic compaction decision gate."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or load_config()
        self.comp_cfg = self.config.get("online_compaction", {})
        self.store_dir = store_dir or get_store_dir(self.config)
        self.state_file = self.store_dir / "plan_milestones.json"
        self.telemetry = TelemetryTracker(self.store_dir)

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.is_file():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"milestones": [], "last_compaction_tokens": 0, "compactions": 0, "unrecovered_rewrite_usd": 0.0}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def evaluate_milestone(
        self,
        completed_step: str,
        *,
        current_tokens_est: int,
        prunable_fraction: float = 0.60,
        expected_remaining_turns: Optional[int] = None,
        files_modified: Optional[List[str]] = None,
        obs_handles: Optional[List[str]] = None,
        key_findings: Optional[str] = None,
        next_step: Optional[str] = None,
        unfinished_steps: Optional[int] = None,
        requests_since_last_milestone: Optional[int] = None,
    ) -> CompactionDecision:
        """Record a completed plan milestone and compute the economic compaction decision.

        Follows arXiv:2609.20519 "Online Context Compact": when `expected_remaining_turns` is not given,
        remaining model requests are estimated from the requests observed between completed steps times
        the number of unfinished steps, capped by the requests that would fill the context window at the
        observed growth rate. Later compactions must also recover earlier unrecovered rewrite costs and
        clear a larger savings margin.
        """
        state = self._load_state()
        state.setdefault("compactions", 0)
        state.setdefault("unrecovered_rewrite_usd", 0.0)
        history = state["milestones"]
        window_limit = max(1, int(self.comp_cfg.get("window_limit_tokens", 200000)))
        pressure_trigger = float(self.comp_cfg.get("pressure_ratio_trigger", 0.65))
        floor_turns = int(self.comp_cfg.get("min_expected_turns_remaining", 4))
        window_cap_note = ""
        if expected_remaining_turns is not None:
            rem_turns = int(expected_remaining_turns)
        elif unfinished_steps is not None and (requests_since_last_milestone or any(m.get("requests") for m in history)):
            observed = [int(m["requests"]) for m in history if m.get("requests")]
            if requests_since_last_milestone:
                observed.append(int(requests_since_last_milestone))
            per_step = sum(observed) / len(observed)
            rem_turns = max(0, int(round(per_step * max(0, int(unfinished_steps)))))
            # Cap by how many requests fit in the window at the observed context growth rate
            samples = [(m.get("requests"), m.get("tokens_at_milestone")) for m in history if m.get("requests")]
            total_requests = sum(r for r, _ in samples) + int(requests_since_last_milestone or 0)
            first_tokens = history[0]["tokens_at_milestone"] if history else 0
            growth = (current_tokens_est - first_tokens) / total_requests if total_requests and history else 0
            if growth > 0:
                fit = int(max(0, window_limit - current_tokens_est) / growth)
                if fit < rem_turns:
                    window_cap_note = f" (capped from {rem_turns} by window growth)"
                    rem_turns = fit
        else:
            rem_turns = floor_turns

        c_cached_read = float(self.comp_cfg.get("cached_read_cost_per_mtok", 0.30))
        c_cache_write = float(self.comp_cfg.get("cache_write_cost_per_mtok", 3.75))

        prunable_tokens = int(max(0, current_tokens_est) * max(0.1, min(0.9, prunable_fraction)))
        retained_tokens = max(1000, current_tokens_est - prunable_tokens)
        pressure_ratio = round(current_tokens_est / window_limit, 4)

        # Economic model (in USD):
        # Saving `prunable_tokens` across `rem_turns` cached reads vs rewriting `retained_tokens` into KV cache once
        projected_savings_usd = (prunable_tokens / 1_000_000.0) * rem_turns * c_cached_read
        cache_rewrite_cost_usd = (retained_tokens / 1_000_000.0) * c_cache_write
        # Later compactions: also recover earlier unrecovered rewrite cost and clear a growing margin
        n_prev = int(state["compactions"])
        unrecovered = float(state["unrecovered_rewrite_usd"])
        required_margin = unrecovered + 0.25 * n_prev * cache_rewrite_cost_usd
        net_benefit_usd = round(projected_savings_usd - cache_rewrite_cost_usd - required_margin, 6)

        can_shorten = prunable_tokens > 0 and retained_tokens < current_tokens_est
        window_pressure_exceeded = pressure_ratio >= pressure_trigger
        economic_break_even = (net_benefit_usd > 0.0) and (current_tokens_est >= 25000)

        should_compact = bool(
            self.comp_cfg.get("enabled", True)
            and can_shorten
            and (window_pressure_exceeded or economic_break_even)
        )

        if window_pressure_exceeded and economic_break_even:
            reason = (
                f"Both Window Pressure ({pressure_ratio*100:.1f}% >= {pressure_trigger*100:.1f}%) "
                f"and Economic Break-Even (+${net_benefit_usd:.4f} net savings over {rem_turns} turns) triggered."
            )
        elif window_pressure_exceeded:
            reason = f"Window Pressure Gate triggered ({pressure_ratio*100:.1f}% >= {pressure_trigger*100:.1f}%)."
        elif economic_break_even:
            reason = (
                f"KV-Cache Economic Gate triggered: compacting ~{prunable_tokens:,} tokens now "
                f"saves +${net_benefit_usd:.4f} over the next {rem_turns} turns{window_cap_note}."
            )
        else:
            reason = (
                f"Defer compaction: keeping prompt cache warm is cheaper right now "
                f"(pressure={pressure_ratio*100:.1f}%, net_benefit=${net_benefit_usd:.4f})."
            )

        # Realized savings since the previous compaction pay back its rewrite cost
        if state["compactions"] and requests_since_last_milestone:
            realized = (state.get("last_prunable_tokens", 0) / 1_000_000.0) * requests_since_last_milestone * c_cached_read
            state["unrecovered_rewrite_usd"] = max(0.0, float(state["unrecovered_rewrite_usd"]) - realized)
        entry = {
            "step": completed_step,
            "files_modified": files_modified or [],
            "obs_handles": obs_handles or [],
            "key_findings": key_findings or "",
            "next_step": next_step or "",
            "tokens_at_milestone": current_tokens_est,
            "requests": int(requests_since_last_milestone or 0),
            "compaction_recommended": should_compact,
            "timestamp": time.time(),
        }
        state["milestones"].append(entry)
        if should_compact:
            state["last_compaction_tokens"] = retained_tokens
            state["compactions"] = n_prev + 1
            state["unrecovered_rewrite_usd"] = float(state["unrecovered_rewrite_usd"]) + cache_rewrite_cost_usd
            state["last_prunable_tokens"] = prunable_tokens
        self._save_state(state)

        self.telemetry.record(
            milestone=True,
            compaction_triggered=should_compact,
            extra_tokens_saved=(prunable_tokens if should_compact else 0),
        )

        checkpoint_md = self._build_checkpoint_markdown(
            state["milestones"],
            should_compact=should_compact,
            reason=reason,
            prunable_tokens=prunable_tokens,
            retained_tokens=retained_tokens,
        )

        return CompactionDecision(
            should_compact=should_compact,
            trigger_reason=reason,
            current_tokens_est=current_tokens_est,
            prunable_tokens_est=prunable_tokens,
            retained_tokens_est=retained_tokens,
            projected_savings_usd=round(projected_savings_usd, 6),
            cache_rewrite_cost_usd=round(cache_rewrite_cost_usd, 6),
            net_benefit_usd=net_benefit_usd,
            window_pressure_ratio=pressure_ratio,
            checkpoint_markdown=checkpoint_md,
            remaining_turns_est=rem_turns,
        )

    def _build_checkpoint_markdown(
        self,
        milestones: List[Dict[str, Any]],
        *,
        should_compact: bool,
        reason: str,
        prunable_tokens: int,
        retained_tokens: int,
    ) -> str:
        badge = "RECOMMENDED NOW (`/compact` or subagent boundary)" if should_compact else "DEFERRED (Keep Cache Warm)"
        lines = [
            f"### [SoL-Pi Online Context Compaction | Status: {badge}]",
            f"- **Decision**: {reason}",
            f"- **Token Projection**: Prunable ~{prunable_tokens:,} tokens | Retained state ~{retained_tokens:,} tokens",
            "",
            "#### Completed Milestones State Receipt:",
        ]
        for idx, m in enumerate(milestones[-8:], start=1):
            files_str = f" (Files: `{', '.join(m['files_modified'])}`)" if m.get("files_modified") else ""
            obs_str = f" [Archived Logs: `{', '.join(m['obs_handles'])}`]" if m.get("obs_handles") else ""
            lines.append(f"{idx}. **[x] {m['step']}**{files_str}{obs_str}")
            if m.get("key_findings"):
                lines.append(f"   - *Verified Evidence*: {m['key_findings']}")
        last = milestones[-1] if milestones else {}
        if last.get("next_step"):
            lines.append(f"\n- **Next Active Subtask**: `{last['next_step']}`")
        return "\n".join(lines)
