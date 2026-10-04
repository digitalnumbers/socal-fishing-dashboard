#!/usr/bin/env python3
"""Versioned model registry and human-gated challenger workflow.

Governance rules enforced here and in validate_build.py:
  * The live Bite Score is never retrained or changed automatically.
  * ``production_model`` must equal the engine's implemented model id
    (learning_common.ENGINE_MODEL_VERSION, currently "v1").
  * Promotion requires (a) a review whose recommendation passed every criterion,
    (b) an explicit human approval record, and (c) a reviewed code change that makes
    the engine implement the approved model. Only then does ``promote`` succeed.
  * Models and decisions are never deleted; decisions are append-only.

CLI
  python pipeline/model_registry.py list
  python pipeline/model_registry.py propose --id v1.2-wt --kind weights --weights-file w.json --description "..."
  python pipeline/model_registry.py propose --id v1.1-cal --kind calibration --description "..."
  python pipeline/model_registry.py review  --id v1.1-cal
  python pipeline/model_registry.py approve --id v1.1-cal --by "Your Name" --note "..."
  python pipeline/model_registry.py promote --id v1.1-cal
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_common import (  # noqa: E402
    CLASS_CUTS, CLASSES, ENGINE_MODEL_VERSION, LABEL_VERSION, P_SPREAD, PRIORITY_REGIONS,
    PRIORITY_SPECIES, ROOT, TERMS, read_json, utc_now_iso, write_json,
)

PT = ZoneInfo("America/Los_Angeles")

PROMOTION_CRITERIA = {
    "min_pairs": 300,
    "min_target_dates": 45,
    "min_issuances": 30,
    "min_aggregate_brier_improvement": 0.02,
    "max_mae_degradation_pts": 0.5,
    "min_bootstrap_p_positive": 0.90,
    "min_stratum_pairs": 30,
    "max_priority_brier_degradation": 0.05,
    "max_short_range_brier_degradation": 0.02,
    "max_short_range_hit_drop": 0.02,
}


def registry_path(data_root: Path) -> Path:
    return Path(data_root) / "model_registry.json"


def initial_registry(species_cfg: pd.DataFrame) -> dict:
    weights = {r.species_id: json.loads(r.weights_json) for r in species_cfg.itertuples()}
    now = utc_now_iso()
    common_classes = {"classes": CLASSES, "score_cuts": CLASS_CUTS,
                      "p_good_or_better": {"form": "logistic((score-60)/spread)", "spread_by_lead_bucket": P_SPREAD}}
    return {
        "schema_version": 1,
        "engine_model_version": ENGINE_MODEL_VERSION,
        "production_model": "v1",
        "governance": {
            "auto_retrain": False,
            "human_approval_required": True,
            "live_model_changes_require_code_change": True,
            "priority_species": PRIORITY_SPECIES,
            "priority_regions": PRIORITY_REGIONS,
            "promotion_criteria": PROMOTION_CRITERIA,
            "review_cadence": {"daily": "append forecasts and outcomes, refresh accuracy (no retraining)",
                               "monthly": "review shadow challengers if enough new labels (recommendation only)",
                               "seasonal": "full walk-forward review; human decision on promotion"},
            "label_version": LABEL_VERSION,
        },
        "models": [
            {
                "model_id": "v1", "status": "production", "role": "champion", "kind": "engine",
                "created_utc": "2026-08-28T20:20:23Z", "registered_utc": now,
                "description": "Original SoCal Fishing Intelligence Bite Score: weighted geometric mean of driver "
                               "suitabilities x ENSO/SST anomaly multiplier x access (fishability) gate, soft ceiling. "
                               "Days 8-30 reuse the same engine on blended/climatological inputs.",
                "code_ref": "pipeline/build_dataset.py section 6 (score_row); pipeline/build_extended.py (extended inputs)",
                "feature_set": [f"term_{t}" for t in TERMS] + ["anomaly_term (sst_anom_f, anomaly_pref)", "fishability (wind, wave, period, distance)"],
                "weights": weights,
                "class_mapping": common_classes,
                "training_window": "none - expert/literature-specified weights and response curves; not fitted to catch data",
                "evaluation_window": None,
                "changes": "Initial production model, preserved unchanged as champion Model v1.",
                "latest_performance_summary": None,
                "promotion_decision": {"decision": "baseline_production", "decided_utc": now,
                                       "reason": "Pre-existing production model at the time the learning system was introduced."},
                "human_approval": {"approved": True, "approved_by": "pre-existing production model (grandfathered)",
                                   "approved_utc": now, "note": "No change to the live model."},
            },
            {
                "model_id": "baseline_climatology", "status": "reference", "role": "baseline A", "kind": "baseline",
                "registered_utc": now,
                "description": "Seasonal climatology: the v1 engine run on day-of-year climatological SST, wave and wind "
                               "with neutral tide/moon terms (climatological_curve.csv). Skill reference.",
                "feature_set": ["zone", "species", "day_of_year"], "weights": "as v1, climatological inputs",
                "class_mapping": common_classes, "training_window": "MUR 2015-2026, CO-OPS 2015-2026, NDBC 2003-2026 climatologies",
                "evaluation_window": None, "changes": "Reference baseline.", "latest_performance_summary": None,
                "promotion_decision": {"decision": "not_eligible", "reason": "Reference baseline only."},
                "human_approval": {"approved": False},
            },
            {
                "model_id": "baseline_enso", "status": "reference", "role": "baseline B", "kind": "baseline",
                "registered_utc": now,
                "description": "ENSO/SST-regime seasonal baseline: climatological score shifted by the zone x month x "
                               "ENSO-regime composite SST anomaly through each species' anomaly preference "
                               "(enso_zone_composite.csv, regime as known at issue time).",
                "feature_set": ["zone", "species", "day_of_year", "ENSO regime at issue", "composite anomaly"],
                "weights": "anomaly multiplier clamp(1 + pref*(anom/2.5)*0.45, 0.35, 1.35)",
                "class_mapping": common_classes, "training_window": "ENSO composites 2015-2026 MUR anomalies",
                "evaluation_window": None, "changes": "Reference baseline.", "latest_performance_summary": None,
                "promotion_decision": {"decision": "not_eligible", "reason": "Reference baseline only."},
                "human_approval": {"approved": False},
            },
            {
                "model_id": "v1.1-cal", "status": "shadow", "role": "challenger", "kind": "calibration",
                "registered_utc": now, "parent": "v1",
                "description": "Offline calibration challenger: per horizon group (0-7 / 8-30 days) linear mapping "
                               "observed_score ~ a + b x v1_score, refitted walk-forward at every issuance using only "
                               "labels available before that issuance. Identity (= v1) until 60 training pairs exist.",
                "feature_set": "v1 score + horizon group", "weights": "as v1",
                "min_training_pairs": 60,
                "class_mapping": common_classes,
                "training_window": "expanding window of labels available before each issuance",
                "evaluation_window": None,
                "changes": "Recalibrates v1's score scale (v1 runs high: Good-or-better is over-called in late summer).",
                "latest_performance_summary": None,
                "promotion_decision": {"decision": "pending", "reason": "Shadow evaluation only."},
                "human_approval": {"approved": False, "approved_by": None, "approved_utc": None},
            },
        ],
        "decisions": [
            {"utc": now, "model_id": "v1", "action": "register", "decision": "production champion",
             "by": "system", "reason": "Learning system introduced; live model preserved unchanged."},
            {"utc": now, "model_id": "v1.1-cal", "action": "register", "decision": "shadow challenger",
             "by": "system", "reason": "Offline calibration test; cannot affect the live score."},
        ],
    }


def ensure_registry(data_root: Path, species_cfg: pd.DataFrame) -> dict:
    p = registry_path(data_root)
    reg = read_json(p)
    if reg is None:
        reg = initial_registry(species_cfg)
        write_json(p, reg)
    return reg


def update_performance(data_root: Path, summary: dict) -> dict:
    """Refresh the informational performance fields; never touches status or decisions."""
    p = registry_path(data_root)
    reg = read_json(p)
    head = {(r["window"], r["dimension"], r["stratum"], r["model_id"]): r for r in summary.get("headline", [])}
    for m in reg["models"]:
        mid = m["model_id"]
        perf = {}
        for hg in ("short_0_7", "outlook_8_30"):
            r = head.get(("all", "horizon_group", hg, mid))
            if r:
                perf[hg] = {k: r.get(k) for k in ("n_pairs", "n_target_dates", "sample_flag", "exact_hit",
                                                  "within_one", "mae", "brier", "brier_skill_vs_clim")}
        if perf:
            m["latest_performance_summary"] = {"as_of": summary["as_of"], **perf}
            m["evaluation_window"] = {"target_dates_from": summary["labels"].get("first_fishing_date"),
                                      "target_dates_to": summary["labels"].get("last_fishing_date"),
                                      "issuances": summary["pairs"].get("issuances"),
                                      "method": summary.get("method")}
    write_json(p, reg)
    return reg


def validate_registry(reg: dict | None, baseline: dict | None) -> list[str]:
    errs = []
    if not reg:
        return ["model_registry.json missing"]
    if reg.get("production_model") != ENGINE_MODEL_VERSION:
        errs.append(f"registry production_model {reg.get('production_model')!r} does not match the implemented "
                    f"engine {ENGINE_MODEL_VERSION!r}; promotion requires a reviewed engine code change")
    prods = [m for m in reg.get("models", []) if m.get("status") == "production"]
    if len(prods) != 1 or prods[0]["model_id"] != reg.get("production_model"):
        errs.append("registry must contain exactly one production model matching production_model")
    for m in prods:
        if not (m.get("human_approval") or {}).get("approved"):
            errs.append(f"production model {m['model_id']} lacks a human approval record")
    if reg.get("governance", {}).get("auto_retrain") is not False:
        errs.append("registry governance.auto_retrain must be false")
    if baseline:
        ids = {m["model_id"] for m in reg.get("models", [])}
        for m in baseline.get("models", []):
            if m["model_id"] not in ids:
                errs.append(f"registry model removed: {m['model_id']}")
        old_dec, new_dec = baseline.get("decisions", []), reg.get("decisions", [])
        if new_dec[:len(old_dec)] != old_dec:
            errs.append("registry decisions are append-only; prior decisions changed")
    return errs


# ---------------------------------------------------------------- CLI
def _load(data_root):
    reg = read_json(registry_path(data_root))
    if reg is None:
        raise SystemExit("no registry; run the daily refresh or learning_step first")
    return reg


def _find(reg, mid):
    for m in reg["models"]:
        if m["model_id"] == mid:
            return m
    raise SystemExit(f"unknown model {mid}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("propose")
    p.add_argument("--id", required=True)
    p.add_argument("--kind", choices=["weights", "calibration"], required=True)
    p.add_argument("--weights-file", type=Path, help="JSON {species_id or '*': {term: weight}} overrides")
    p.add_argument("--description", required=True)
    p.add_argument("--min-training-pairs", type=int, default=60)
    r = sub.add_parser("review")
    r.add_argument("--id", required=True)
    a = sub.add_parser("approve")
    a.add_argument("--id", required=True)
    a.add_argument("--by", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--override-recommendation", default=None,
                   help="reason for approving despite a non-passing review (recorded)")
    pr = sub.add_parser("promote")
    pr.add_argument("--id", required=True)
    args = ap.parse_args()
    reg = _load(args.data)
    now = utc_now_iso()

    if args.cmd == "list":
        for m in reg["models"]:
            print(f"{m['model_id']:22s} {m['status']:11s} {m.get('role', ''):12s} approved={m.get('human_approval', {}).get('approved')}")
        print("production:", reg["production_model"])
        return 0

    if args.cmd == "propose":
        if any(m["model_id"] == args.id for m in reg["models"]):
            raise SystemExit("model id already registered; ids are immutable - choose a new one")
        over = json.loads(args.weights_file.read_text()) if args.weights_file else None
        if args.kind == "weights" and not over:
            raise SystemExit("--weights-file is required for weights challengers")
        reg["models"].append({
            "model_id": args.id, "status": "shadow", "role": "challenger", "kind": args.kind, "parent": "v1",
            "registered_utc": now, "description": args.description,
            "feature_set": "as v1 (stored as-issued driver terms)", "weights_override": over,
            "min_training_pairs": args.min_training_pairs,
            "training_window": "walk-forward: labels available before each issuance" if args.kind == "calibration"
            else "none (weights specified, evaluated on as-issued terms)",
            "evaluation_window": None, "changes": args.description, "latest_performance_summary": None,
            "promotion_decision": {"decision": "pending"},
            "human_approval": {"approved": False, "approved_by": None, "approved_utc": None},
        })
        reg["decisions"].append({"utc": now, "model_id": args.id, "action": "register",
                                 "decision": "shadow challenger", "by": "operator", "reason": args.description})
        write_json(registry_path(args.data), reg)
        print(f"registered {args.id} as shadow challenger; it is evaluated offline on every run")
        return 0

    m = _find(reg, args.id)
    if args.cmd == "review":
        ev = read_json(Path(args.data) / "model_evaluation.json") or {}
        res = next((c for c in ev.get("challengers", []) if c["model_id"] == args.id), None)
        if not res:
            raise SystemExit("no evaluation for this model yet; wait for the next refresh")
        m["promotion_decision"] = {"decision": res["recommendation"], "decided_utc": now,
                                   "evaluation_as_of": ev.get("as_of"), "checks": res["checks"]}
        m["status"] = "candidate" if res["recommendation"].startswith("recommend") else m["status"]
        reg["decisions"].append({"utc": now, "model_id": args.id, "action": "review",
                                 "decision": res["recommendation"], "by": "operator",
                                 "reason": "; ".join(f"{c['check']}: {'pass' if c['passed'] else 'FAIL'}" for c in res["checks"])})
        write_json(registry_path(args.data), reg)
        print(json.dumps(m["promotion_decision"], indent=2))
        return 0

    if args.cmd == "approve":
        rec = (m.get("promotion_decision") or {}).get("decision", "")
        if not rec.startswith("recommend") and not args.override_recommendation:
            raise SystemExit(f"latest review is {rec!r}; approval needs a passing review or --override-recommendation")
        m["human_approval"] = {"approved": True, "approved_by": args.by, "approved_utc": now, "note": args.note,
                               "override": args.override_recommendation}
        reg["decisions"].append({"utc": now, "model_id": args.id, "action": "approve", "decision": "approved",
                                 "by": args.by, "reason": args.note or args.override_recommendation or ""})
        write_json(registry_path(args.data), reg)
        print(f"approval recorded. Next: implement {args.id} in the engine, set ENGINE_MODEL_VERSION, then promote.")
        return 0

    if args.cmd == "promote":
        if not (m.get("human_approval") or {}).get("approved"):
            raise SystemExit("refused: no human approval recorded for this model")
        if ENGINE_MODEL_VERSION != args.id:
            raise SystemExit(f"refused: the scoring engine implements {ENGINE_MODEL_VERSION!r}. Implement {args.id} "
                             "in a reviewed code change and set learning_common.ENGINE_MODEL_VERSION first.")
        old = _find(reg, reg["production_model"])
        old["status"], old["role"] = "retired", "former champion"
        m["status"], m["role"] = "production", "champion"
        reg["production_model"] = args.id
        reg["decisions"].append({"utc": now, "model_id": args.id, "action": "promote", "decision": "production",
                                 "by": m["human_approval"]["approved_by"], "reason": "approved and implemented"})
        write_json(registry_path(args.data), reg)
        print(f"{args.id} is now production")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
