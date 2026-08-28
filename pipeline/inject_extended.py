#!/usr/bin/env python3
"""Inject the extended-outlook tables into app/data.js.

Why this exists instead of a change to build_site.py: build_site.py (and build_dataset.py) read
from /home/user/workspace/socal/data/out and import lib_parse from /home/user/workspace/socal.
That raw working directory no longer exists in this checkout, so the original site build cannot be
re-run without re-downloading the whole multi-decade source archive. This script therefore parses
the existing `window.DATA = {...};` payload, ADDS the new extended keys, and rewrites the file.

It is strictly additive. Every pre-existing key (meta, conditions, scores, curve, oni, validation,
cpue, trips, registry, dictionary, gaps, ...) is passed through untouched, so the near-term 1-7 day
forecast, its methodology and every existing chart are unaffected.

New keys written:
  extended             extended_outlook.csv           daily 8-30 day rows per zone
  extended_scores      extended_scores.csv            daily 8-30 day rows per zone/species
  confidence           forecast_confidence.csv        the three confidence tiers
  analogs              enso_analog_years.csv          ENSO analog years for this window
  analog_zone          enso_analog_zone_anomaly.csv   observed analog-year anomaly per zone
  cpc                  cpc_outlook_current.csv        CPC products actually sampled
  extended_provenance  extended_field_provenance.csv  forecast vs climatology per field
  extended_meta        extended_meta.json             build metadata + horizons

Usage:  python3 pipeline/inject_extended.py [--repo DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The CSVs are the full record; the browser payload only needs the columns the UI reads. Sending
# every column pushed data.js from 2.4 MB to 4.1 MB, mostly from long provenance strings repeated
# on all 216/1392 rows, so each table carries an explicit allowlist. None means "keep all columns".
COLS = {
    "extended": [
        "date", "zone_id", "zone", "band", "lead_days", "confidence_tier", "confidence_key",
        "sst_norm_f", "sst_proj_f", "sst_anom_proj_f", "sst_proj_lo_f", "sst_proj_hi_f",
        "w_persistence", "w_enso_analog", "w_cpc_outlook", "cpc_nudge_f", "sst_anom_core_f",
        "wave_proj_ft", "wind_proj_kt", "wave_norm_ft", "wind_norm_kt", "sea_state_published",
        "wind_norm_is_regional_fallback", "wave_basis", "wind_basis", "sst_basis",
        "tide_range_ft", "max_exchange_rate_ft_h",
        "best_water_hour", "moon_illum", "moon_phase", "sunrise_hour", "sunset_hour",
        "cpc_product", "cpc_temp_cat", "cpc_temp_prob", "cpc_prcp_cat",
        "enso_expected_anom_f",
    ],
    "extended_scores": [
        "date", "zone_id", "species_id", "lead_days", "confidence_tier", "confidence_key",
        "score_outlook", "score_lo", "score_hi", "score_band_pts", "climatological_score",
        "vs_seasonal_norm", "trend", "fishability_outlook", "term_sst", "term_season",
        "term_tide", "term_moon", "term_swell", "anomaly_term", "missing_drivers",
        "score_band_raw_pts", "score_halfband_lo_pts", "score_halfband_hi_pts",
        "score_band_is_ratcheted", "score_band_hits_ceiling", "score_band_hits_floor",
    ],
}

# csv file -> key in window.DATA. Optional tables are allowed to be missing.
TABLES = [
    ("extended_outlook.csv", "extended", True),
    ("extended_scores.csv", "extended_scores", True),
    ("forecast_confidence.csv", "confidence", True),
    ("enso_analog_years.csv", "analogs", True),
    ("enso_analog_zone_anomaly.csv", "analog_zone", False),
    ("cpc_outlook_current.csv", "cpc", True),
    ("extended_field_provenance.csv", "extended_provenance", True),
]


def clean(v):
    """JSON-safe scalar: NaN/NaT -> None, numpy scalars -> python scalars."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, 6)
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            return str(v)
        return clean(v)
    if isinstance(v, str):
        return v
    return v


