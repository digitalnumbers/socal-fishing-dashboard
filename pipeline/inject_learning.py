#!/usr/bin/env python3
"""Add the forecast-learning payload to app/data.js (strictly additive key ``learning``).

Also stages the learning artefacts into app/downloads/learning/ so the dashboard can
link the ledgers, registry, evaluation and methodology directly.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_common import REGIONS, atomic_write, read_json  # noqa: E402

DOWNLOADS = ["model_registry.json", "model_evaluation.json", "model_evaluation.csv", "outcome_ledger.csv",
             "label_reference_L1.csv", "update_log.json", "forecast_changes_latest.csv",
             "forecast_ledger/index.csv"]


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in json.loads(df.to_json(orient="records"))]


def build(root: Path) -> dict:
    data = root / "data"
    ev = read_json(data / "model_evaluation.json", {}) or {}
    reg = read_json(data / "model_registry.json", {}) or {}
    idx_p = data / "forecast_ledger/index.csv"
    idx = pd.read_csv(idx_p, dtype=str, keep_default_na=False) if idx_p.exists() else pd.DataFrame()
    ch_p = data / "forecast_changes_latest.csv"
    ch = pd.read_csv(ch_p) if ch_p.exists() else pd.DataFrame()
    lab_p = data / "outcome_ledger.csv"
    labels = pd.read_csv(lab_p) if lab_p.exists() else pd.DataFrame()
    cover = []
    if not labels.empty:
        g = labels.groupby(["region_id", "species_id", "label_status"]).size().unstack(fill_value=0).reset_index()
        cover = _records(g)
    models = [{k: m.get(k) for k in ("model_id", "status", "role", "kind", "description", "changes",
                                     "training_window", "evaluation_window", "latest_performance_summary",
                                     "promotion_decision", "human_approval")} for m in reg.get("models", [])]
    keep = ["target_date", "zone_id", "species_id", "lead_now", "lead_prior", "tier_now", "tier_prior",
            "score_prior", "score_now", "delta", "class_prior", "class_now", "top_drivers",
            "input_changes", "notes"]
    return {
        "evaluation": ev,
        "registry": {"production_model": reg.get("production_model"),
                     "engine_model_version": reg.get("engine_model_version"),
                     "governance": reg.get("governance"), "models": models,
                     "decisions": (reg.get("decisions") or [])[-25:]},
        "ledger_index": _records(idx[[c for c in ("issuance_id", "issue_utc", "issue_local_date", "origin",
                                                  "model_version", "n_rows", "lead_min", "lead_max")
                                      if c in idx.columns]]) if not idx.empty else [],
        "changes": _records(ch[keep]) if not ch.empty else [],
        "changes_meta": ({"current": ch.current_issuance.iloc[0], "prior": ch.prior_issuance.iloc[0]}
                         if not ch.empty else None),
        "label_coverage": cover,
        "regions": {k: {"name": v["name"], "zones": v["zones"], "labelled": v["labelled"]} for k, v in REGIONS.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    a = ap.parse_args()
    root = a.repo
    js = root / "app/data.js"
    raw = js.read_text(encoding="utf-8")
    m = re.search(r"(window\.DATA\s*=\s*)(\{.*\})(\s*;\s*)$", raw, flags=re.S)
    if not m:
        raise SystemExit("app/data.js is not a window.DATA assignment")
    payload = json.loads(m.group(2))
    payload["learning"] = build(root)
    atomic_write(js, (m.group(1) + json.dumps(payload, separators=(",", ":"), allow_nan=False) + m.group(3)).encode())
    dest = root / "app/downloads/learning"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    n = 0
    for rel in DOWNLOADS:
        src = root / "data" / rel
        if src.exists():
            shutil.copy2(src, dest / Path(rel).name)
            n += 1
    doc = root / "docs/FORECAST_LEARNING.md"
    if doc.exists():
        shutil.copy2(doc, dest / doc.name)
        n += 1
    print(f"learning payload injected ({len(json.dumps(payload['learning'])) / 1024:.0f} KB); staged {n} downloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
