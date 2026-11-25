"""Mechanism 5: SoL-Pi & Harness2 Recursive Self-Improvement (RSI) Auto-Research Loop (`solpi tune`).

Implements the Propose -> Probe -> Filter (Capability Floor + Efficiency Gate) -> Compose cycle
from SoL-Pi (arXiv:2609.20519) and Google Research's Harness2 (`google-research/harness2`):
- Evaluates candidate harness parameter mutations across sample logs or repository command suites.
- Enforces:
  1. Capability Floor: Diagnostic evidence retention (every critical error line / location in the
     ground-truth raw log must remain accessible or quoted in the reduced receipt) >= baseline.
  2. Efficiency Gate: Selects the Pareto-optimal configuration that maximizes token/byte reduction
     without violating the Capability Floor.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DEFAULT_CONFIG, load_config
from .observation_pack import ObservationPackStore
from .reducer import DIAGNOSTIC_REGEX, LOCATION_REGEX


@dataclass
class CandidateEvaluation:
    name: str
    config_patch: Dict[str, Any]
    evidence_recall_score: float
    passed_capability_floor: bool
    raw_bytes_total: int
    transmitted_bytes_total: int
    reduction_percent: float


def compute_evidence_recall(raw_log: str, packed_output: str) -> float:
    """Measure what fraction of critical file:line locations & error lines are preserved in the receipt."""
    raw_locs = set()
    for m in LOCATION_REGEX.finditer(raw_log):
        loc = f"{m.group(1)}:{m.group(2)}" if m.group(1) else f"{m.group(3)}:{m.group(4)}"
        raw_locs.add(loc)

    if not raw_locs:
        # Check diagnostic keyword presence
        raw_has_err = bool(DIAGNOSTIC_REGEX.search(raw_log))
        packed_has_err = bool(DIAGNOSTIC_REGEX.search(packed_output))
        return 1.0 if (not raw_has_err or packed_has_err) else 0.0

    retained = sum(1 for loc in raw_locs if loc in packed_output)
    return round(retained / len(raw_locs), 4)


def generate_synthetic_benchmark_corpus() -> List[Dict[str, Any]]:
    """Built-in multi-environment diagnostic corpus for zero-setup `solpi tune` benchmarking."""
    # 1. Verbose pytest run (~18 KB) with 2 failing tests buried in passing logs
    pytest_lines = [f"tests/unit/test_module_{i}.py::test_case_{j} PASSED [ {i}%]" for i in range(1, 35) for j in range(1, 10)]
    pytest_lines.insert(
        140,
        'File "src/auth/jwt_validator.py", line 88, in verify_token\n'
        "AssertionError: Token signature expired at epoch 1790000123",
    )
    pytest_lines.insert(
        260,
        'File "src/db/pool.py", line 142, in acquire_conn\n'
        "ConnectionRefusedError: [Errno 111] Connection refused to 127.0.0.1:5432",
    )
    pytest_log = "\n".join(pytest_lines)

    # 2. TypeScript / Webpack build log (~14 KB)
    ts_lines = [f"info  - Compiling module src/components/Widget{i}.tsx..." for i in range(1, 220)]
    ts_lines.append("src/api/client.ts:54:12 - error TS2345: Argument of type 'string | null' is not assignable to parameter of type 'string'.")
    ts_lines.append("src/hooks/useSession.ts:19:7 - error TS2322: Type 'undefined' is not assignable to type 'UserSession'.")
    ts_log = "\n".join(ts_lines)

    # 3. Rust Cargo build output (~12 KB)
    cargo_lines = [f"   Compiling crate_dep_{i} v0.4.{i} (/workspace/deps/crate_{i})" for i in range(1, 175)]
    cargo_lines.append("error[E0382]: borrow of moved value: `channel_rx`")
    cargo_lines.append(" --> src/worker/dispatcher.rs:211:24")
    cargo_log = "\n".join(cargo_lines)

    return [
        {"id": "pytest_long_suite", "cmd": "pytest -v", "exit_code": 1, "log": pytest_log},
        {"id": "typescript_build_fail", "cmd": "tsc --noEmit", "exit_code": 2, "log": ts_log},
        {"id": "cargo_borrow_error", "cmd": "cargo build", "exit_code": 101, "log": cargo_log},
    ]


def run_auto_research_loop(
    corpus: Optional[List[Dict[str, Any]]] = None,
    capability_floor: float = 0.95,
    base_config: Optional[Dict[str, Any]] = None,
    temp_store_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the Propose -> Probe -> Filter -> Compose RSI search loop across candidate harness configs."""
    base_cfg = base_config or load_config()
    samples = corpus or generate_synthetic_benchmark_corpus()

    # Candidate hypotheses (Propose phase)
    candidates = [
        {
            "name": "baseline_default",
            "patch": {
                "observation_pack": {"threshold_bytes": 10240, "head_lines": 15, "tail_lines": 20},
                "evidence_reducer": {"threshold_bytes": 4096, "max_verbatim_quotes": 35, "context_radius_lines": 2},
            },
        },
        {
            "name": "aggressive_compact_v1",
            "patch": {
                "observation_pack": {"threshold_bytes": 4096, "head_lines": 6, "tail_lines": 8},
                "evidence_reducer": {"threshold_bytes": 2048, "max_verbatim_quotes": 20, "context_radius_lines": 1},
            },
        },
        {
            "name": "ultra_compact_v2",
            "patch": {
                "observation_pack": {"threshold_bytes": 2048, "head_lines": 4, "tail_lines": 5},
                "evidence_reducer": {"threshold_bytes": 2048, "max_verbatim_quotes": 15, "context_radius_lines": 0},
            },
        },
        {
            "name": "conservative_high_context",
            "patch": {
                "observation_pack": {"threshold_bytes": 12288, "head_lines": 25, "tail_lines": 25},
                "evidence_reducer": {"threshold_bytes": 8192, "max_verbatim_quotes": 50, "context_radius_lines": 3},
            },
        },
    ]

    evaluations: List[CandidateEvaluation] = []
    # Fresh isolated store per run: a persistent dir makes repeated runs hit the dedup path
    # (identical logs, seen_count > grace_turns) and silently skew recall/reduction.
    owns_store = temp_store_dir is None
    work_root = Path(tempfile.mkdtemp(prefix="solpi_tune_")) if owns_store else Path(temp_store_dir)

    for idx, cand in enumerate(candidates):
        cfg = copy.deepcopy(base_cfg)
        for section, vals in cand["patch"].items():
            cfg.setdefault(section, {}).update(vals)

        store_sub = work_root / f"cand_{idx}"
        store_sub.mkdir(parents=True, exist_ok=True)
        store = ObservationPackStore(config=cfg, store_dir=store_sub)

        raw_total = 0
        trans_total = 0
        recalls: List[float] = []

        for item in samples:
            res = store.pack(item["log"], exit_code=item["exit_code"], command=item.get("cmd", item["id"]))
            raw_total += res.raw_bytes
            trans_total += res.transmitted_bytes
            recalls.append(compute_evidence_recall(item["log"], res.formatted_output))

        avg_recall = round(sum(recalls) / max(1, len(recalls)), 4)
        reduction_pct = round((1.0 - (trans_total / max(1, raw_total))) * 100.0, 2)
        passed_floor = avg_recall >= capability_floor

        evaluations.append(
            CandidateEvaluation(
                name=cand["name"],
                config_patch=cand["patch"],
                evidence_recall_score=avg_recall,
                passed_capability_floor=passed_floor,
                raw_bytes_total=raw_total,
                transmitted_bytes_total=trans_total,
                reduction_percent=reduction_pct,
            )
        )

    if owns_store:
        shutil.rmtree(work_root, ignore_errors=True)

    # Filter & Compose phase: pick candidate with highest reduction_percent among those passing capability_floor
    surviving = [ev for ev in evaluations if ev.passed_capability_floor]
    winner = max(surviving, key=lambda e: e.reduction_percent) if surviving else evaluations[0]

    composed_config = copy.deepcopy(base_cfg)
    for section, vals in winner.config_patch.items():
        composed_config.setdefault(section, {}).update(vals)

    return {
        "winner": winner.name,
        "capability_floor": capability_floor,
        "winner_evidence_recall": winner.evidence_recall_score,
        "winner_reduction_percent": winner.reduction_percent,
        "candidates": [
            {
                "name": e.name,
                "evidence_recall": e.evidence_recall_score,
                "passed_floor": e.passed_capability_floor,
                "reduction_percent": e.reduction_percent,
                "raw_bytes": e.raw_bytes_total,
                "transmitted_bytes": e.transmitted_bytes_total,
            }
            for e in evaluations
        ],
        "recommended_config": composed_config,
    }
