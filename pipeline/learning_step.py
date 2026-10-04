#!/usr/bin/env python3
"""Forecast-learning step of the daily refresh (runs inside the staging tree).

Order: ingest new raw dock trips -> record today's issuance (immutable) -> rebuild
normalized outcome labels -> walk-forward evaluation (+ shadow challengers) ->
"why this forecast changed" -> append update_log.json. It never retrains or changes
the live Bite Score.

Modes
  live     daily refresh: ingest + new issuance + evaluation
  rebuild  no new issuance; re-derive labels/evaluation from the stored ledgers
  dry_run  same as rebuild (used by run_daily_refresh --dry-run inside staging)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_forecasts  # noqa: E402
import forecast_changes  # noqa: E402
from forecast_ledger import append_issuance, build_issuance  # noqa: E402
from learning_common import read_json, utc_now_iso, write_json  # noqa: E402
from model_registry import ensure_registry, update_performance  # noqa: E402
from outcome_ledger import build_labels, ingest, write_labels  # noqa: E402

MAX_LOG_ENTRIES = 2000


def run(root: Path, mode: str, today: date) -> dict:
    root = Path(root)
    data = root / "data"
    csv_dir = root / "dataset/csv"
    species = pd.read_csv(csv_dir / "dim_species.csv")
    entry = {"run_utc": utc_now_iso(), "local_date": today.isoformat(), "mode": mode, "warnings": []}

    if mode == "live":
        dock = root / ".refresh-cache/daily/dock_trips.json"
        if dock.exists():
            fetches = json.loads(dock.read_text()).get("fetches", [])
            entry["outcomes_ingest"] = ingest(data, fetches)
        else:
            entry["warnings"].append("no dock trip fetch this run; outcomes unchanged")
        status = read_json(csv_dir / "source_status.json", {}) or {}
        cutoff = status.get("build_started_at_utc")
        issue = utc_now_iso()
        rows, meta = build_issuance(csv_dir, issue_utc=issue, data_cutoff_utc=cutoff,
                                    origin="live_daily_refresh", source_ref=f"run:{cutoff}", today=today)
        rec = append_issuance(data, rows, meta)
        entry["issuance"] = rec and {k: rec[k] for k in ("issuance_id", "issue_utc", "n_rows", "file")}
        if rec is None:
            entry["warnings"].append("issuance already recorded; ledger unchanged")

    registry = ensure_registry(data, species)
    labels = build_labels(data, species, today)
    write_labels(data, labels)
    entry["labels"] = {"rows": int(len(labels)),
                       "labelled": int(labels.observed_class.notna().sum()) if len(labels) else 0,
                       "final": int((labels.label_status == "final").sum()) if len(labels) else 0,
                       "provisional": int((labels.label_status == "provisional").sum()) if len(labels) else 0}
    summary = evaluate_forecasts.run(data, species, registry, today)
    update_performance(data, summary)
    entry["evaluation"] = {"pairs": summary["pairs"]["total"], "issuances": summary["ledger"]["issuances"],
                           "challengers": {c["model_id"]: c["recommendation"] for c in summary["challengers"]}}
    changes = forecast_changes.compute(data, species)
    forecast_changes.write(data, changes)
    entry["forecast_changes"] = int(len(changes))
    entry["live_model_changed"] = False

    log_p = data / "update_log.json"
    log = read_json(log_p, {"schema_version": 1, "description": "Append-only run log of the forecast-learning system.",
                            "entries": []})
    log["entries"] = (log["entries"] + [entry])[-MAX_LOG_ENTRIES:]
    write_json(log_p, log)
    print(json.dumps(entry, indent=2))
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--mode", choices=["live", "rebuild", "dry_run"], default="rebuild")
    ap.add_argument("--today", type=date.fromisoformat, required=True)
    a = ap.parse_args()
    run(a.root, a.mode, a.today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