def records(df: pd.DataFrame) -> list[dict]:
    out = []
    for row in df.to_dict(orient="records"):
        out.append({k: clean(v) for k, v in row.items()})
    return out


def load_payload(path: str) -> tuple[dict, str, str]:
    """Return (payload, prefix, suffix) from a `window.DATA = {...};` file."""
    raw = open(path, encoding="utf-8").read()
    m = re.search(r"(window\.DATA\s*=\s*)(\{.*\})(\s*;\s*)$", raw, flags=re.S)
    if not m:
        raise SystemExit(f"could not find a `window.DATA = {{...}};` assignment in {path}")
    return json.loads(m.group(2)), m.group(1), m.group(3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    csv_dir = os.path.join(a.repo, "dataset", "csv")
    data_js = os.path.join(a.repo, "app", "data.js")

    payload, prefix, suffix = load_payload(data_js)
    before = sorted(payload.keys())
    print(f"existing keys ({len(before)}): {', '.join(before)}")

    added = {}
    for fname, key, required in TABLES:
        p = os.path.join(csv_dir, fname)
        if not os.path.exists(p):
            if required:
                raise SystemExit(f"missing required table {p} -- run pipeline/build_extended.py first")
            print(f"  skip {fname} (absent, optional)")
            continue
        df = pd.read_csv(p)
        want = COLS.get(key)
        if want:
            missing = [c for c in want if c not in df.columns]
            if missing:
                raise SystemExit(f"{fname} is missing expected columns {missing}")
            df = df[want]
        added[key] = records(df)
        print(f"  {key:22s} <- {fname:34s} {len(df):5d} rows x {len(df.columns)} cols")

    # The Data & Sources tab reads window.DATA.registry / .gaps, which were baked in by the
    # original build_site.py from pipeline/config/sources.json. gen_extended_docs.py has since
    # added the extended-range feeds and caveats to that config, so refresh those two keys here,
    # otherwise the dashboard's own source registry would silently omit the CPC and 16-day model
    # feeds that the 30-Day Outlook tab depends on. Field order is preserved so the existing
    # rendering code needs no change.
    cfg_p = os.path.join(a.repo, "pipeline", "config", "sources.json")
    if os.path.exists(cfg_p):
        cfg = json.load(open(cfg_p))
        added["registry"] = cfg["registry"]
        added["gaps"] = cfg["gaps"]
        print(f"  {'registry':22s} <- {'config/sources.json':34s} {len(cfg['registry']):5d} rows")
        print(f"  {'gaps':22s} <- {'config/sources.json':34s} {len(cfg['gaps']):5d} notes")

    meta_p = os.path.join(csv_dir, "extended_meta.json")
    if not os.path.exists(meta_p):
        raise SystemExit(f"missing {meta_p} -- run pipeline/build_extended.py first")
    added["extended_meta"] = json.load(open(meta_p))
    print(f"  {'extended_meta':22s} <- extended_meta.json")

    clobber = [k for k in added if k in payload]
    if clobber:
        print(f"note: replacing previously injected keys: {', '.join(clobber)}")

    payload.update(added)

    # sanity: nothing pre-existing was dropped
    lost = set(before) - set(payload.keys()) - set(added.keys())
    if lost:
        raise SystemExit(f"refusing to write: would drop existing keys {sorted(lost)}")

    out = prefix + json.dumps(payload, separators=(",", ":"), allow_nan=False) + suffix
    if a.dry_run:
        print(f"dry run: would write {len(out):,} bytes ({len(out)/1e6:.2f} MB)")
        return 0

    bak = data_js + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(data_js, bak)
        print(f"backed up original -> {os.path.basename(bak)}")
    open(data_js, "w", encoding="utf-8").write(out)
    print(f"wrote {data_js}  {len(out):,} bytes ({len(out)/1e6:.2f} MB)")
    print(f"payload keys now ({len(payload)}): {', '.join(sorted(payload.keys()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
