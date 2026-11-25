"""Mechanism 5: SoL-Pi & Harness2 Recursive Self-Improvement (RSI) Auto-Research Loop (`solpi tune`).

Propose -> Probe -> Filter (Capability Floor + Efficiency Gate) -> Compose, as in SoL-Pi (arXiv:2609.20519)
and Google Research's Harness2, hardened against overfitting:

- Corpus: ~16 log families x 4 variants (+ optional REAL tool runs), each with ground-truth diagnostic lines
  (`solpi.tune_corpus`).  Recall = share of those lines visible verbatim in the receipt the agent gets.
- Train / holdout split BY FAMILY: candidates are selected on TRAIN families only; HOLDOUT families (and real
  tool logs) are never used for selection, only to validate the winner.
- Capability floor on the macro-average recall AND on every single family (a mean can hide a family that
  loses all its evidence).
- If the winner fails on HOLDOUT, the safe default configuration is recommended instead (`validated: false`).
- Candidates are a grid over the reducer/pack knobs (36 combos; 8 with `quick=True`) plus the shipped default
  as reference.
"""

from __future__ import annotations

import copy
import itertools
import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DEFAULT_CONFIG, load_config
from .observation_pack import ObservationPackStore
from .reducer import DIAGNOSTIC_REGEX, LOCATION_REGEX
from .tune_corpus import generate_corpus

FAMILY_FLOOR = 0.85  # no family may fall below this recall, whatever the mean says


def compute_evidence_recall(raw_log: str, packed_output: str) -> float:
    """Legacy location-based recall (kept for API compatibility; the tuner uses ground-truth lines)."""
    raw_locs = set()
    for m in LOCATION_REGEX.finditer(raw_log):
        loc = f"{m.group(1)}:{m.group(2)}" if m.group(1) else f"{m.group(3)}:{m.group(4)}"
        raw_locs.add(loc)
    if not raw_locs:
        raw_has_err = bool(DIAGNOSTIC_REGEX.search(raw_log))
        return 1.0 if (not raw_has_err or DIAGNOSTIC_REGEX.search(packed_output)) else 0.0
    return round(sum(1 for loc in raw_locs if loc in packed_output) / len(raw_locs), 4)


def generate_synthetic_benchmark_corpus() -> List[Dict[str, Any]]:
    """Back-compat alias: the deterministic synthetic corpus (train + holdout families)."""
    return generate_corpus()


def _tok(n_bytes: int) -> int:
    return n_bytes // 4


def evaluate_config(cfg: Dict[str, Any], cases: List[Dict[str, Any]], store_dir: Path) -> Dict[str, Any]:
    """Pack every case with `cfg` and score evidence recall (per family, macro) and token reduction."""
    store = ObservationPackStore(config=cfg, store_dir=store_dir)
    fam: Dict[str, List[int]] = defaultdict(lambda: [0, 0])  # hit, total truth lines
    raw_b = out_b = fail_open = 0
    for c in cases:
        res = store.pack(c["log"], exit_code=c["exit_code"], command=c.get("cmd", ""))
        shown = res.formatted_output
        f = fam[c["family"]]
        f[0] += sum(1 for t in c["truth"] if t.strip() in shown)
        f[1] += len(c["truth"])
        raw_b += len(c["log"].encode("utf-8"))
        out_b += len(shown.encode("utf-8"))
        fail_open += int(res.failed_open)
    per_family = {k: round(h / max(1, t), 4) for k, (h, t) in fam.items()}
    macro = round(sum(per_family.values()) / max(1, len(per_family)), 4)
    return {
        "recall": macro,
        "worst_family": min(per_family.values()) if per_family else 1.0,
        "per_family": per_family,
        "reduction_percent": round((1.0 - _tok(out_b) / max(1, _tok(raw_b))) * 100.0, 2),
        "raw_bytes": raw_b,
        "transmitted_bytes": out_b,
        "fail_open": fail_open,
        "cases": len(cases),
    }


def _passes(m: Dict[str, Any], floor: float) -> bool:
    return m["recall"] >= floor and m["worst_family"] >= min(FAMILY_FLOOR, floor)


def _candidate_grid(quick: bool = False) -> List[Dict[str, Any]]:
    out = []
    quotes_opts, radius_opts, thr_opts = ([15, 35], [0, 2], [2048]) if quick else ([15, 35, 60], [0, 1, 2], [2048, 4096])
    for (head, tail), quotes, radius, thr in itertools.product(
        [(4, 5), (15, 20)], quotes_opts, radius_opts, thr_opts
    ):
        out.append({
            "name": f"h{head}t{tail}_q{quotes}_r{radius}_t{thr}",
            "patch": {
                "observation_pack": {"head_lines": head, "tail_lines": tail},
                "evidence_reducer": {"threshold_bytes": thr, "max_verbatim_quotes": quotes, "context_radius_lines": radius},
            },
        })
    return out


