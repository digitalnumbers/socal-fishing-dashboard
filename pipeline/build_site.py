"""Emit site/data.js payload for the SoCal Fishing Intelligence dashboard."""
import json, os, math
import numpy as np
import pandas as pd

ROOT = os.environ.get("SOCAL_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.environ.get("SOCAL_OUT", os.path.join(ROOT, "dataset", "csv"))
SITE = os.environ.get("SOCAL_SITE", os.path.join(ROOT, "app"))
os.makedirs(SITE, exist_ok=True)

def rd(name):
    p = f"{OUT}/{name}.csv"
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_csv(p)
    for c in d.columns:
        if c == "date":
            d[c] = pd.to_datetime(d[c]).dt.strftime("%Y-%m-%d")
    return d

def recs(d, cols=None, r=2):
    if d.empty:
        return []
    d = d[cols] if cols else d
    d = d.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].round(r)
    return json.loads(d.to_json(orient="records", double_precision=r))

cond = rd("conditions_daily")
scores = rd("scores_daily")
curve = rd("climatological_curve")
zones = rd("dim_zones")
species = rd("dim_species")
oni = rd("enso_oni")
enso_cur = rd("enso_current")
comp = rd("enso_zone_composite")
sens = rd("enso_sensitivity")
val = rd("model_validation")
cpue = rd("catch_daily_cpue")
trips = rd("catch_reports")
sstclim = rd("sst_climatology")
shclim = rd("shore_sst_climatology")
meta = json.load(open(f"{OUT}/run_meta.json"))

cond_cols = [c for c in ["zone_id", "date", "doy", "sst_f", "sst_source", "sst_norm", "sst_p10", "sst_p90",
                         "sst_anom_f", "sst_pctile", "sst_trend_7d", "sst_years", "wave_ft", "swell_ft",
                         "wave_period_s", "wave_norm", "wave_anom_ft", "wind_kt", "gust_kt", "wind_dir_deg",
                         "wind_norm", "wind_anom_kt", "pressure_hpa", "pressure_trend_24h", "cloud_pct",
                         "air_temp_f", "tide_range_ft", "max_exchange_rate_ft_h", "first_high_hour",
                         "best_water_hour", "moon_illum", "moon_phase", "moon_age_days", "sunrise_hour",
                         "sunset_hour", "daylight_hours", "front_f_per_nm", "sst_spread_f", "chl_mg_m3",
                         "cuti", "offshore_minus_nearshore_f", "shore_sst_f", "shore_sst_anom_f",
                         "is_forecast", "enso_regime", "oni_latest"] if c in cond.columns]

score_cols = [c for c in ["date", "zone_id", "species_id", "score", "seasonal_norm_score", "vs_norm",
                          "annual_percentile", "core_score", "fishability", "access_note", "is_forecast",
                          "term_sst", "term_season", "term_tide", "term_moon", "term_pressure",
                          "term_swell", "term_front", "term_chl", "anomaly_term", "contrib_json",
                          "missing_drivers"] if c in scores.columns]

# decimate the annual climatological curve to every 3rd day to keep the payload lean
cv = curve[curve.doy % 3 == 1] if not curve.empty else curve

payload = {
    "meta": {**meta, "generated": meta.get("run_utc"),
             "today": sorted(cond.date.unique())[14] if len(cond) else None,
             "dates": sorted(cond.date.unique().tolist()) if not cond.empty else []},
    "zones": recs(zones),
    "species": recs(species),
    "conditions": recs(cond, cond_cols),
    "scores": recs(scores, score_cols),
    "curve": recs(cv, ["zone_id", "species_id", "doy", "climatological_score", "sst_norm_f"], 1),
    "sst_clim": recs(sstclim, ["zone_id", "doy", "sst_norm", "sst_p10", "sst_p90", "sst_sd", "sst_years"], 2),
    "shore_clim": recs(shclim, [c for c in shclim.columns if c in
                                ("tide_station", "doy", "shoresst_norm", "shoresst_p10", "shoresst_p90", "shoresst_years")], 2),
    "oni": recs(oni[oni.year >= 1980] if not oni.empty else oni,
                [c for c in ["year", "month", "season", "oni", "regime", "simple_regime"] if c in oni.columns], 2),
    "enso_current": recs(enso_cur),
    "enso_composite": recs(comp),
    "enso_sensitivity": recs(sens, None, 3),
    "validation": recs(val, None, 3),
    "cpue": recs(cpue, None, 3),
    "trips": recs(trips[["date", "landing", "boat", "trip_type", "anglers", "species_id", "kept", "cpue_per_angler_day"]]
                  if not trips.empty else trips, None, 3),
}
status_path = os.environ.get("SOCAL_SOURCE_STATUS", os.path.join(OUT, "source_status.json"))
payload["source_status"] = json.load(open(status_path)) if os.path.exists(status_path) else {
    "overall": "unknown", "sources": [], "note": "Freshness metadata unavailable for this legacy build."
}
docs = json.load(open(f"{OUT}/docs.json"))
payload["registry"] = docs["registry"]
payload["dictionary"] = docs["dictionary"]
payload["gaps"] = docs["gaps"]
js = "window.DATA = " + json.dumps(payload, separators=(",", ":")) + ";\n"
open(f"{SITE}/data.js", "w").write(js)
print(f"data.js {len(js)/1024:.0f} KB | conditions {len(payload['conditions'])} scores {len(payload['scores'])} curve {len(payload['curve'])}")
