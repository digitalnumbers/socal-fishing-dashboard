"""Extended-range (day 7-30) outlook builder for SoCal Fishing Intelligence.

DESIGN CONTRACT
---------------
This module is strictly ADDITIVE. It does not modify, rescore or regenerate anything inside the
existing 21-day window (14 days hindcast + ~7 days forecast). The near-term product built by
build_dataset.py remains the authoritative high-confidence view and its methodology is untouched.

To guarantee the extended outlook is scored with *exactly* the same species response model as the
near-term forecast, this module does not reimplement the scoring engine: it lifts section 6 of
build_dataset.py verbatim at runtime (see `load_scoring_engine`). If the near-term model changes,
the extended outlook changes with it, automatically and identically.

CONFIDENCE TIERS
----------------
  tier 1  "High confidence"      days 1-7    built by build_dataset.py (NOT produced here)
  tier 2  "Moderate confidence"  days 8-14   deterministic model wind/swell blended toward
                                             climatology + CPC 8-14 day outlook + real tide/moon
  tier 3  "Outlook / trend only" days 15-30  climatology + ENSO analog + CPC week 3-4 / monthly
                                             + real tide/moon. NO daily wind or swell numbers.

WHAT IS A FORECAST AND WHAT IS CLIMATOLOGY
------------------------------------------
  FORECAST (a model or forecaster predicted this specific date)
    - cpc_temp_cat / cpc_temp_prob / cpc_prcp_cat / cpc_prcp_prob   CPC probabilistic outlook
    - wave_model_ft / wind_model_kt / pressure_model_hpa            Open-Meteo, day <= 15 only
  ASTRONOMICAL (calculable exactly, any lead time -- not a forecast, not climatology)
    - tide_range_ft, max_exchange_rate_ft_h, best_water_hour, first_high_hour  CO-OPS harmonics
    - moon_illum, moon_phase, moon_age_days, sunrise_hour, sunset_hour
  CLIMATOLOGY / SEASONAL AVERAGE (a historical day-of-year pattern, not a prediction)
    - sst_norm_f, wave_norm_ft, wind_norm_kt, climatological_score
  ENSO ANALOG (historical composite conditioned on the current ENSO regime)
    - enso_expected_anom_f, analog_years
  BLENDED (weighted mix of the above -- weights reported per row)
    - sst_proj_f, sst_anom_proj_f, wave_proj_ft, wind_proj_kt, score_outlook

Usage:
  python3 pipeline/build_extended.py [--raw DIR] [--csv DIR] [--today YYYY-MM-DD]
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(REPO, "pipeline", "config")
DEFAULT_RAW = os.environ.get("SOCAL_RAW_EXT", os.path.join(REPO, ".refresh-cache", "extended"))
DEFAULT_CSV = os.path.join(REPO, "dataset", "csv")

HORIZON = 30                 # last lead day produced
INSHORE = {"sd_bay", "mission_bay", "surf_zone"}

# NDBC 46086 (San Clemente Basin) is the only station in buoy_climatology.csv with a full
# 366-day wind normal, so it is the regional fallback when a zone's own buoys have no wind
# climatology. Zones that fall back are flagged in `wind_norm_is_regional_fallback`.
WIND_FALLBACK_BUOY = "46086"
SD_PROXY = (-117.15, 32.70)  # onshore San Diego metro point used to sample CPC CONUS outlooks

TIERS = [
    # lead_days is zero-based: build date through +6 is the seven-day view.
    {"tier": 1, "lead_from": 0, "lead_to": 6, "key": "high",
     "label": "High confidence", "short": "Days 1-7",
     "basis": "Observed conditions plus deterministic marine and atmospheric forecast",
     "shows": "Specific daily SST, swell, wind, tide and score",
     "produced_by": "build_dataset.py (near-term model, unchanged)"},
    {"tier": 2, "lead_from": 7, "lead_to": 14, "key": "moderate",
     "label": "Moderate confidence", "short": "Days 8-14",
     "basis": "Deterministic model blended toward day-of-year climatology, plus the CPC 8-14 day "
              "probabilistic outlook, plus exact tide and lunar values",
     "shows": "Daily score with an uncertainty band, plus wind blended toward climatology. "
              "Swell is published only for the first day or two of this tier, because the marine model runs "
              "a shorter horizon than the atmospheric one.",
     "produced_by": "build_extended.py"},
    {"tier": 3, "lead_from": 15, "lead_to": 30, "key": "outlook",
     "label": "Outlook / trend only", "short": "Days 15-30",
     "basis": "Day-of-year climatology weighted with the ENSO-regime historical analog composite, "
              "plus the CPC week 3-4 and monthly probabilistic outlooks, plus exact tide and lunar "
              "values. No deterministic weather model is skilful at this range.",
     "shows": "Trend direction versus the seasonal norm and a tide/moon quality signal. "
              "Daily wind and swell numbers are deliberately NOT published.",
     "produced_by": "build_extended.py"},
]


# ======================================================================== scoring engine reuse
def load_scoring_engine():
    """Exec section 6 of build_dataset.py so the extended outlook uses the identical model.

    Only the pure scoring functions are lifted (trapezoid .. climatological_curve); none of
    build_dataset.py's I/O or fetch code runs.
    """
    src = open(os.path.join(REPO, "pipeline", "build_dataset.py")).read()
    m = re.search(r"# =+ 6\. scoring engine\n(.*?)\n# =+ 7\.", src, re.S)
    if not m:
        raise RuntimeError("could not locate the scoring engine block in build_dataset.py")
    zones = json.load(open(os.path.join(CFG, "zones.json")))
    species = json.load(open(os.path.join(CFG, "species.json")))
    ns = {"math": math, "np": np, "pd": pd, "json": json,
          "datetime": dt.datetime, "timedelta": dt.timedelta, "date": dt.date,
          "ZONES": zones, "SPECIES": species,
          "ZMAP": {z["id"]: z for z in zones}, "SMAP": {s["id"]: s for s in species}}
    exec(compile(m.group(1), "build_dataset.py::scoring", "exec"), ns)
    return ns, zones, species


# ======================================================================== CPC outlook sampling
def _rings(shape):
    pts = shape.points
    idx = list(shape.parts) + [len(pts)]
    return [pts[idx[i]:idx[i + 1]] for i in range(len(idx) - 1)]


def _ring_contains(ring, pt):
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def sample_cpc(shp_path, pt=SD_PROXY):
    """Return the CPC outlook category and probability whose polygon contains `pt`.

    CPC posts one polygon per (category, probability) contour; even-odd ring winding handles the
    holes. Returns the highest-probability match, or the equal-chances default if none contains it.
    """
    try:
        import shapefile  # pyshp
    except ImportError:
        return None
    if not os.path.exists(shp_path):
        return None
    r = shapefile.Reader(shp_path)
    names = [f[0] for f in r.fields[1:]]
    ci = names.index("Cat")
    pi = names.index("Prob")
    hits = []
    for sr in r.shapeRecords():
        if sum(_ring_contains(g, pt) for g in _rings(sr.shape)) % 2 == 1:
            rec = dict(zip(names, list(sr.record)))
            hits.append((float(rec["Prob"]), str(rec["Cat"]), rec))
    if not hits:
        return {"cat": "EC", "prob": 33.3, "record": {}}
    hits.sort(reverse=True)
    prob, cat, rec = hits[0]
    return {"cat": cat, "prob": prob, "record": rec}


def cpc_signal_f(cat, prob, scale):
    """Convert a CPC tercile probability into a small expected SST anomaly nudge, in degF.

    CPC terciles describe *air* temperature over land. Coastal SST responds far less and more
    slowly, so the mapping is deliberately weak: a 40% above-normal call becomes +0.16 degF at
    the 8-14 day scale, not a degree. Equal-chances and Normal produce exactly zero.
    """
    if cat is None:
        return 0.0, 0.0
    c = str(cat).strip().lower()
    direction = 1.0 if c == "above" else (-1.0 if c == "below" else 0.0)
    if direction == 0.0:
        return 0.0, 0.0
    confidence = max(0.0, (float(prob) - 33.3) / 66.7)   # 33.3% -> 0, 100% -> 1
    return direction * confidence * scale, confidence


def collect_cpc(raw):
    """Assemble the CPC outlook stack for San Diego, ordered from shortest to longest lead."""
    out = {}

    def one(key, pattern, kind, scale, window_from=None, window_to=None):
        hit = None
        product_dir = os.path.join(raw, "cpc", pattern.split("/", 1)[0])
        sample_path = os.path.join(product_dir, f"_sample_{key}.json")
        for p in sorted(glob.glob(os.path.join(raw, "cpc", pattern)), reverse=True):
            hit = sample_cpc(p)
            if hit:
                hit["shapefile"] = os.path.relpath(p, raw)
                os.makedirs(product_dir, exist_ok=True)
                tmp = sample_path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(hit, fh, indent=1, default=str)
                os.replace(tmp, sample_path)
                break
        if not hit and os.path.exists(sample_path):
            try:
                with open(sample_path) as fh:
                    hit = json.load(fh)
            except (OSError, ValueError, TypeError):
                hit = None
        if not hit:
            return
        rec = hit.get("record", {})
        sig, conf = cpc_signal_f(hit["cat"], hit["prob"], scale)
        out[key] = {
            "product": key, "kind": kind, "cat": hit["cat"], "prob": hit["prob"],
            "signal_f": round(sig, 3), "confidence": round(conf, 3),
            "issued": str(rec.get("Fcst_Date") or ""),
            "valid_from": str(rec.get("Start_Date") or window_from or ""),
            "valid_to": str(rec.get("End_Date") or window_to or ""),
            "valid_label": str(rec.get("Valid_Seas") or ""),
            "shapefile": hit["shapefile"],
        }

    # 8-14 day: real short-range probabilistic forecast, strongest extended-range signal
    one("cpc_814_temp", "814temp/*.shp", "temperature", 1.6)
    one("cpc_814_prcp", "814prcp/*.shp", "precipitation", 0.0)
    # weeks 3-4 (roughly days 15-28): real but much weaker probabilistic forecast
    one("cpc_wk34_temp", "wk34temp/*.shp", "temperature", 1.1)
    one("cpc_wk34_prcp", "wk34prcp/*.shp", "precipitation", 0.0)
    # monthly outlook for the calendar month that the tail of the window falls in
    one("cpc_month_temp", "seastemp/lead14_*_temp.shp", "temperature", 0.8)
    one("cpc_season_temp", "seastemp/lead1_*_temp.shp", "temperature", 0.6)
    return out


def cpc_for_lead(cpc, lead):
    """Pick which CPC product governs a given lead day, with its blend weight."""
    if 7 <= lead <= 14 and "cpc_814_temp" in cpc:
        return cpc["cpc_814_temp"], cpc.get("cpc_814_prcp"), 0.55
    if 15 <= lead <= 28 and "cpc_wk34_temp" in cpc:
        return cpc["cpc_wk34_temp"], cpc.get("cpc_wk34_prcp"), 0.32
    if "cpc_month_temp" in cpc:
        return cpc["cpc_month_temp"], None, 0.20
    if "cpc_season_temp" in cpc:
        return cpc["cpc_season_temp"], None, 0.15
    return None, None, 0.0


# ======================================================================== astronomical inputs
def tide_features_ext(raw):
    """Daily tide metrics from the 40-day harmonic prediction pull.

    Mirrors lib_parse.tide_features so extended-range tide fields are computed exactly the same
    way as near-term ones. Harmonic predictions are astronomical, so they are as accurate on
    day 30 as on day 1.
    """
    rows = []
    for path in sorted(glob.glob(os.path.join(raw, "coops_*_predictions_ext.json"))):
        st = os.path.basename(path).split("_")[1]
        preds = json.load(open(path)).get("predictions", [])
        g = pd.DataFrame(preds)
        if g.empty:
            continue
        g["ts"] = pd.to_datetime(g.t)
        g["v"] = g.v.astype(float)
        g = g[g.type.notna()].drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        g["dt_h"] = g.ts.diff().dt.total_seconds() / 3600
        g["rate"] = (g.v.diff() / g.dt_h).abs()
        g["date"] = g.ts.dt.date
        for d, gd in g.groupby("date"):
            n_in_day = len(gd)
            widened = False
            if n_in_day < 2:
                # Genuine mixed/diurnal day: SoCal tides occasionally produce only one hi/lo
                # extremum inside a calendar day (typically at neaps). Rather than drop the day,
                # widen the window to the bracketing extrema so a real range is still defined.
                # Use the day's extremum plus ONE neighbour -- the closer of the two in height --
                # so the reported range is a single real tidal exchange. Spanning both neighbours
                # would sum two exchanges across ~2 days and inflate tide_range_ft, which showed up
                # as spurious double-digit score spikes on those days.
                i = int(gd.index[0])
                cands = [j for j in (i - 1, i + 1) if 0 <= j < len(g)]
                if not cands:
                    continue
                nb = min(cands, key=lambda j: abs(float(g.v.iloc[j]) - float(g.v.iloc[i])))
                gd = g.loc[min(i, nb):max(i, nb)]
                widened = True
                if len(gd) < 2:
                    continue
            best = gd.loc[gd.rate.idxmax()] if gd.rate.notna().any() else gd.iloc[-1]
            mid = best.ts - dt.timedelta(hours=float(best.dt_h or 0) / 2)
            rows.append({"tide_station": st, "date": pd.Timestamp(d),
                         "tide_high_ft": gd.v.max(), "tide_low_ft": gd.v.min(),
                         "tide_range_ft": gd.v.max() - gd.v.min(),
                         "tide_exchanges": n_in_day, "tide_window_widened": widened,
                         "max_exchange_rate_ft_h": float(gd.rate.max(skipna=True)) if gd.rate.notna().any() else np.nan,
                         "best_water_hour": mid.hour + mid.minute / 60,
                         "first_high_hour": (gd[gd.type == "H"].ts.dt.hour.min()
                                             if (gd.type == "H").any() else np.nan)})
    return pd.DataFrame(rows).drop_duplicates(subset=["tide_station", "date"])


def moon_phase(d):
    """Meeus low-precision lunar phase -- identical to lib_parse.moon_phase."""
    t = dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc)
    jd = t.timestamp() / 86400.0 + 2440587.5
    T = (jd - 2451545.0) / 36525
    D = (297.8501921 + 445267.1114034 * T - 0.0018819 * T ** 2) % 360
    M = (357.5291092 + 35999.0502909 * T) % 360
    Mp = (134.9633964 + 477198.8675055 * T) % 360
    r = math.radians
    i = (180 - D - 6.289 * math.sin(r(Mp)) + 2.100 * math.sin(r(M))
         - 1.274 * math.sin(r(2 * D - Mp)) - 0.658 * math.sin(r(2 * D))
         - 0.214 * math.sin(r(2 * Mp)) - 0.110 * math.sin(r(D))) % 360
    illum = (1 + math.cos(r(i))) / 2
    syn = 29.530588
    age = ((jd - 2451550.1) / syn % 1) * syn
    names = [(1.5, "New"), (6.0, "Waxing Crescent"), (9.0, "First Quarter"), (13.5, "Waxing Gibbous"),
             (16.5, "Full"), (21.0, "Waning Gibbous"), (24.0, "Last Quarter"),
             (28.1, "Waning Crescent"), (30, "New")]
    return illum, i, next(n for lim, n in names if age <= lim), age


def sun_times(d, lat=32.72, lon=-117.22):
    """NOAA solar equations -- identical to lib_parse.sun_times."""
    n = d.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1 + 0.5)
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                   - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    la = math.radians(lat)
    cosH = (math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl))) - math.tan(la) * math.tan(decl)
    ha = math.degrees(math.acos(max(-1, min(1, cosH))))
    sr = (720 + 4 * (-lon - ha) - eq) / 60 - 8
    ss = (720 + 4 * (-lon + ha) - eq) / 60 - 8
    if 3 <= d.month <= 10:
        sr += 1
        ss += 1
    return sr, ss, ss - sr


# ======================================================================== model + climatology
def openmeteo_16day(raw):
    """Deterministic 16-day model, day 7-15 of the extended window. Beyond its horizon: nothing."""
    p = os.path.join(raw, "openmeteo_16day.json")
    if not os.path.exists(p):
        return pd.DataFrame()
    blob = json.load(open(p))
    rows = []
    for zid, rec in blob.items():
        mar = (rec.get("marine") or {}).get("daily") or {}
        wx = (rec.get("weather") or {}).get("daily") or {}
        for i, day in enumerate(mar.get("time", [])):
            def g(dd, k, j=i, f=1.0):
                v = dd.get(k)
                return round(v[j] * f, 3) if v and j < len(v) and v[j] is not None else np.nan
            rows.append({"zone_id": zid, "date": pd.Timestamp(day),
                         "wave_model_ft": g(mar, "wave_height_max", f=3.28084),
                         "wave_model_period_s": g(mar, "wave_period_max"),
                         "swell_model_ft": g(mar, "swell_wave_height_max", f=3.28084)})
        for i, day in enumerate(wx.get("time", [])):
            hit = next((r for r in rows if r["zone_id"] == zid and r["date"] == pd.Timestamp(day)), None)
            if hit is None:
                hit = {"zone_id": zid, "date": pd.Timestamp(day)}
                rows.append(hit)

            def gw(k, f=1.0):
                v = wx.get(k)
                return round(v[i] * f, 3) if v and i < len(v) and v[i] is not None else np.nan
            hit["wind_model_kt"] = gw("wind_speed_10m_max", 0.539957)
            hit["gust_model_kt"] = gw("wind_gusts_10m_max", 0.539957)
            hit["pressure_model_hpa"] = gw("pressure_msl_mean")
            hit["cloud_model_pct"] = gw("cloud_cover_mean")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["zone_id", "date"])
        df["pressure_trend_model_24h"] = df.groupby("zone_id").pressure_model_hpa.diff()
    return df


def zone_climatology(csv, zones):
    """Day-of-year SST / wave / wind normals per zone, using the same authority rule as
    build_dataset.py: inshore zones use the CO-OPS shore thermistor climatology, everything
    else uses the MUR satellite climatology; waves and wind come from the primary NDBC buoy."""
    sst = pd.read_csv(os.path.join(csv, "sst_climatology.csv"))
    sh = pd.read_csv(os.path.join(csv, "shore_sst_climatology.csv"))
    bc = pd.read_csv(os.path.join(csv, "buoy_climatology.csv"))
    frames = []
    for z in zones:
        if z["id"] in INSHORE and not sh.empty:
            c = sh[sh.tide_station.astype(str) == str(z["tide_station"])].rename(columns={
                "shoresst_norm": "sst_norm_f", "shoresst_sd": "sst_sd_f",
                "shoresst_p10": "sst_p10_f", "shoresst_p90": "sst_p90_f",
                "shoresst_years": "sst_years"})
            src = "CO-OPS shore thermistor climatology"
        else:
            c = sst[sst.zone_id == z["id"]].rename(columns={
                "sst_norm": "sst_norm_f", "sst_sd": "sst_sd_f",
                "sst_p10": "sst_p10_f", "sst_p90": "sst_p90_f"})
            src = "MUR 1 km satellite climatology"
        keep = [k for k in ["doy", "sst_norm_f", "sst_sd_f", "sst_p10_f", "sst_p90_f", "sst_years"] if k in c.columns]
        c = c[keep].copy()
        if c.empty:
            continue
        # Wave and wind climatology are resolved with an explicit per-field fallback chain: the
        # zone's own buoys in priority order, then the regional fallback buoy. This matters because
        # coverage is very uneven in buoy_climatology.csv -- 46225/46232/46258 carry wave normals but
        # NO wind normals at all, and SDBC1 carries neither. Taking only buoys[0] left wind_norm
        # null for most zones, which silently knocked the whole fishability/access gate out of the
        # climatological score and produced a large false step at the day-14/15 tier boundary.
        # The station that actually supplied each field is recorded so provenance stays honest.
        own = [str(s) for s in z["buoys"]]
        # Wind gets the regional fallback; SWELL DOES NOT. Significant wave height is far too
        # site-specific -- substituting offshore 46086 (San Clemente Basin, ~8 ft normals) for a
        # protected zone like San Diego Bay would invent large swell inside a harbour. Where a
        # zone's own buoys have no wave normal the field stays null and the scoring engine drops
        # the swell driver and renormalizes, which is the honest behaviour.
        chains = {"wave": own, "wind": own + [WIND_FALLBACK_BUOY]}
        picked = {}
        for field, p90 in (("wave", "wave_p90"), ("wind", "wind_p90")):
            col = f"{field}_norm"
            for st in chains[field]:
                b = bc[(bc.station.astype(str) == st) & bc[col].notna()]
                if b.empty:
                    continue
                take = [k for k in ["doy", col, p90] + (["wave_years"] if field == "wave" else []) if k in b.columns]
                c = c.merge(b[take], on="doy", how="left")
                picked[field] = st
                break
            else:
                c[col] = np.nan
                c[p90] = np.nan
                picked[field] = None
        if "wave_years" not in c.columns:
            c["wave_years"] = np.nan
        c["zone_id"] = z["id"]
        c["sst_climatology_source"] = src
        c["wave_norm_station"] = picked.get("wave")
        c["wind_norm_station"] = picked.get("wind")
        c["wind_norm_is_regional_fallback"] = (picked.get("wind") == WIND_FALLBACK_BUOY
                                               and WIND_FALLBACK_BUOY not in own)
        frames.append(c)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ======================================================================== ENSO analog machinery
def enso_state(csv):
    cur = pd.read_csv(os.path.join(csv, "enso_current.csv")).iloc[0].to_dict()
    oni = pd.read_csv(os.path.join(csv, "enso_oni.csv"))
    return cur, oni


def analog_years(oni, cur, target_months, tol=0.5, min_year=1950):
    """Historical years whose ONI in this calendar window resembles the present regime.

    Match rule: same ENSO sign as now AND seasonal ONI within +/- tol of the current value,
    evaluated over the calendar months the outlook window spans.
    """
    o = oni[(oni.year >= min_year) & oni.oni.notna()].copy()
    now = float(cur["oni"])
    sign = np.sign(now)
    win = o[o.month.isin(target_months)]
    agg = win.groupby("year").agg(window_oni=("oni", "mean"), n_seasons=("oni", "size")).reset_index()
    agg = agg[agg.year < int(cur["year"])]
    agg["delta"] = (agg.window_oni - now).abs()
    agg = agg[(np.sign(agg.window_oni) == sign) & (agg.delta <= tol)]
    return agg.sort_values("delta").reset_index(drop=True)


def observed_analog_anomaly(csv, zones, analogs, doys):
    """Observed satellite SST anomaly in the analog years for this calendar window, per zone.

    Only years present in the MUR record (2015+) can contribute an observed value, so this is a
    small sample and is reported as supporting evidence, never as the primary signal.
    """
    p = os.path.join(csv, "mur_history.csv")
    if not os.path.exists(p) or analogs.empty:
        return pd.DataFrame()
    m = pd.read_csv(p)
    m["date"] = pd.to_datetime(m.date)
    m["doy"] = m.date.dt.dayofyear
    m["year"] = m.date.dt.year
    clim = pd.read_csv(os.path.join(csv, "sst_climatology.csv"))[["zone_id", "doy", "sst_norm"]]
    m = m.merge(clim, on=["zone_id", "doy"], how="left")
    m["anom"] = m.sst_f - m.sst_norm
    yrs = set(analogs.year.tolist())
    sel = m[m.year.isin(yrs) & m.doy.isin(doys) & m.anom.notna()]
    if sel.empty:
        return pd.DataFrame()
    return (sel.groupby(["zone_id", "year"])
               .agg(observed_anom_f=("anom", "mean"), n_days=("anom", "size"))
               .reset_index())


def enso_expected_anomaly(csv, zones, cur, months):
    """Expected zone SST anomaly for the current ENSO regime, from the project's own composites.

    Primary: enso_zone_composite (mean observed anomaly by zone x month x regime).
    Fallback: enso_sensitivity (regression slope of zone anomaly on ONI) x the current ONI.
    """
    comp_p = os.path.join(csv, "enso_zone_composite.csv")
    sens_p = os.path.join(csv, "enso_sensitivity.csv")
    comp = pd.read_csv(comp_p) if os.path.exists(comp_p) else pd.DataFrame()
    sens = pd.read_csv(sens_p) if os.path.exists(sens_p) else pd.DataFrame()
    regime = str(cur["simple_regime"])
    oni_now = float(cur["oni"])
    out = {}
    for z in zones:
        for mo in months:
            val, sd, basis, n = np.nan, np.nan, "unavailable", 0
            if not comp.empty:
                g = comp[(comp.zone_id == z["id"]) & (comp.month == mo) & (comp.simple_regime == regime)]
                if not g.empty and pd.notna(g.iloc[0].mean_anom_f):
                    val = float(g.iloc[0].mean_anom_f)
                    sd = float(g.iloc[0].sd_anom_f) if pd.notna(g.iloc[0].sd_anom_f) else np.nan
                    n = int(g.iloc[0].n_days)
                    basis = f"enso_zone_composite ({regime}, month {mo}, n={n} days)"
            if (pd.isna(val)) and not sens.empty:
                g = sens[(sens.zone_id == z["id"]) & (sens.month == mo)]
                if not g.empty and pd.notna(g.iloc[0].anom_f_per_oni):
                    val = float(g.iloc[0].anom_f_per_oni) * oni_now
                    n = int(g.iloc[0].n_days)
                    basis = f"enso_sensitivity slope x ONI {oni_now:+.2f} (r={g.iloc[0].r:.2f}, n={n})"
            out[(z["id"], mo)] = {"anom_f": val, "sd_f": sd, "basis": basis, "n_days": n}
    return out


# ======================================================================== lead-time weighting
def lead_weights(lead):
    """Split the projected SST anomaly between persistence and the ENSO analog composite.

    Persistence of the currently observed anomaly decays with an e-folding time of 8 days; the
    ENSO analog composite picks up exactly what persistence gives back, so the ENSO share rises
    monotonically with lead time as required. The two always sum to 1.

    The CPC outlook is applied separately as an *additive* nudge (see `cpc_nudge`), not as a third
    share of the blend. Making it a share would let the persistence weight jump back up whenever
    the governing CPC product changes at a tier boundary, which is not physical.
    """
    w_persist = math.exp(-(lead - 6) / 8.0)
    return round(w_persist, 4), round(1.0 - w_persist, 4)


def band_multiplier(lead):
    """Anomaly uncertainty widens with lead time, in units of the climatological SD."""
    return 0.55 + 0.038 * max(0, lead - 6)


def trend_label(delta):
    if delta is None or (isinstance(delta, float) and math.isnan(delta)):
        return "unknown"
    if delta >= 8:
        return "well above typical"
    if delta >= 3:
        return "above typical"
    if delta > -3:
        return "near typical"
    if delta > -8:
        return "below typical"
    return "well below typical"


# ======================================================================== main build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=DEFAULT_RAW)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--today", default=None)
    a = ap.parse_args()
    raw, csv = a.raw, a.csv

    ns, zones, species = load_scoring_engine()
    score_row = ns["score_row"]
    ZMAP = {z["id"]: z for z in zones}

    cond = pd.read_csv(os.path.join(csv, "conditions_daily.csv"))
    cond["date"] = pd.to_datetime(cond.date)
    today = pd.Timestamp(a.today) if a.today else pd.Timestamp(sorted(cond.date.unique())[14])
    near_last = pd.Timestamp(cond.date.max())
    start = max(near_last + pd.Timedelta(days=1), today + pd.Timedelta(days=7))
    dates = pd.date_range(start, today + pd.Timedelta(days=HORIZON), freq="D")
    print(f"today {today.date()} | near-term ends {near_last.date()} | "
          f"extended {dates[0].date()} -> {dates[-1].date()} ({len(dates)} days)")

    clim = zone_climatology(csv, zones)
    tide = tide_features_ext(raw)
    model = openmeteo_16day(raw)
    cpc = collect_cpc(raw)
    cur, oni = enso_state(csv)
    months = sorted({d.month for d in dates})
    doys = sorted({int(d.dayofyear) for d in dates})
    exp_anom = enso_expected_anomaly(csv, zones, cur, months)
    ana = analog_years(oni, cur, months)
    obs_ana = observed_analog_anomaly(csv, zones, ana, doys)
    print(f"ENSO {cur['simple_regime']} ONI {cur['oni']:+.2f} | "
          f"{len(ana)} analog years | CPC products {list(cpc)}")

    # current anomaly to persist forward: the most recent observed day per zone
    last_obs = (cond[cond.date <= today].sort_values("date").groupby("zone_id").tail(1)
                .set_index("zone_id")[["sst_anom_f", "date"]].to_dict("index"))

    # ---------------------------------------------------------------- extended_outlook rows
    rows = []
    for d in dates:
        lead = int((d - today).days)
        tier = 2 if lead <= 14 else 3
        tinfo = next(t for t in TIERS if t["tier"] == tier)
        doy = int(d.dayofyear)
        mo = int(d.month)
        cpc_t, cpc_p, w_cpc_raw = cpc_for_lead(cpc, lead)
        illum, _, phase, age = moon_phase(d.date())
        sr, ss, dl = sun_times(d.date())
        for z in zones:
            c = clim[(clim.zone_id == z["id"]) & (clim.doy == doy)]
            if c.empty:
                continue
            c = c.iloc[0]
            ea = exp_anom.get((z["id"], mo), {"anom_f": np.nan, "sd_f": np.nan, "basis": "unavailable", "n_days": 0})
            anom_now = last_obs.get(z["id"], {}).get("sst_anom_f", np.nan)
            enso_anom = ea["anom_f"]

            # ---- blend the SST anomaly: (persistence + ENSO analog) + additive CPC nudge
            wp, we = lead_weights(lead)
            parts, wsum = 0.0, 0.0
            if pd.notna(anom_now):
                parts += wp * float(anom_now); wsum += wp
            if pd.notna(enso_anom):
                parts += we * float(enso_anom); wsum += we
            anom_core = parts / wsum if wsum > 0 else np.nan
            wc = w_cpc_raw if (cpc_t and cpc_t["signal_f"] != 0) else 0.0
            nudge = wc * cpc_t["signal_f"] if wc > 0 else 0.0
            anom_proj = anom_core + nudge if pd.notna(anom_core) else np.nan

            sd = float(c.sst_sd_f) if pd.notna(c.sst_sd_f) else np.nan
            if pd.isna(sd) and pd.notna(ea["sd_f"]):
                sd = float(ea["sd_f"])
            halfband = (sd * band_multiplier(lead)) if pd.notna(sd) else np.nan
            sst_norm = float(c.sst_norm_f) if pd.notna(c.sst_norm_f) else np.nan
            sst_proj = sst_norm + anom_proj if pd.notna(sst_norm) and pd.notna(anom_proj) else np.nan

            # ---- wind and swell: model-blended in tier 2, climatology-only (unpublished) in tier 3
            mrow = model[(model.zone_id == z["id"]) & (model.date == d)]
            mrow = mrow.iloc[0] if not mrow.empty else None
            wave_norm = float(c.wave_norm) if pd.notna(c.get("wave_norm")) else np.nan
            wind_norm = float(c.wind_norm) if pd.notna(c.get("wind_norm")) else np.nan
            wave_model = float(mrow.wave_model_ft) if mrow is not None and pd.notna(mrow.get("wave_model_ft")) else np.nan
            wind_model = float(mrow.wind_model_kt) if mrow is not None and pd.notna(mrow.get("wind_model_kt")) else np.nan
            per_model = float(mrow.wave_model_period_s) if mrow is not None and pd.notna(mrow.get("wave_model_period_s")) else np.nan
            prs_trend = float(mrow.pressure_trend_model_24h) if mrow is not None and pd.notna(mrow.get("pressure_trend_model_24h")) else np.nan

            # Wave and wind are treated independently because their model horizons differ: the
            # Open-Meteo marine (swell) fields run out several days before the atmospheric fields.
            # In tier 3 neither is published no matter what the model says -- day 15+ deterministic
            # weather has no skill, and publishing a number there would fabricate precision.
            bw = math.exp(-(lead - 6) / 7.0) if tier == 2 else 0.0
            if tier == 2 and pd.notna(wave_model):
                wave_proj = bw * wave_model + (1 - bw) * wave_norm if pd.notna(wave_norm) else wave_model
                wave_basis = f"model blended toward climatology (model weight {bw:.2f})"
            else:
                wave_proj = np.nan
                wave_basis = ("climatology only - deterministic swell model does not reach this lead"
                              if tier == 2 else
                              "climatology only - no skilful deterministic swell at this lead")
            if tier == 2 and pd.notna(wind_model):
                wind_proj = bw * wind_model + (1 - bw) * wind_norm if pd.notna(wind_norm) else wind_model
                wind_basis = f"model blended toward climatology (model weight {bw:.2f})"
            else:
                wind_proj = np.nan
                wind_basis = ("climatology only - deterministic wind model does not reach this lead"
                              if tier == 2 else
                              "climatology only - no skilful deterministic wind at this lead")
            sea_published = bool(pd.notna(wave_proj) or pd.notna(wind_proj))
            sea_basis = ("model-blended" if (pd.notna(wave_proj) and pd.notna(wind_proj))
                         else ("partially model-blended" if sea_published else "climatology only"))

            # sea state actually fed to the score: the published projection where it exists,
            # otherwise the day-of-year climatological normal (never a fabricated number)
            wave_used = wave_proj if pd.notna(wave_proj) else wave_norm
            wind_used = wind_proj if pd.notna(wind_proj) else wind_norm
            period_used = per_model if pd.notna(wave_proj) else np.nan
            # The pressure driver is deliberately EXCLUDED from the extended score at both tiers.
            # It is only available from the deterministic model (tier 2) and vanishes at tier 3.
            # Feeding it in for days 8-14 and then dropping it at day 15 makes the geometric-mean
            # weights renormalize, which produced a ~19-point cliff at the tier boundary that was
            # pure construction artifact, not signal. Holding one driver set across the whole 8-30
            # day range keeps extended scores comparable with each other. The raw model value is
            # still published as `pressure_trend_model_24h` for reference.
            prs_used = np.nan

            tr = tide[(tide.tide_station.astype(str) == str(z["tide_station"])) & (tide.date == d)]
            tr = tr.iloc[0] if not tr.empty else None

            rows.append({
                "date": d.strftime("%Y-%m-%d"), "zone_id": z["id"], "zone": z["name"], "band": z["band"],
                "lead_days": lead, "doy": doy, "month": mo,
                "confidence_tier": tier, "confidence_key": tinfo["key"], "confidence_label": tinfo["label"],
                # --- climatology (seasonal average, NOT a forecast)
                "sst_norm_f": round(sst_norm, 2) if pd.notna(sst_norm) else np.nan,
                "sst_sd_f": round(sd, 2) if pd.notna(sd) else np.nan,
                "sst_years": c.get("sst_years"),
                "sst_climatology_source": c.sst_climatology_source,
                "wave_norm_ft": round(wave_norm, 2) if pd.notna(wave_norm) else np.nan,
                "wind_norm_kt": round(wind_norm, 2) if pd.notna(wind_norm) else np.nan,
                "wave_norm_station": c.get("wave_norm_station"),
                "wind_norm_station": c.get("wind_norm_station"),
                "wind_norm_is_regional_fallback": bool(c.get("wind_norm_is_regional_fallback")),
                # --- blended projection
                "sst_proj_f": round(sst_proj, 2) if pd.notna(sst_proj) else np.nan,
                "sst_anom_proj_f": round(anom_proj, 2) if pd.notna(anom_proj) else np.nan,
                "sst_proj_lo_f": round(sst_proj - halfband, 2) if pd.notna(sst_proj) and pd.notna(halfband) else np.nan,
                "sst_proj_hi_f": round(sst_proj + halfband, 2) if pd.notna(sst_proj) and pd.notna(halfband) else np.nan,
                "sst_basis": ("persistence + ENSO analog + CPC outlook" if wc > 0
                              else "persistence + ENSO analog"),
                "w_persistence": wp, "w_enso_analog": we, "w_cpc_outlook": round(wc, 4),
                # --- deterministic model (forecast, only where it exists)
                "wave_model_ft": round(wave_model, 2) if pd.notna(wave_model) else np.nan,
                "wind_model_kt": round(wind_model, 2) if pd.notna(wind_model) else np.nan,
                "wave_model_period_s": round(per_model, 1) if pd.notna(per_model) else np.nan,
                "pressure_trend_model_24h": round(prs_trend, 2) if pd.notna(prs_trend) else np.nan,
                "wave_proj_ft": round(wave_proj, 2) if pd.notna(wave_proj) else np.nan,
                "wind_proj_kt": round(wind_proj, 2) if pd.notna(wind_proj) else np.nan,
                "sea_state_basis": sea_basis, "sea_state_published": sea_published,
                "wave_basis": wave_basis, "wind_basis": wind_basis,
                "model_blend_weight": round(bw, 3),
                "cpc_nudge_f": round(nudge, 3),
                "sst_anom_core_f": round(anom_core, 2) if pd.notna(anom_core) else np.nan,
                # --- CPC probabilistic outlook (forecast)
                "cpc_product": cpc_t["product"] if cpc_t else None,
                "cpc_temp_cat": cpc_t["cat"] if cpc_t else None,
                "cpc_temp_prob": cpc_t["prob"] if cpc_t else np.nan,
                "cpc_temp_signal_f": cpc_t["signal_f"] if cpc_t else np.nan,
                "cpc_valid_from": cpc_t["valid_from"] if cpc_t else None,
                "cpc_valid_to": cpc_t["valid_to"] if cpc_t else None,
                "cpc_valid_label": cpc_t["valid_label"] if cpc_t else None,
                "cpc_prcp_cat": cpc_p["cat"] if cpc_p else None,
                "cpc_prcp_prob": cpc_p["prob"] if cpc_p else np.nan,
                # --- ENSO analog
                "enso_regime": cur["simple_regime"], "oni": cur["oni"],
                "enso_expected_anom_f": round(float(enso_anom), 2) if pd.notna(enso_anom) else np.nan,
                "enso_analog_basis": ea["basis"],
                "analog_years": ",".join(str(int(y)) for y in ana.year.head(8)),
                # --- astronomical (exact at any lead)
                "tide_range_ft": round(float(tr.tide_range_ft), 2) if tr is not None else np.nan,
                "tide_window_widened": bool(tr.tide_window_widened) if tr is not None else False,
                "max_exchange_rate_ft_h": round(float(tr.max_exchange_rate_ft_h), 3) if tr is not None else np.nan,
                "best_water_hour": round(float(tr.best_water_hour), 2) if tr is not None else np.nan,
                "first_high_hour": (float(tr.first_high_hour) if tr is not None and pd.notna(tr.first_high_hour) else np.nan),
                "tide_station": z["tide_station"],
                "moon_illum": round(illum, 3), "moon_phase": phase, "moon_age_days": round(age, 2),
                "sunrise_hour": round(sr, 2), "sunset_hour": round(ss, 2), "daylight_hours": round(dl, 2),
                # scoring inputs actually used
                "xx_sst_used": sst_proj, "xx_sst_lo": sst_proj - halfband if pd.notna(halfband) else sst_proj,
                "xx_sst_hi": sst_proj + halfband if pd.notna(halfband) else sst_proj,
                "xx_wave_used": wave_used, "xx_wind_used": wind_used,
                "xx_period_used": period_used,
                "xx_prs_used": prs_used,
            })
    ext = pd.DataFrame(rows)

    # ---------------------------------------------------------------- extended_scores rows
    curve_p = os.path.join(csv, "climatological_curve.csv")
    curve = pd.read_csv(curve_p) if os.path.exists(curve_p) else pd.DataFrame()
    cidx = {}
    if not curve.empty:
        for r in curve.itertuples():
            cidx[(r.zone_id, r.species_id, int(r.doy))] = r.climatological_score

    srows = []
    for r in ext.itertuples():
        z = ZMAP[r.zone_id]
        base = {
            "month": r.month, "doy": r.doy,
            "sst_norm": r.sst_norm_f, "sst_sd": r.sst_sd_f,
            "wave_norm": r.wave_norm_ft, "wind_norm": r.wind_norm_kt,
            "wave_ft": r.xx_wave_used, "wind_kt": r.xx_wind_used,
            "wave_period_s": r.xx_period_used,
            "pressure_trend_24h": r.xx_prs_used,
            "tide_range_ft": r.tide_range_ft, "max_exchange_rate_ft_h": r.max_exchange_rate_ft_h,
            "best_water_hour": r.best_water_hour, "first_high_hour": r.first_high_hour,
            "moon_illum": r.moon_illum, "moon_age_days": r.moon_age_days,
            "sunrise_hour": r.sunrise_hour, "sunset_hour": r.sunset_hour,
            "front_f_per_nm": np.nan, "sst_spread_f": np.nan, "chl_mg_m3": np.nan,
        }
        widen = 3.0 if r.confidence_tier == 2 else 7.0
        for sp in species:
            if r.zone_id not in sp["zones"]:
                continue
            mid = score_row(sp, {**base, "sst_f": r.xx_sst_used,
                                 "sst_anom_f": r.sst_anom_proj_f}, z)
            if mid is None:
                continue
            lo = score_row(sp, {**base, "sst_f": r.xx_sst_lo,
                                "sst_anom_f": (r.sst_anom_proj_f - (r.sst_proj_hi_f - r.sst_proj_f))
                                if pd.notna(r.sst_proj_hi_f) else r.sst_anom_proj_f}, z)
            hi = score_row(sp, {**base, "sst_f": r.xx_sst_hi,
                                "sst_anom_f": (r.sst_anom_proj_f + (r.sst_proj_hi_f - r.sst_proj_f))
                                if pd.notna(r.sst_proj_hi_f) else r.sst_anom_proj_f}, z)
            norm = score_row(sp, base, z, clim_mode=True)
            clim_score = cidx.get((r.zone_id, sp["id"], r.doy))
            if clim_score is None:
                clim_score = norm["score"] if norm else np.nan
            cand = [x["score"] for x in (lo, hi) if x]
            s_lo = min(cand + [mid["score"]]) - widen if cand else mid["score"] - widen
            s_hi = max(cand + [mid["score"]]) + widen if cand else mid["score"] + widen
            delta = round(mid["score"] - clim_score, 1) if pd.notna(clim_score) else np.nan
            srows.append({
                "date": r.date, "zone_id": r.zone_id, "zone": r.zone, "band": r.band,
                "species_id": sp["id"], "species": sp["name"], "group": sp["group"],
                "lead_days": r.lead_days, "confidence_tier": r.confidence_tier,
                "confidence_key": r.confidence_key, "confidence_label": r.confidence_label,
                "score_outlook": mid["score"],
                "score_lo": round(max(1.0, s_lo), 1), "score_hi": round(min(99.9, s_hi), 1),
                "score_band_pts": round(min(99.9, s_hi) - max(1.0, s_lo), 1),
                "seasonal_norm_score": norm["score"] if norm else np.nan,
                "climatological_score": clim_score,
                "vs_seasonal_norm": delta, "trend": trend_label(delta),
                "fishability_outlook": mid["fishability"] if r.sea_state_published else np.nan,
                "fishability_climatological": (norm["fishability"] if norm else np.nan),
                "access_note": mid["access_note"] if r.sea_state_published else "climatological sea state",
                "term_sst": mid["terms"]["sst"], "term_season": mid["terms"]["season"],
                "term_tide": mid["terms"]["tide"], "term_moon": mid["terms"]["moon"],
                "term_swell": mid["terms"]["swell"],
                "pressure_trend_model_24h": r.pressure_trend_model_24h,   # reference, not scored
                "anomaly_term": mid["anomaly_term"],
                "missing_drivers": ",".join(mid["missing"]),
                "sst_proj_f": r.sst_proj_f, "sst_anom_proj_f": r.sst_anom_proj_f,
                "tide_range_ft": r.tide_range_ft, "moon_illum": r.moon_illum, "moon_phase": r.moon_phase,
                "basis": ("model-blended conditions + exact tide/moon" if r.sea_state_published
                          else "climatology + ENSO analog + exact tide/moon"),
                "is_extended": True,
            })
    esc = pd.DataFrame(srows)

    # ------------------------------------------------------- monotone uncertainty envelope
    # The raw band above is a *sensitivity* band: it re-scores at the low and high end of the
    # projected SST range. The SST range itself widens monotonically with lead (2.3 -> 5.1 F),
    # but the score band derived from it can NARROW at longer lead, because the score response
    # saturates near its soft ceiling: a species already pinned at ~90 moves less for a given
    # temperature swing than one sitting at ~73 on the steep part of its curve.
    #
    # Publishing that raw band would tell the user the day-25 score is more certain than the
    # day-8 score, which is false. So enforce a one-way ratchet: the half-width may never
    # shrink as lead grows. This only ever widens the published band, so it cannot manufacture
    # precision -- it just refuses to claim precision that lead time does not support.
    if not esc.empty:
        esc = esc.sort_values(["zone_id", "species_id", "lead_days"]).reset_index(drop=True)
        hw_lo = (esc["score_outlook"] - esc["score_lo"]).abs()
        hw_hi = (esc["score_hi"] - esc["score_outlook"]).abs()
        esc["score_band_raw_pts"] = (hw_lo + hw_hi).round(1)          # pre-ratchet, kept for audit
        hw_lo = hw_lo.groupby([esc["zone_id"], esc["species_id"]]).cummax()
        hw_hi = hw_hi.groupby([esc["zone_id"], esc["species_id"]]).cummax()
        raw_lo, raw_hi = esc["score_lo"].copy(), esc["score_hi"].copy()
        esc["score_halfband_lo_pts"] = hw_lo.round(1)   # monotone by construction, pre-clip
        esc["score_halfband_hi_pts"] = hw_hi.round(1)
        esc["score_lo"] = (esc["score_outlook"] - hw_lo).clip(lower=1.0).round(1)
        esc["score_hi"] = (esc["score_outlook"] + hw_hi).clip(upper=99.9).round(1)
        esc["score_band_pts"] = (esc["score_hi"] - esc["score_lo"]).round(1)
        esc["score_band_is_ratcheted"] = (
            (esc["score_halfband_lo_pts"] > (esc["score_outlook"] - raw_lo).abs() + 0.05)
            | (esc["score_halfband_hi_pts"] > (raw_hi - esc["score_outlook"]).abs() + 0.05))
        # The 0-100 scale is bounded, so a band around a score already near the ceiling gets
        # clipped and the *drawn* width can still shrink even though the underlying half-width
        # did not. Flag those rows instead of pretending the band is unbounded.
        esc["score_band_hits_ceiling"] = (esc["score_outlook"] + hw_hi) > 99.9
        esc["score_band_hits_floor"] = (esc["score_outlook"] - hw_lo) < 1.0

    # ---------------------------------------------------------------- supporting tables
    tiers = pd.DataFrame(TIERS)
    analog_tbl = ana.copy()
    analog_tbl["current_oni"] = cur["oni"]
    analog_tbl["current_regime"] = cur["simple_regime"]
    analog_tbl["window_months"] = ",".join(str(m) for m in months)
    if not obs_ana.empty:
        analog_tbl = analog_tbl.merge(
            obs_ana.groupby("year").agg(zones_with_obs=("zone_id", "nunique"),
                                        observed_anom_f=("observed_anom_f", "mean")).reset_index(),
            on="year", how="left")
    cpc_tbl = pd.DataFrame(list(cpc.values()))

    prov = pd.DataFrame([
        # field, table, basis, source, notes
        ("sst_norm_f", "extended_outlook", "climatology", "MUR / CO-OPS day-of-year climatology",
         "Seasonal average for this day-of-year, +/-7 day window. Not a forecast."),
        ("sst_sd_f", "extended_outlook", "climatology", "same climatology", "Interannual SD, drives the uncertainty band."),
        ("wave_norm_ft", "extended_outlook", "climatology", "NDBC buoy day-of-year climatology", "Seasonal average swell height."),
        ("wind_norm_kt", "extended_outlook", "climatology", "NDBC buoy day-of-year climatology",
         "Seasonal average wind speed. Resolved through a fallback chain because most SoCal buoys in "
         "this dataset carry wave normals but no wind normals."),
        ("wave_norm_station", "extended_outlook", "metadata", "derived", "Which NDBC station supplied wave_norm_ft."),
        ("wind_norm_station", "extended_outlook", "metadata", "derived", "Which NDBC station supplied wind_norm_kt."),
        ("wind_norm_is_regional_fallback", "extended_outlook", "metadata", "derived",
         "True when wind climatology came from regional fallback buoy 46086 rather than a zone buoy."),
        ("cpc_temp_cat", "extended_outlook", "forecast", "CPC 8-14 day / week 3-4 / monthly outlook",
         "Probabilistic tercile forecast issued for a specific window. Land-based CONUS product."),
        ("cpc_temp_prob", "extended_outlook", "forecast", "CPC outlook", "Probability of the leading tercile."),
        ("cpc_prcp_cat", "extended_outlook", "forecast", "CPC outlook", "Precipitation tercile; carried for context, zero weight in scoring."),
        ("wave_model_ft", "extended_outlook", "forecast", "Open-Meteo Marine 16-day", "Deterministic model. Exists only to about lead day 15."),
        ("wind_model_kt", "extended_outlook", "forecast", "Open-Meteo 16-day", "Deterministic model. Exists only to about lead day 15."),
        ("pressure_trend_model_24h", "extended_outlook", "forecast", "Open-Meteo 16-day",
         "Reference only. Deliberately NOT fed to the extended score: it exists only in tier 2, and "
         "dropping it at tier 3 renormalized the driver weights and created a false ~19 pt cliff at "
         "the day-14/15 boundary. Excluded at both tiers so extended scores stay comparable."),
        ("wave_proj_ft", "extended_outlook", "blend", "model + climatology",
         "NULL for days 15-30 on purpose, and also NULL inside tier 2 past the marine model horizon "
         "(the Open-Meteo swell fields stop several days before its wind fields)."),
        ("wind_proj_kt", "extended_outlook", "blend", "model + climatology", "NULL for days 15-30 on purpose."),
        ("wave_basis", "extended_outlook", "metadata", "derived", "Per-row text: is this swell value model-blended or climatology only."),
        ("wind_basis", "extended_outlook", "metadata", "derived", "Per-row text: is this wind value model-blended or climatology only."),
        ("sst_proj_f", "extended_outlook", "blend", "persistence + ENSO analog + CPC", "Weights reported per row."),
        ("sst_anom_proj_f", "extended_outlook", "blend", "persistence + ENSO analog + CPC", "Weights reported per row."),
        ("sst_anom_core_f", "extended_outlook", "blend", "persistence + ENSO analog",
         "The anomaly before the CPC nudge. w_persistence + w_enso_analog = 1 by construction."),
        ("cpc_nudge_f", "extended_outlook", "forecast", "CPC tercile signal x product weight",
         "Additive degF adjustment applied on top of sst_anom_core_f. Exactly 0 when CPC says EC/Normal."),
        ("tide_window_widened", "extended_outlook", "metadata", "derived",
         "True on genuine diurnal days where the hi/lo window was widened to bracketing extrema."),
        ("sst_proj_lo_f", "extended_outlook", "blend", "climatological SD x lead multiplier", "Widens with lead time."),
        ("sst_proj_hi_f", "extended_outlook", "blend", "climatological SD x lead multiplier", "Widens with lead time."),
        ("enso_expected_anom_f", "extended_outlook", "enso_analog", "enso_zone_composite / enso_sensitivity",
         "Historical mean anomaly for this zone, month and ENSO regime."),
        ("analog_years", "extended_outlook", "enso_analog", "CPC ONI 1950-present", "Years with a similar ONI in this calendar window."),
        ("tide_range_ft", "extended_outlook", "astronomical", "CO-OPS harmonic predictions",
         "Exact at any lead time. Not a weather forecast."),
        ("max_exchange_rate_ft_h", "extended_outlook", "astronomical", "CO-OPS harmonic predictions", "Exact at any lead time."),
        ("best_water_hour", "extended_outlook", "astronomical", "CO-OPS harmonic predictions", "Exact at any lead time."),
        ("first_high_hour", "extended_outlook", "astronomical", "CO-OPS harmonic predictions", "Exact at any lead time."),
        ("moon_illum", "extended_outlook", "astronomical", "Meeus lunar phase", "Exact at any lead time."),
        ("moon_phase", "extended_outlook", "astronomical", "Meeus lunar phase", "Exact at any lead time."),
        ("sunrise_hour", "extended_outlook", "astronomical", "NOAA solar equations", "Exact at any lead time."),
        ("sunset_hour", "extended_outlook", "astronomical", "NOAA solar equations", "Exact at any lead time."),
        ("score_outlook", "extended_scores", "blend", "same species model as the 7-day forecast",
         "Tier 2 uses blended model conditions; tier 3 uses climatology + ENSO analog."),
        ("score_lo", "extended_scores", "blend", "SST band re-scored, plus tier widening, then ratcheted",
         "Low edge of the plausible score range. Tier widening +/-3 pts tier 2, +/-7 pts tier 3."),
        ("score_hi", "extended_scores", "blend", "SST band re-scored, plus tier widening, then ratcheted",
         "High edge of the plausible score range."),
        ("score_band_pts", "extended_scores", "blend", "score_hi - score_lo after the monotone ratchet",
         "Published band width. Never narrows as lead time grows."),
        ("score_band_raw_pts", "extended_scores", "blend", "score_hi - score_lo before the ratchet",
         "Pure SST-sensitivity band. Can narrow at long lead where the score saturates near its ceiling; kept for audit only."),
        ("score_band_is_ratcheted", "extended_scores", "metadata", "build_extended.py",
         "True where the published band was widened to stop it implying more certainty at longer lead."),
        ("score_halfband_lo_pts", "extended_scores", "blend", "monotone (cummax) low half-width",
         "Non-decreasing in lead by construction, before the 0-100 clip."),
        ("score_halfband_hi_pts", "extended_scores", "blend", "monotone (cummax) high half-width",
         "Non-decreasing in lead by construction, before the 0-100 clip."),
        ("score_band_hits_ceiling", "extended_scores", "metadata", "build_extended.py",
         "True where the upper band edge was clipped at the top of the 0-100 scale, so the drawn band is narrower than the modelled uncertainty."),
        ("score_band_hits_floor", "extended_scores", "metadata", "build_extended.py",
         "True where the lower band edge was clipped at the bottom of the scale."),
        ("climatological_score", "extended_scores", "climatology", "climatological_curve.csv", "The day-of-year seasonal norm."),
        ("seasonal_norm_score", "extended_scores", "climatology", "score_row(clim_mode=True)", "Unchanged near-term methodology."),
        ("trend", "extended_scores", "blend", "score_outlook vs climatological_score", "Qualitative label, the primary tier-3 output."),
        ("fishability_outlook", "extended_scores", "blend", "model-blended wind/swell", "NULL for days 15-30 on purpose."),
        # --- weights, flags and driver terms. These are not projected physical values, but a reader
        # can still mistake them for measurements, so every one of them gets an explicit basis too.
        ("w_persistence", "extended_outlook", "metadata", "exp(-(lead-6)/8)",
         "Share of the SST projection carried by the currently observed anomaly. Decays with lead."),
        ("w_enso_analog", "extended_outlook", "metadata", "1 - w_persistence",
         "Share carried by the ENSO analog composite. Rises with lead, so climate dominates far out."),
        ("w_cpc_outlook", "extended_outlook", "metadata", "lead-dependent constant",
         "Weight of the CPC nudge. Additive, not a third share, so the two shares always sum to 1."),
        ("model_blend_weight", "extended_outlook", "metadata", "exp(-(lead-6)/7)",
         "Weight of the deterministic model in the wind/swell blend. Zero beyond the model horizon."),
        ("sst_basis", "extended_outlook", "metadata", "build_extended.py",
         "Plain-language description of how this row's SST projection was formed."),
        ("sea_state_basis", "extended_outlook", "metadata", "build_extended.py",
         "Why a daily sea state is or is not published for this row."),
        ("sea_state_published", "extended_outlook", "metadata", "build_extended.py",
         "False for the whole days 15-30 tier: no daily wind or swell figure is published there."),
        ("enso_analog_basis", "extended_outlook", "metadata", "build_extended.py",
         "Whether the analog expectation came from the zone ENSO composite or the observed 2023 anomaly."),
        ("sst_climatology_source", "extended_outlook", "metadata", "build_extended.py",
         "Which baseline supplied sst_norm_f for this zone: shore thermistor inshore, MUR satellite offshore."),
        ("enso_regime", "extended_outlook", "enso_analog", "CPC ONI",
         "Current ENSO regime label. Observed, not forecast."),
        ("oni", "extended_outlook", "enso_analog", "CPC ONI table",
         "Latest observed 3-month Oceanic Nino Index. Observed, not forecast."),
        ("cpc_temp_signal_f", "extended_outlook", "forecast", "CPC tercile probability",
         "Signed degF signal before the lead-dependent weight. Exactly 0 for Equal Chances."),
        ("cpc_product", "extended_outlook", "metadata", "build_extended.py",
         "Which CPC product supplied this row: 814temp, wk34temp, monthly or seasonal."),
        ("cpc_valid_from", "extended_outlook", "metadata", "CPC shapefile attributes", "Start of the CPC product's valid period."),
        ("cpc_valid_to", "extended_outlook", "metadata", "CPC shapefile attributes", "End of the CPC product's valid period."),
        ("cpc_valid_label", "extended_outlook", "metadata", "CPC shapefile attributes", "Human-readable valid period."),
        ("cpc_prcp_prob", "extended_outlook", "forecast", "CPC precipitation tercile",
         "Stored for context only. Rainfall has no term in the species model and is not scored."),
        ("daylight_hours", "extended_outlook", "astronomical", "NOAA solar equations", "Exact at any lead time."),
        ("moon_age_days", "extended_outlook", "astronomical", "Meeus lunar phase", "Exact at any lead time."),
        ("wave_model_period_s", "extended_outlook", "forecast", "Open-Meteo marine model",
         "Only inside the marine model horizon; NULL beyond it. No climatological period fallback."),
        ("sst_years", "extended_outlook", "climatology", "day-of-year climatology sample",
         "Distinct years contributing to sst_norm_f. Inshore baseline is 11 years, offshore up to 23."),
        ("term_sst", "extended_scores", "blend", "species response to sst_proj_f", "Driver response, 0-1.35."),
        ("term_season", "extended_scores", "climatology", "species seasonal index", "Day-of-year index. Not a forecast."),
        ("term_tide", "extended_scores", "astronomical", "tide range and exchange rate", "Exact at any lead time."),
        ("term_moon", "extended_scores", "astronomical", "lunar illumination", "Exact at any lead time."),
        ("term_swell", "extended_scores", "blend", "species response to wave_proj_ft",
         "NULL wherever no wave value exists, in which case the driver is dropped, not imputed."),
        ("anomaly_term", "extended_scores", "enso_analog", "ENSO sensitivity by species",
         "Multiplier applied to the weighted driver mean for the current regime."),
        ("vs_seasonal_norm", "extended_scores", "blend", "score_outlook - climatological_score",
         "The honest headline for the outlook tier: better or worse than typical, in points."),
        ("fishability_climatological", "extended_scores", "climatology", "climatological wind/swell",
         "What a typical day in this calendar window allows, available at every lead."),
        ("missing_drivers", "extended_scores", "metadata", "build_extended.py",
         "Drivers dropped for this row. Remaining weights renormalize; nothing is imputed."),
        ("is_extended", "extended_scores", "metadata", "build_extended.py",
         "Always true here. Distinguishes these rows from the unchanged near-term scores_daily table."),
    ], columns=["field", "table", "basis", "source", "notes"])

    # ---------------------------------------------------------------- write
    def w(name, df):
        p = os.path.join(csv, name + ".csv")
        tmp = p + ".tmp"
        df.to_csv(tmp, index=False)
        os.replace(tmp, p)
        print(f"  {name+'.csv':34s} {len(df):6d} rows")

    print("writing:")
    w("extended_outlook", ext[[c for c in ext.columns if not c.startswith("xx_")]])
    w("extended_scores", esc)
    w("forecast_confidence", tiers)
    w("enso_analog_years", analog_tbl)
    w("cpc_outlook_current", cpc_tbl)
    w("extended_field_provenance", prov)
    if not obs_ana.empty:
        w("enso_analog_zone_anomaly", obs_ana)

    meta = {
        "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "today": today.strftime("%Y-%m-%d"),
        "near_term_last_date": near_last.strftime("%Y-%m-%d"),
        "extended_from": dates[0].strftime("%Y-%m-%d"),
        "extended_to": dates[-1].strftime("%Y-%m-%d"),
        "horizon_days": HORIZON,
        "extended_rows": int(len(ext)), "extended_score_rows": int(len(esc)),
        "enso_regime": cur["simple_regime"], "oni": cur["oni"],
        "analog_years": [int(y) for y in ana.year.tolist()],
        "cpc_products": {k: {kk: vv for kk, vv in v.items() if kk != "record"} for k, v in cpc.items()},
        "model_horizon_last_date": (model.date.max().strftime("%Y-%m-%d") if not model.empty else None),
        "tiers": TIERS,
    }
    json.dump(meta, open(os.path.join(csv, "extended_meta.json"), "w"), indent=1, default=str)
    print(f"  extended_meta.json")
    return meta


if __name__ == "__main__":
    main()
