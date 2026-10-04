#!/usr/bin/env python3
"""Immutable, append-only forecast ledger.

Each issuance (one run of the production model) is written ONCE to its own
deterministically gzipped CSV under ``data/forecast_ledger/YYYY-MM/`` and registered
in ``data/forecast_ledger/index.csv`` with a file hash, a content hash and a hash
chain to the previous index row. Existing issuance files are never rewritten; the
validation gate (validate_build.py) fails publication if any prior byte changes.

The ledger stores what the model said *at issue time*: score, class, P(Good+),
confidence tier, every driver term, the zone-level input features, source
timestamps and data-quality flags, plus the two baseline forecasts (seasonal
climatology and ENSO-regime climatology) computed from information available at
issue time.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_common import (  # noqa: E402
    ENGINE_MODEL_VERSION, LEDGER_SCHEMA_VERSION, ZONE_PRIMARY_REGION, REGIONS, TERMS,
    atomic_write, clamp, clean_scalar, csv_bytes, deterministic_gzip, inverse_soft_ceiling,
    lead_bucket, p_good, score_class, sha256_bytes, sha256_file, soft_ceiling, tier_for_lead,
    utc_now_iso, parse_ts,
)

PT = ZoneInfo("America/Los_Angeles")

LEDGER_COLUMNS = [
    "record_id", "issuance_id", "schema_version", "origin", "source_ref",
    "issue_utc", "issue_local", "issue_local_date", "data_cutoff_utc",
    "target_date", "lead_days", "lead_bucket", "horizon_tier", "confidence_key", "confidence_label",
    "zone_id", "region_id", "species_id", "model_version",
    "bite_score", "bite_class", "p_good_or_better", "score_lo", "score_hi",
    "baseline_clim_score", "baseline_enso_score", "baseline_enso_anom_f",
    "enso_regime", "oni",
    "core_score", "fishability", "anomaly_term",
    "term_sst", "term_season", "term_tide", "term_moon", "term_pressure", "term_swell", "term_front", "term_chl",
    "missing_drivers", "basis",
    "sst_f", "sst_anom_f", "sst_source", "wave_ft", "wind_kt", "tide_range_ft", "moon_illum",
    "pressure_trend_24h", "front_f_per_nm",
    "inputs_json",
    "src_mur_newest", "src_mur_freshness", "src_ndbc_newest_utc", "src_coops_wtemp_newest",
    "src_openmeteo_fetched_utc", "src_openmeteo_valid_to", "src_cpc_814_issued", "src_cpc_wk34_issued",
    "src_oni_checked", "src_tides_fetched_utc", "src_failed",
    "dq_flags",
]
INDEX_COLUMNS = [
    "issuance_id", "issue_utc", "data_cutoff_utc", "issue_local_date", "origin", "source_ref",
    "model_version", "schema_version", "n_rows", "lead_min", "lead_max", "file",
    "file_sha256", "content_sha256", "prev_index_row_sha256", "created_utc",
]

NEAR_INPUTS = [
    "sst_f", "sst_source", "sst_norm", "sst_anom_f", "sst_pctile", "sst_trend_7d", "shore_sst_f",
    "wave_ft", "swell_ft", "wave_period_s", "swell_period_s", "wave_dir_deg", "wind_kt", "gust_kt",
    "wind_dir_deg", "pressure_hpa", "pressure_trend_24h", "cloud_pct", "air_temp_f", "precip_mm",
    "tide_range_ft", "tide_exchanges", "max_exchange_rate_ft_h", "best_water_hour", "moon_illum",
    "moon_phase", "moon_age_days", "front_f_per_nm", "sst_spread_f", "wave_norm", "wind_norm",
    "is_forecast", "enso_regime", "oni_latest",
]
EXT_INPUTS = [
    "sst_norm_f", "sst_proj_f", "sst_anom_proj_f", "sst_proj_lo_f", "sst_proj_hi_f", "sst_basis",
    "w_persistence", "w_enso_analog", "w_cpc_outlook", "cpc_nudge_f", "sst_anom_core_f",
    "wave_proj_ft", "wind_proj_kt", "wave_norm_ft", "wind_norm_kt", "sea_state_published", "sea_state_basis",
    "model_blend_weight", "pressure_trend_model_24h", "cpc_product", "cpc_temp_cat", "cpc_temp_prob",
    "enso_regime", "oni", "enso_expected_anom_f", "analog_years", "tide_range_ft",
    "max_exchange_rate_ft_h", "best_water_hour", "moon_illum", "moon_phase",
]
# Raw deterministic model columns that must never be stored as an input at lead >= 15.
FAR_FORBIDDEN = {"wave_model_ft", "wind_model_kt", "wave_model_period_s", "wave_proj_ft", "wind_proj_kt"}


def _num(v):
    v = clean_scalar(v)
    return v


def _inputs_json(row: dict | None, cols: list[str], lead: int) -> str:
    if row is None:
        return "{}"
    out = {}
    for c in cols:
        if lead >= 15 and c in FAR_FORBIDDEN:
            continue
        if c in row:
            v = clean_scalar(row[c])
            if v is not None and v != "":
                out[c] = v
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


def _source_fields(status: dict | None, ext_meta: dict | None) -> tuple[dict, list[str]]:
    f = {k: None for k in LEDGER_COLUMNS if k.startswith("src_")}
    flags = []
    cpc = (ext_meta or {}).get("cpc_products") or {}
    f["src_cpc_814_issued"] = (cpc.get("cpc_814_temp") or {}).get("issued")
    f["src_cpc_wk34_issued"] = (cpc.get("cpc_wk34_temp") or {}).get("issued")
    if not status or not status.get("sources"):
        flags.append("no_source_timestamps")
        return f, flags
    rows = status["sources"]

    def pick(prefix):
        return [r for r in rows if r["source_identifier"].startswith(prefix)]

    mur = pick("mur_sst")
    if mur:
        f["src_mur_newest"] = mur[0].get("newest_valid_source_timestamp")
        f["src_mur_freshness"] = mur[0].get("freshness")
        if mur[0].get("freshness") in ("failed", "stale", "delayed"):
            flags.append(f"mur_{mur[0].get('freshness')}")
    nd = [parse_ts(r.get("newest_valid_source_timestamp")) for r in pick("ndbc:")]
    nd = [x for x in nd if x]
    f["src_ndbc_newest_utc"] = max(nd).isoformat().replace("+00:00", "Z") if nd else None
    co = [r.get("newest_valid_source_timestamp") for r in pick("coops:") if r["source_identifier"].endswith("water_temperature")]
    co = [x for x in co if x]
    f["src_coops_wtemp_newest"] = max(co) if co else None
    om = pick("openmeteo:")
    fetched = [r.get("last_successful_fetch_utc") for r in om if r.get("last_successful_fetch_utc")]
    f["src_openmeteo_fetched_utc"] = min(fetched) if fetched else None
    valid = [r.get("newest_valid_source_timestamp") for r in om if r.get("newest_valid_source_timestamp")]
    f["src_openmeteo_valid_to"] = min(valid) if valid else None
    oni = pick("cpc_oni")
    f["src_oni_checked"] = oni[0].get("last_successful_fetch_utc") if oni else None
    tides = [r.get("last_successful_fetch_utc") for r in pick("coops:") if r["source_identifier"].endswith("predictions")]
    tides = [t for t in tides if t]
    f["src_tides_fetched_utc"] = min(tides) if tides else None
    failed = sorted(r["source_identifier"] for r in rows if r.get("freshness") == "failed")
    f["src_failed"] = ",".join(failed) if failed else None
    if failed:
        flags.append(f"sources_failed={len(failed)}")
    return f, flags


def build_issuance(csv_dir: Path, *, issue_utc: str, data_cutoff_utc: str | None, origin: str,
                   source_ref: str, model_version: str = ENGINE_MODEL_VERSION,
                   today: date | None = None) -> tuple[list[dict], dict]:
    """Assemble ledger rows for one issuance from a built dataset/csv directory."""
    csv_dir = Path(csv_dir)
    ext_meta = json.loads((csv_dir / "extended_meta.json").read_text()) if (csv_dir / "extended_meta.json").exists() else {}
    status_p = csv_dir / "source_status.json"
    status = json.loads(status_p.read_text()) if status_p.exists() else None
    if today is None:
        today = date.fromisoformat(ext_meta["today"])
    scores = pd.read_csv(csv_dir / "scores_daily.csv")
    cond = pd.read_csv(csv_dir / "conditions_daily.csv")
    ext_sc = pd.read_csv(csv_dir / "extended_scores.csv")
    ext_ol = pd.read_csv(csv_dir / "extended_outlook.csv")
    curve = pd.read_csv(csv_dir / "climatological_curve.csv")
    comp = pd.read_csv(csv_dir / "enso_zone_composite.csv")
    enso_cur = pd.read_csv(csv_dir / "enso_current.csv").iloc[0].to_dict()
    species = pd.read_csv(csv_dir / "dim_species.csv")
    pref = dict(zip(species.species_id, species.anomaly_pref))

    regime = enso_cur.get("simple_regime")
    oni = clean_scalar(enso_cur.get("oni"))
    curve_idx = {(r.zone_id, r.species_id, int(r.doy)): r.climatological_score for r in curve.itertuples()}
    comp_idx = {(r.zone_id, int(r.month), r.simple_regime): r.mean_anom_f for r in comp.itertuples()}
    cond_idx = {(r["zone_id"], str(r["date"])[:10]): r for r in cond.to_dict("records")}
    ol_idx = {(r["zone_id"], str(r["date"])[:10]): r for r in ext_ol.to_dict("records")}

    src, src_flags = _source_fields(status, ext_meta)
    issue_dt = parse_ts(issue_utc)
    issue_local = issue_dt.astimezone(PT)
    issuance_id = f"{today.isoformat()}T{issue_local:%H%M}PT_{model_version}"
    base = {
        "issuance_id": issuance_id, "schema_version": LEDGER_SCHEMA_VERSION, "origin": origin,
        "source_ref": source_ref, "issue_utc": issue_utc, "issue_local": issue_local.isoformat(),
        "issue_local_date": today.isoformat(), "data_cutoff_utc": data_cutoff_utc,
        "model_version": model_version, "enso_regime": regime, "oni": oni, **src,
    }

    def baselines(zone, sp, tdate: date):
        doy = tdate.timetuple().tm_yday
        clim = curve_idx.get((zone, sp, doy))
        if clim is None or (isinstance(clim, float) and math.isnan(clim)):
            return None, None, None
        anom = comp_idx.get((zone, tdate.month, regime))
        if anom is None or (isinstance(anom, float) and math.isnan(anom)):
            return round(float(clim), 1), None, None
        f = clamp(1.0 + float(pref.get(sp, 0)) * (float(anom) / 2.5) * 0.45, 0.35, 1.35)
        enso = soft_ceiling(clamp(inverse_soft_ceiling(float(clim) / 100.0) * f, 0.01, 1.35)) * 100
        return round(float(clim), 1), round(enso, 1), round(float(anom), 3)

    rows = []
    # ---- leads 0-6 from the unchanged near-term model
    sd = scores.copy()
    sd["d"] = pd.to_datetime(sd["date"]).dt.date
    near = sd[(sd.d >= today) & (sd.d <= today + timedelta(days=6))]
    for r in near.to_dict("records"):
        t = r["d"]
        lead = (t - today).days
        crow = cond_idx.get((r["zone_id"], t.isoformat()))
        rows.append(_row(base, r, t, lead, crow, NEAR_INPUTS, baselines, src_flags, origin, issue_local,
                         score=r["score"], lo=None, hi=None, fish=r.get("fishability"),
                         basis="observed + deterministic forecast" if lead > 0 else "observed/nowcast",
                         pressure_trend=(crow or {}).get("pressure_trend_24h")))
    # ---- leads 7-30 from the unchanged extended model
    es = ext_sc.copy()
    es["d"] = pd.to_datetime(es["date"]).dt.date
    for r in es.to_dict("records"):
        t = r["d"]
        lead = (t - today).days
        if lead < 7 or lead > 30:
            continue
        orow = ol_idx.get((r["zone_id"], t.isoformat()))
        rows.append(_row(base, r, t, lead, orow, EXT_INPUTS, baselines, src_flags, origin, issue_local,
                         score=r["score_outlook"], lo=r.get("score_lo"), hi=r.get("score_hi"),
                         fish=r.get("fishability_outlook"), basis=r.get("basis"),
                         pressure_trend=r.get("pressure_trend_model_24h")))
    rows.sort(key=lambda x: (x["target_date"], x["zone_id"], x["species_id"]))
    meta = {"issuance_id": issuance_id, "issue_utc": issue_utc, "data_cutoff_utc": data_cutoff_utc,
            "issue_local_date": today.isoformat(), "origin": origin, "source_ref": source_ref,
            "model_version": model_version, "n_rows": len(rows),
            "lead_min": min((x["lead_days"] for x in rows), default=None),
            "lead_max": max((x["lead_days"] for x in rows), default=None)}
    return rows, meta


def _row(base, r, t, lead, inputs_row, input_cols, baselines, src_flags, origin, issue_local, *,
         score, lo, hi, fish, basis, pressure_trend):
    zone, sp = r["zone_id"], r["species_id"]
    tier, ckey, clabel = tier_for_lead(lead)
    clim, enso, anom = baselines(zone, sp, t)
    flags = list(src_flags)
    if origin != "live_daily_refresh":
        flags.append(origin)
    md = r.get("missing_drivers")
    md = "" if md is None or (isinstance(md, float) and math.isnan(md)) else str(md)
    if md:
        flags.append("missing_drivers")
    sst_source = (inputs_row or {}).get("sst_source") if lead <= 6 else "projected (persistence + ENSO analog + CPC)"
    if lead <= 6 and sst_source and "model" in str(sst_source).lower():
        flags.append("sst_modelled_not_observed")
    if lead == 0 and issue_local.hour >= 12:
        flags.append("lead0_issued_after_local_noon")
    if zone in ("sd_bay", "mission_bay"):
        flags.append("shared_bay_thermistor")
    if not REGIONS[ZONE_PRIMARY_REGION[zone]]["labelled"]:
        flags.append("no_outcome_source")
    if lead >= 15:
        flags += ["no_daily_sea_state", "fishability_default"]
    elif lead >= 7:
        flags.append("model_decayed_toward_climatology")
    ir = inputs_row or {}
    row = {
        **base,
        "record_id": f"{base['issuance_id']}|{t.isoformat()}|{zone}|{sp}",
        "target_date": t.isoformat(), "lead_days": int(lead), "lead_bucket": lead_bucket(lead),
        "horizon_tier": tier, "confidence_key": ckey, "confidence_label": clabel,
        "zone_id": zone, "region_id": ZONE_PRIMARY_REGION[zone], "species_id": sp,
        "bite_score": _num(score), "bite_class": score_class(_num(score)),
        "p_good_or_better": p_good(_num(score), lead),
        "score_lo": _num(lo), "score_hi": _num(hi),
        "baseline_clim_score": clim, "baseline_enso_score": enso, "baseline_enso_anom_f": anom,
        "core_score": _num(r.get("core_score")), "fishability": _num(fish),
        "anomaly_term": _num(r.get("anomaly_term")),
        **{f"term_{k}": _num(r.get(f"term_{k}")) for k in TERMS},
        "missing_drivers": md or None, "basis": basis,
        "sst_f": _num(ir.get("sst_f") if lead <= 6 else ir.get("sst_proj_f")),
        "sst_anom_f": _num(ir.get("sst_anom_f") if lead <= 6 else ir.get("sst_anom_proj_f")),
        "sst_source": sst_source,
        "wave_ft": _num(ir.get("wave_ft")) if lead <= 6 else (_num(ir.get("wave_proj_ft")) if lead < 15 else None),
        "wind_kt": _num(ir.get("wind_kt")) if lead <= 6 else (_num(ir.get("wind_proj_kt")) if lead < 15 else None),
        "tide_range_ft": _num(ir.get("tide_range_ft")), "moon_illum": _num(ir.get("moon_illum")),
        "pressure_trend_24h": _num(pressure_trend) if lead < 15 else None,
        "front_f_per_nm": _num(ir.get("front_f_per_nm")) if lead <= 6 else None,
        "inputs_json": _inputs_json(ir, input_cols, lead),
        "dq_flags": ";".join(dict.fromkeys(flags)) or None,
    }
    return row


# ---------------------------------------------------------------- persistence
def ledger_root(data_root: Path) -> Path:
    return Path(data_root) / "forecast_ledger"


def read_index(data_root: Path) -> pd.DataFrame:
    p = ledger_root(data_root) / "index.csv"
    if not p.exists():
        return pd.DataFrame(columns=INDEX_COLUMNS)
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def append_issuance(data_root: Path, rows: list[dict], meta: dict) -> dict | None:
    """Write a new issuance. Returns the index row, or None when already recorded."""
    root = ledger_root(data_root)
    idx = read_index(data_root)
    if not idx.empty and ((idx.issuance_id == meta["issuance_id"]).any()
                          or (meta["source_ref"] and (idx.source_ref == meta["source_ref"]).any())):
        return None
    if not rows:
        raise ValueError("refusing to record an empty issuance")
    rel = f"{meta['issue_local_date'][:7]}/{meta['issuance_id']}.csv.gz"
    path = root / rel
    if path.exists():
        raise FileExistsError(f"ledger file already exists and is immutable: {rel}")
    body = csv_bytes(rows, LEDGER_COLUMNS)
    gz = deterministic_gzip(body)
    atomic_write(path, gz)
    index_path = root / "index.csv"
    prev = ""
    if index_path.exists():
        lines = index_path.read_text().rstrip("\n").split("\n")
        prev = sha256_bytes(lines[-1].encode()) if len(lines) > 1 else ""
    entry = {**{k: meta.get(k) for k in INDEX_COLUMNS}, "schema_version": LEDGER_SCHEMA_VERSION,
             "file": rel, "file_sha256": sha256_bytes(gz), "content_sha256": sha256_bytes(body),
             "prev_index_row_sha256": prev, "created_utc": utc_now_iso()}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=INDEX_COLUMNS, lineterminator="\n")
    if not index_path.exists():
        w.writeheader()
    w.writerow({k: ("" if entry.get(k) is None else entry[k]) for k in INDEX_COLUMNS})
    old = index_path.read_bytes() if index_path.exists() else b""
    atomic_write(index_path, old + buf.getvalue().encode())
    return entry


def load_ledger(data_root: Path) -> pd.DataFrame:
    idx = read_index(data_root)
    frames = []
    for r in idx.itertuples():
        p = ledger_root(data_root) / r.file
        with gzip.open(p, "rt") as fh:
            frames.append(pd.read_csv(fh))
    if not frames:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def verify_ledger(staged_data: Path, baseline_data: Path | None) -> list[str]:
    """Immutability + leakage checks. Returns a list of error strings."""
    errors = []
    idx = read_index(staged_data)
    root = ledger_root(staged_data)
    prev = ""
    raw_lines = (root / "index.csv").read_text().rstrip("\n").split("\n") if (root / "index.csv").exists() else []
    for i, r in enumerate(idx.itertuples()):
        if r.prev_index_row_sha256 != prev:
            errors.append(f"forecast ledger index hash chain broken at {r.issuance_id}")
        prev = sha256_bytes(raw_lines[i + 1].encode())
        p = root / r.file
        if not p.exists():
            errors.append(f"forecast ledger file missing: {r.file}")
            continue
        if sha256_file(p) != r.file_sha256:
            errors.append(f"forecast ledger file modified after issue: {r.file}")
            continue
        body = gzip.decompress(p.read_bytes())
        if sha256_bytes(body) != r.content_sha256:
            errors.append(f"forecast ledger content hash mismatch: {r.file}")
        df = pd.read_csv(io.BytesIO(body))
        if len(df) != int(r.n_rows):
            errors.append(f"forecast ledger row count mismatch: {r.file}")
        issue = parse_ts(r.issue_utc)
        for col in ("src_ndbc_newest_utc", "src_openmeteo_fetched_utc", "src_tides_fetched_utc",
                    "src_oni_checked", "src_coops_wtemp_newest", "src_mur_newest"):
            if col in df:
                ts = [parse_ts(v) for v in df[col].dropna().unique()]
                if any(t and issue and t > issue + timedelta(minutes=5) for t in ts):
                    errors.append(f"look-ahead: {col} later than issue time in {r.issuance_id}")
        far = df[df.lead_days >= 15]
        for col in ("wave_ft", "wind_kt"):
            if far[col].notna().any():
                errors.append(f"{r.issuance_id}: deterministic {col} stored at lead >= 15")
        if far.inputs_json.astype(str).str.contains("wave_model_ft|wind_model_kt|wave_proj_ft|wind_proj_kt").any():
            errors.append(f"{r.issuance_id}: forbidden sea-state inputs stored at lead >= 15")
        if df.record_id.duplicated().any():
            errors.append(f"{r.issuance_id}: duplicate record ids")
    if baseline_data is not None:
        old = read_index(baseline_data)
        if len(old) > len(idx):
            errors.append("forecast ledger index lost issuances")
        for o in old.itertuples():
            m = idx[idx.issuance_id == o.issuance_id]
            if m.empty:
                errors.append(f"forecast ledger issuance removed: {o.issuance_id}")
            elif (m.iloc[0].file_sha256 != o.file_sha256) or (m.iloc[0].content_sha256 != o.content_sha256):
                errors.append(f"forecast ledger issuance rewritten: {o.issuance_id}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Record one issuance from a built dataset/csv directory")
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--issue-utc", required=True)
    ap.add_argument("--cutoff-utc")
    ap.add_argument("--origin", default="live_daily_refresh")
    ap.add_argument("--source-ref", default="")
    ap.add_argument("--today", type=date.fromisoformat)
    a = ap.parse_args()
    rows, meta = build_issuance(a.csv, issue_utc=a.issue_utc, data_cutoff_utc=a.cutoff_utc,
                                origin=a.origin, source_ref=a.source_ref, today=a.today)
    entry = append_issuance(a.data, rows, meta)
    print(json.dumps(entry or {"skipped": "already recorded", **meta}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