def _apply(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(base)
    for section, vals in patch.items():
        cfg.setdefault(section, {}).update(vals)
    return cfg


def run_auto_research_loop(
    corpus: Optional[List[Dict[str, Any]]] = None,
    capability_floor: float = 0.95,
    base_config: Optional[Dict[str, Any]] = None,
    temp_store_dir: Optional[Path] = None,
    *,
    include_real: bool = False,
    seed: int = 1337,
    quick: bool = False,
) -> Dict[str, Any]:
    """Select the most compact configuration that keeps the evidence, then validate it on held-out data."""
    base_cfg = base_config or load_config()
    cases = corpus or generate_corpus(seed=seed, include_real=include_real)
    train = [c for c in cases if c.get("split", "train") == "train"]
    holdout = [c for c in cases if c.get("split") == "holdout"]

    # Fresh isolated store per run: a persistent dir makes repeated runs hit the dedup path.
    owns_store = temp_store_dir is None
    work_root = Path(tempfile.mkdtemp(prefix="solpi_tune_")) if owns_store else Path(temp_store_dir)
    counter = itertools.count()

    def run_eval(cfg: Dict[str, Any], subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        d = work_root / f"eval_{next(counter)}"
        d.mkdir(parents=True, exist_ok=True)
        return evaluate_config(cfg, subset, d)

    try:
        default_cfg = {s: dict(v) for s, v in DEFAULT_CONFIG.items()}
        default_cfg = _apply(base_cfg, {
            "observation_pack": {k: DEFAULT_CONFIG["observation_pack"][k] for k in ("head_lines", "tail_lines")},
            "evidence_reducer": {k: DEFAULT_CONFIG["evidence_reducer"][k]
                                 for k in ("threshold_bytes", "max_verbatim_quotes", "context_radius_lines")},
        })
        reference = {"train": run_eval(default_cfg, train),
                     "holdout": run_eval(default_cfg, holdout) if holdout else None}

        evaluated = []
        for cand in _candidate_grid(quick):
            m = run_eval(_apply(base_cfg, cand["patch"]), train)
            evaluated.append({**cand, "train": m, "passed": _passes(m, capability_floor)})

        surviving = [e for e in evaluated if e["passed"]]
        # Efficiency gate: most reduction; ties -> the more conservative (more evidence) setting
        winner = max(surviving, key=lambda e: (e["train"]["reduction_percent"], e["train"]["recall"])) if surviving else None

        validated, hold_metrics = False, None
        if winner is not None and holdout:
            hold_metrics = run_eval(_apply(base_cfg, winner["patch"]), holdout)
            validated = _passes(hold_metrics, capability_floor)
        elif winner is not None:
            validated = True  # nothing to validate against

        recommended = _apply(base_cfg, winner["patch"]) if (winner and validated) else default_cfg
        chosen_name = winner["name"] if (winner and validated) else "shipped_default"
    finally:
        if owns_store:
            shutil.rmtree(work_root, ignore_errors=True)

    def slim(m: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return None if m is None else {k: m[k] for k in ("recall", "worst_family", "reduction_percent", "fail_open", "cases", "per_family")}

    return {
        "winner": chosen_name,
        "selected_candidate": winner["name"] if winner else None,
        "validated_on_holdout": validated,
        "capability_floor": capability_floor,
        "family_floor": min(FAMILY_FLOOR, capability_floor),
        "winner_evidence_recall": (winner["train"]["recall"] if winner else reference["train"]["recall"]),
        "winner_reduction_percent": (winner["train"]["reduction_percent"] if winner else reference["train"]["reduction_percent"]),
        "corpus": {"train_cases": len(train), "holdout_cases": len(holdout),
                   "families": sorted({c["family"] for c in cases}), "real_cases": sum(1 for c in cases if c.get("real"))},
        "shipped_default": {"train": slim(reference["train"]), "holdout": slim(reference["holdout"])},
        "selected_holdout": slim(hold_metrics),
        "candidates": [
            {"name": e["name"], "evidence_recall": e["train"]["recall"], "worst_family": e["train"]["worst_family"],
             "passed_floor": e["passed"], "reduction_percent": e["train"]["reduction_percent"],
             "raw_bytes": e["train"]["raw_bytes"], "transmitted_bytes": e["train"]["transmitted_bytes"]}
            for e in evaluated
        ],
        "recommended_config": recommended,
    }
