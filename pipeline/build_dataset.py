"""SoCal Fishing Intelligence — transform, feature, score, validate, export."""
import json, os, glob, math, sys
from datetime import datetime, date, timedelta, timezone
import numpy as np
import pandas as pd

ROOT = os.environ.get("SOCAL_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import lib_parse as L
from lib_parse import ZONES, SPECIES, RAW

OUT = os.environ.get("SOCAL_OUT", os.path.join(ROOT, "dataset", "csv"))
BASELINE = os.environ.get("SOCAL_BASELINE_CSV", os.path.join(ROOT, "dataset", "csv"))
os.makedirs(OUT, exist_ok=True)
ZMAP = {z["id"]: z for z in ZONES}
SMAP = {s["id"]: s for s in SPECIES}
RUN_TS = datetime.now(timezone.utc)

# ============================================================ 1. zone timeline (Open-Meteo)
def om_zone_daily():
    rows = []
    for z in ZONES:
        pm, pw = f"{RAW}/om_marine_{z['id']}.json", f"{RAW}/om_wx_{z['id']}.json"
        if not (os.path.exists(pm) and os.path.exists(pw)):
            continue
        m, w = json.load(open(pm))["daily"], json.load(open(pw))["daily"]
        md = pd.DataFrame(m); wd = pd.DataFrame(w)
        d = md.merge(wd, on="time", how="outer")
        d["zone_id"] = z["id"]
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["date"] = pd.to_datetime(d.time)
    out = pd.DataFrame({
        "zone_id": d.zone_id, "date": d.date,
        "wave_ft": d.wave_height_max * 3.28084,
        "swell_ft": d.swell_wave_height_max * 3.28084,
        "wave_period_s": d.wave_period_max,
        "swell_period_s": d.swell_wave_period_max,
        "wave_dir_deg": d.wave_direction_dominant,
        "sst_f_model": (d.sea_surface_temperature_max + d.sea_surface_temperature_min) / 2 * 9 / 5 + 32,
        "wind_kt": d.wind_speed_10m_max, "gust_kt": d.wind_gusts_10m_max,
        "wind_dir_deg": d.wind_direction_10m_dominant,
        "pressure_hpa": d.pressure_msl_mean, "cloud_pct": d.cloud_cover_mean,
        "air_temp_f": d.temperature_2m_max * 9 / 5 + 32, "precip_mm": d.precipitation_sum})
    out = out.sort_values(["zone_id", "date"])
    out["pressure_trend_24h"] = out.groupby("zone_id").pressure_hpa.diff()
    return out

# ============================================================ 2. MUR SST + climatology
def mur_zone_history():
    frames = []
    prior = os.path.join(BASELINE, "mur_history.csv")
    if os.path.exists(prior):
        d = pd.read_csv(prior)
        d["date"] = pd.to_datetime(d.date)
        frames.append(d[["zone_id", "date", "sst_f"]])
    for p in sorted(glob.glob(f"{RAW}/mur_*_*.csv")):
        if "grid" in p:
            continue
        base = os.path.basename(p)[4:-4]
        zid = "_".join(base.split("_")[:-2])
        if zid not in ZMAP:
            continue
        try:
            d = pd.read_csv(p, skiprows=[1])
        except Exception:
            continue
        if "analysed_sst" not in d.columns:
            continue
        d = d.rename(columns={"analysed_sst": "sst_c"})
        d["date"] = pd.to_datetime(d.time).dt.tz_localize(None).dt.normalize()
        d["zone_id"] = zid
        d["sst_f"] = d.sst_c * 9 / 5 + 32
        frames.append(d[["zone_id", "date", "sst_f"]])
    if not frames:
        return pd.DataFrame(columns=["zone_id", "date", "sst_f"])
    return (pd.concat(frames, ignore_index=True)
              .dropna(subset=["sst_f"]).drop_duplicates(["zone_id", "date"])
              .sort_values(["zone_id", "date"]))

def doy_climatology(df, value_col, group_col="zone_id", window=7, label="sst"):
    """Centered +/-window day-of-year climatology across all available years."""
    d = df.copy()
    d["doy"] = d.date.dt.dayofyear
    rows = []
    for g, gg in d.groupby(group_col):
        by = {k: v[value_col].values for k, v in gg.groupby("doy")}
        yrs = gg.date.dt.year.nunique()
        for doy in range(1, 367):
            vals = np.concatenate([by.get(((doy + k - 1) % 366) + 1, np.array([])) for k in range(-window, window + 1)]) if by else np.array([])
            vals = vals[~np.isnan(vals)]
            if len(vals) < 5:
                continue
            rows.append({group_col: g, "doy": doy, f"{label}_norm": vals.mean(),
                         f"{label}_sd": vals.std(ddof=1), f"{label}_p10": np.percentile(vals, 10),
                         f"{label}_p50": np.percentile(vals, 50), f"{label}_p90": np.percentile(vals, 90),
                         f"{label}_n": len(vals), f"{label}_years": yrs})
    return pd.DataFrame(rows)

# ============================================================ 3. buoy climatology (waves/wind/SST)
def buoy_climatology():
    hourly, daily = L.ndbc_all()
    prior = os.path.join(BASELINE, "buoy_daily.csv")
    if os.path.exists(prior):
        old = pd.read_csv(prior)
        old["date"] = pd.to_datetime(old.date)
        daily = pd.concat([old, daily], ignore_index=True) if not daily.empty else old
        daily = daily.drop_duplicates(["station", "date"], keep="last")
    if daily.empty:
        return daily, pd.DataFrame(), hourly
    daily = daily.dropna(subset=["date"])
    wave_clim = doy_climatology(daily.dropna(subset=["wave_ft"]), "wave_ft", "station", 7, "wave")
    wind_clim = doy_climatology(daily.dropna(subset=["wind_kt"]), "wind_kt", "station", 7, "wind")
    sst_clim = doy_climatology(daily.dropna(subset=["buoy_sst_f"]), "buoy_sst_f", "station", 7, "buoysst")
    clim = wave_clim.merge(wind_clim, on=["station", "doy"], how="outer").merge(sst_clim, on=["station", "doy"], how="outer")
    return daily, clim, hourly

# ============================================================ 4. shore SST (CO-OPS)
def shore_sst():
    d = L.coops_json("coops_*_water_temperature_*.json", "data")
    daily = pd.DataFrame()
    if not d.empty:
        d["date"] = pd.to_datetime(d.ts.dt.date)
        daily = d.groupby(["station", "date"]).v.agg(["mean", "min", "max", "count"]).reset_index()
        daily = daily.rename(columns={"station": "tide_station", "mean": "shore_sst_f",
                                      "min": "shore_sst_min_f", "max": "shore_sst_max_f", "count": "n_obs"})
        daily = daily[daily.n_obs >= 12]
    prior = os.path.join(BASELINE, "shore_sst_daily.csv")
    if os.path.exists(prior):
        old = pd.read_csv(prior, dtype={"tide_station": str})
        old["date"] = pd.to_datetime(old.date)
        daily["tide_station"] = daily.get("tide_station", pd.Series(dtype=str)).astype(str)
        daily = pd.concat([old, daily], ignore_index=True).drop_duplicates(
            ["tide_station", "date"], keep="last")
    if daily.empty:
        return pd.DataFrame(), pd.DataFrame()
    clim = doy_climatology(daily, "shore_sst_f", "tide_station", 7, "shoresst")
    return daily, clim

# ============================================================ 5. assemble
def assemble():
    art = {}
    zone_daily = om_zone_daily()
    mur = mur_zone_history()
    art["mur_history"] = mur
    mur_clim = doy_climatology(mur, "sst_f", "zone_id", 7, "sst") if not mur.empty else pd.DataFrame()
    art["sst_climatology"] = mur_clim
    buoy_daily, buoy_clim, buoy_hourly = buoy_climatology()
    art["buoy_daily"] = buoy_daily
    art["buoy_climatology"] = buoy_clim
    sh_daily, sh_clim = shore_sst()
    art["shore_sst_daily"] = sh_daily
    art["shore_sst_climatology"] = sh_clim
    tide = L.tide_features()
    art["tide_daily"] = tide
    o = L.oni(); art["enso_oni"] = o
    fronts = L.mur_fronts(); art["fronts"] = fronts
    cuti = L.cuti_daily(); art["upwelling_cuti"] = cuti
    chl = L.chl_zone(); art["chlorophyll"] = chl

    df = zone_daily.copy()
    df["doy"] = df.date.dt.dayofyear
    df["month"] = df.date.dt.month

    # observed satellite SST where available, else model SST
    if not mur.empty:
        df = df.merge(mur.rename(columns={"sst_f": "sst_f_sat"}), on=["zone_id", "date"], how="left")
    else:
        df["sst_f_sat"] = np.nan
    df["sst_f"] = df.sst_f_sat.combine_first(df.sst_f_model)
    df["sst_source"] = np.where(df.sst_f_sat.notna(), "MUR 1km satellite", "Open-Meteo marine model")

    if not mur_clim.empty:
        df = df.merge(mur_clim, on=["zone_id", "doy"], how="left")
    else:
        for c in ["sst_norm", "sst_sd", "sst_p10", "sst_p50", "sst_p90", "sst_n", "sst_years"]:
            df[c] = np.nan
    # tides
    df["tide_station"] = df.zone_id.map(lambda z: ZMAP[z]["tide_station"])
    if not tide.empty:
        df = df.merge(tide, on=["tide_station", "date"], how="left")
    # shore sst observed + norm (inshore reality check)
    if not sh_daily.empty:
        df = df.merge(sh_daily[["tide_station", "date", "shore_sst_f"]], on=["tide_station", "date"], how="left")
        if not sh_clim.empty:
            df = df.merge(sh_clim[["tide_station", "doy", "shoresst_norm", "shoresst_sd", "shoresst_p10",
                                   "shoresst_p50", "shoresst_p90", "shoresst_years"]],
                          on=["tide_station", "doy"], how="left")
            df["shore_sst_anom_f"] = df.shore_sst_f - df.shoresst_norm

    # ---- authoritative SST per zone: inshore uses shore-station thermistor, offshore uses satellite
    INSHORE = {"sd_bay", "mission_bay", "surf_zone"}
    ins = df.zone_id.isin(INSHORE) & df.get("shore_sst_f", pd.Series(np.nan, index=df.index)).notna()
    if "shore_sst_f" in df.columns:
        df.loc[ins, "sst_f"] = df.loc[ins, "shore_sst_f"]
        df.loc[ins, "sst_source"] = "NOAA CO-OPS shore thermistor"
        for a, b in [("sst_norm", "shoresst_norm"), ("sst_sd", "shoresst_sd"), ("sst_p10", "shoresst_p10"),
                     ("sst_p50", "shoresst_p50"), ("sst_p90", "shoresst_p90"), ("sst_years", "shoresst_years")]:
            if b in df.columns:
                df.loc[ins, a] = df.loc[ins, b]
    df["sst_anom_f"] = df.sst_f - df.sst_norm
    df["sst_pctile"] = df.apply(
        lambda r: np.nan if pd.isna(r.get("sst_sd")) or not r.get("sst_sd") or pd.isna(r.get("sst_anom_f"))
        else float(min(99.9, max(0.1, 100 * 0.5 * (1 + math.erf(r.sst_anom_f / (r.sst_sd * math.sqrt(2))))))), axis=1)
    df["sst_trend_7d"] = df.groupby("zone_id").sst_f.diff(7)

    # buoy comparison for wave/wind percentile vs long climatology
    df["primary_buoy"] = df.zone_id.map(lambda z: ZMAP[z]["buoys"][0])
    if not buoy_clim.empty:
        bc = buoy_clim.rename(columns={"station": "primary_buoy"})
        df = df.merge(bc[["primary_buoy", "doy", "wave_norm", "wave_p90", "wind_norm", "wind_p90",
                          "buoysst_norm", "buoysst_years", "wave_years"]],
                      on=["primary_buoy", "doy"], how="left")
        df["wave_anom_ft"] = df.wave_ft - df.wave_norm
        df["wind_anom_kt"] = df.wind_kt - df.wind_norm

    # fronts / chl / upwelling
    if not fronts.empty:
        df = df.merge(fronts, on=["zone_id", "date"], how="left")
    else:
        df["front_f_per_nm"] = np.nan; df["sst_spread_f"] = np.nan
    if not chl.empty:
        df = df.merge(chl, on=["zone_id", "date"], how="left")
    else:
        df["chl_mg_m3"] = np.nan
    if not cuti.empty:
        df = df.merge(cuti, on="date", how="left")
    else:
        df["cuti"] = np.nan

    # cross-shore SST gradient proxy from buoys (offshore 46086 vs nearshore 46232 vs shore)
    if not buoy_daily.empty:
        piv = buoy_daily.pivot_table(index="date", columns="station", values="buoy_sst_f")
        if {"46086", "46232"} <= set(piv.columns):
            grad = (piv["46086"] - piv["46232"]).rename("offshore_minus_nearshore_f").reset_index()
            df = df.merge(grad, on="date", how="left")
        else:
            df["offshore_minus_nearshore_f"] = np.nan

    # moon + sun
    uniq = sorted(df.date.unique())
    moon = []
    for d in uniq:
        dd = pd.Timestamp(d).date()
        illum, ang, name, age = L.moon_phase(dd)
        sr, ss, dl = L.sun_times(dd)
        moon.append({"date": pd.Timestamp(d), "moon_illum": illum, "moon_phase": name,
                     "moon_age_days": age, "sunrise_hour": sr, "sunset_hour": ss, "daylight_hours": dl})
    df = df.merge(pd.DataFrame(moon), on="date", how="left")

    # ENSO
    latest = o.dropna(subset=["oni"]).iloc[-1]
    df["oni_latest"] = latest.oni
    df["enso_regime"] = latest.regime
    df["enso_simple"] = latest.simple_regime
    df["enso_season"] = f"{latest.season} {int(latest.year)}"

    art["enso_current"] = pd.DataFrame([{
        "season": latest.season, "year": int(latest.year), "oni": latest.oni,
        "regime": latest.regime, "simple_regime": latest.simple_regime,
        "trailing_12mo_mean_oni": float(o.oni.tail(12).mean())}])

    # ENSO composite of zone SST anomaly by regime x month (data-derived regime adjustment)
    if not mur.empty and not mur_clim.empty:
        m = mur.copy(); m["doy"] = m.date.dt.dayofyear; m["month"] = m.date.dt.month; m["year"] = m.date.dt.year
        m = m.merge(mur_clim[["zone_id", "doy", "sst_norm"]], on=["zone_id", "doy"], how="left")
        m["anom"] = m.sst_f - m.sst_norm
        m = m.merge(o[["year", "month", "oni", "simple_regime"]], on=["year", "month"], how="left")
        comp = (m.dropna(subset=["anom", "simple_regime"]).groupby(["zone_id", "month", "simple_regime"])
                  .agg(mean_anom_f=("anom", "mean"), sd_anom_f=("anom", "std"), n_days=("anom", "size"))
                  .reset_index())
        art["enso_zone_composite"] = comp
        sens = []
        for (zid, mo), g in m.dropna(subset=["anom", "oni"]).groupby(["zone_id", "month"]):
            if len(g) > 60 and g.oni.std() > 0.15:
                slope, icept = np.polyfit(g.oni, g.anom, 1)
                r = np.corrcoef(g.oni, g.anom)[0, 1]
                sens.append({"zone_id": zid, "month": mo, "anom_f_per_oni": slope,
                             "r": r, "n_days": len(g)})
        art["enso_sensitivity"] = pd.DataFrame(sens)
    else:
        art["enso_zone_composite"] = pd.DataFrame(); art["enso_sensitivity"] = pd.DataFrame()

    df["is_forecast"] = df.date.dt.date > (RUN_TS.astimezone(timezone(timedelta(hours=-7))).date())
    art["conditions_daily"] = df
    return art

# ============================================================ 6. scoring engine
def trapezoid(x, a, b, c, d):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if x <= a or x >= d:
        return 0.02
    if x < b:
        return 0.02 + 0.98 * (x - a) / (b - a)
    if x <= c:
        return 1.0
    return 0.02 + 0.98 * (d - x) / (d - c)

def clamp(v, lo=0.02, hi=1.0):
    return max(lo, min(hi, v))

def tide_term(sp, row):
    pref = sp["tide_pref"]
    rng = row.get("tide_range_ft"); rate = row.get("max_exchange_rate_ft_h")
    if pd.isna(rng) or pd.isna(rate):
        return None
    r_term = clamp(0.25 + 0.75 * min(1.0, (rng - 2.0) / 4.5))        # 2ft -> weak, 6.5ft+ -> strong
    x_term = clamp(0.25 + 0.75 * min(1.0, (rate - 0.5) / 1.3))       # ft/hr exchange strength
    base = 0.45 * r_term + 0.55 * x_term
    if pref == "any":
        return clamp(0.6 + 0.4 * base)
    # timing bonus: moving water overlapping the low-light windows
    bw = row.get("best_water_hour"); sr = row.get("sunrise_hour"); ss = row.get("sunset_hour")
    timing = 0.5
    if not (pd.isna(bw) or pd.isna(sr) or pd.isna(ss)):
        d1 = min(abs(bw - sr), abs(bw - ss), abs(bw + 24 - sr), abs(bw - 24 - ss))
        timing = clamp(math.exp(-(d1 ** 2) / (2 * 2.5 ** 2)))
    if pref == "strong_moving":
        return clamp(0.55 * base + 0.45 * (0.4 + 0.6 * timing))
    if pref == "high_pushing":
        hi = row.get("first_high_hour")
        push = 0.5 if pd.isna(hi) else clamp(math.exp(-((min(abs(hi - 7), abs(hi - 19))) ** 2) / (2 * 3.5 ** 2)))
        return clamp(0.45 * base + 0.55 * (0.35 + 0.65 * push))
    if pref == "grey_light_moving":
        return clamp(0.4 * base + 0.6 * (0.3 + 0.7 * timing))
    if pref == "moderate":
        return clamp(1.0 - abs(base - 0.6) * 0.9)
    return clamp(0.5 * base + 0.5 * (0.45 + 0.55 * timing))

def moon_term(sp, row):
    illum = row.get("moon_illum"); age = row.get("moon_age_days")
    if pd.isna(illum):
        return None
    pref = sp["moon_pref"]
    if pref == "any":
        return 0.75 + 0.25 * (1 - abs(illum - 0.5) * 2) * 0.5 + 0.1
    if pref == "spring_tide":
        return clamp(0.45 + 0.55 * abs(illum - 0.5) * 2)     # new or full = big tides
    if pref == "post_new_moon":
        if pd.isna(age):
            return 0.6
        d = min(abs(age - 2.5), abs(age - 32.03))
        return clamp(0.35 + 0.65 * math.exp(-(d ** 2) / (2 * 3.0 ** 2)))
    if pref == "dark_or_full_night":
        return clamp(0.5 + 0.5 * abs(illum - 0.5) * 2)
    return 0.7

def swell_term(sp, row):
    w = row.get("wave_ft"); tol = sp["swell_tolerance_ft"]
    if pd.isna(w):
        return None
    if w <= tol * 0.35:
        return 1.0
    if w >= tol * 1.9:
        return 0.05
    return clamp(1.0 - (w - tol * 0.35) / (tol * 1.55) * 0.95)

def pressure_term(row):
    t = row.get("pressure_trend_24h")
    if pd.isna(t):
        return None
    # slow fall ahead of a front is the classic feed trigger; sharp rise shuts it down
    if t <= -4: return 0.55
    if t <= -1: return 1.0
    if t <= 1: return 0.85
    if t <= 4: return 0.6
    return 0.4

def front_term(row):
    f = row.get("front_f_per_nm"); spread = row.get("sst_spread_f")
    v = f if not pd.isna(f) else (spread / 12 if not pd.isna(spread) else np.nan)
    if pd.isna(v):
        return None
    return clamp(0.25 + 0.75 * min(1.0, v / 0.6))

def chl_term(sp, row):
    c = row.get("chl_mg_m3")
    if pd.isna(c):
        return None
    if sp["group"] in ("tuna", "pelagic"):
        return clamp(trapezoid(c, 0.02, 0.15, 1.2, 6.0))
    return clamp(trapezoid(c, 0.05, 0.3, 3.0, 15.0))

def fishability(row, zone):
    """Access gate: can you realistically fish this zone today (small-boat perspective)."""
    w = row.get("wind_kt"); wv = row.get("wave_ft"); per = row.get("wave_period_s")
    if pd.isna(w) or pd.isna(wv):
        return 0.7, "insufficient wind/wave data"
    dist = zone["distance_nm"]
    wind_gate = clamp(trapezoid(w, -5, 0, 12, 26 if dist < 5 else 22))
    wave_gate = clamp(trapezoid(wv, -1, 0, 3.5 if dist > 15 else 4.5, 9 if dist > 15 else 11))
    chop = 1.0
    if not pd.isna(per) and per < 8 and wv > 3:
        chop = 0.75
    run = 1.0 if dist < 12 else (0.9 if dist < 40 else 0.8)
    v = wind_gate * 0.5 + wave_gate * 0.5
    v = v * chop
    note = []
    if w > 18: note.append(f"{w:.0f} kt wind")
    if wv > 5: note.append(f"{wv:.1f} ft seas")
    if dist > 40: note.append("long-range run")
    return clamp(v * run, 0.05, 1.0), ", ".join(note) or "workable"

def anomaly_term(sp, row):
    a = row.get("sst_anom_f")
    if pd.isna(a):
        return None
    pref = sp["anomaly_pref"]
    return clamp(1.0 + pref * (a / 2.5) * 0.45, 0.35, 1.35)

def ref_temp(sp, zone, sst):
    """Groundfish respond to near-bottom temperature, not surface. Approximate the summer
    thermal profile with a depth-decay offset applied to SST (documented approximation)."""
    if sst is None or pd.isna(sst) or sp.get("temp_ref") != "bottom":
        return sst
    d = zone.get("depth_mid_ft", 100)
    offset = 14.0 * (1 - math.exp(-d / 160.0))
    return sst - offset

def soft_ceiling(v, knee=0.90, span=0.10):
    """Compress values above the knee asymptotically toward 1.0.

    Without this, a warm-anomaly boost pushes several species past 1.0 and they all clip to a
    tied 100, destroying discrimination at the top of the range. The mapping is strictly
    monotonic, so ranking is preserved while the ceiling is never quite reached."""
    if v <= knee:
        return v
    return knee + span * (1.0 - math.exp(-(v - knee) / span))

def score_row(sp, row, zone, clim_mode=False):
    """Weighted geometric mean of driver terms, gated by access. Returns score + contributions."""
    sst = row.get("sst_norm") if clim_mode else row.get("sst_f")
    sst = ref_temp(sp, zone, sst)
    terms = {
        "sst": trapezoid(sst, *sp["sst_f"]),
        "season": sp["season_index"][int(row["month"]) - 1],
        "tide": 0.72 if clim_mode else tide_term(sp, row),
        "moon": 0.72 if clim_mode else moon_term(sp, row),
        "pressure": 0.85 if clim_mode else pressure_term(row),
        "swell": swell_term(sp, {**row, "wave_ft": row.get("wave_norm") if clim_mode else row.get("wave_ft")}),
        "front": 0.6 if clim_mode else front_term(row),
        "chl": 0.7 if clim_mode else chl_term(sp, row),
    }
    if not clim_mode:
        at = anomaly_term(sp, row)
    else:
        at = 1.0
    w = dict(sp["weights"])
    used = {k: v for k, v in terms.items() if v is not None and w.get(k, 0) > 0}
    if not used:
        return None
    wsum = sum(w[k] for k in used)
    log = sum(w[k] * math.log(max(v, 0.02)) for k, v in used.items()) / wsum
    core = math.exp(log)
    core = clamp(core * (at if at else 1.0), 0.01, 1.35)
    fish, note = fishability(
        {**row, "wind_kt": row.get("wind_norm") if clim_mode else row.get("wind_kt"),
         "wave_ft": row.get("wave_norm") if clim_mode else row.get("wave_ft")}, zone)
    final = soft_ceiling(clamp(core * (0.35 + 0.65 * fish), 0.01, 1.35))
    contrib = {k: round(w[k] / wsum * (math.log(max(v, 0.02)) - math.log(0.5)), 4) for k, v in used.items()}
    return {"score": round(final * 100, 1), "core": round(soft_ceiling(core) * 100, 1),
            "fishability": round(fish * 100, 1), "access_note": note,
            "terms": {k: (round(v, 3) if v is not None else None) for k, v in terms.items()},
            "anomaly_term": round(at, 3) if at else None, "contrib": contrib,
            "missing": [k for k, v in terms.items() if v is None and w.get(k, 0) > 0]}

def build_scores(cond):
    rows = []
    for _, r in cond.iterrows():
        z = ZMAP[r.zone_id]
        rd = r.to_dict()
        for sp in SPECIES:
            if r.zone_id not in sp["zones"]:
                continue
            s = score_row(sp, rd, z)
            if s is None:
                continue
            n = score_row(sp, rd, z, clim_mode=True)
            rows.append({
                "date": r.date, "zone_id": r.zone_id, "zone": z["name"], "band": z["band"],
                "species_id": sp["id"], "species": sp["name"], "group": sp["group"],
                "score": s["score"], "seasonal_norm_score": n["score"] if n else np.nan,
                "vs_norm": round(s["score"] - (n["score"] if n else np.nan), 1) if n else np.nan,
                "core_score": s["core"], "fishability": s["fishability"], "access_note": s["access_note"],
                "is_forecast": bool(r.is_forecast),
                "sst_f": r.get("sst_f"), "sst_anom_f": r.get("sst_anom_f"),
                "wave_ft": r.get("wave_ft"), "wind_kt": r.get("wind_kt"),
                "tide_range_ft": r.get("tide_range_ft"), "moon_illum": r.get("moon_illum"),
                "front_f_per_nm": r.get("front_f_per_nm"),
                "term_sst": s["terms"]["sst"], "term_season": s["terms"]["season"],
                "term_tide": s["terms"]["tide"], "term_moon": s["terms"]["moon"],
                "term_pressure": s["terms"]["pressure"], "term_swell": s["terms"]["swell"],
                "term_front": s["terms"]["front"], "term_chl": s["terms"]["chl"],
                "anomaly_term": s["anomaly_term"],
                "contrib_json": json.dumps(s["contrib"]), "missing_drivers": ",".join(s["missing"]),
                "enso_regime": r.get("enso_regime"), "oni": r.get("oni_latest")})
    return pd.DataFrame(rows)

def climatological_curve(art):
    """Score every day-of-year per zone/species from climatological drivers only: the
    seasonal 'typical opportunity' curve, and the annual range today is ranked against."""
    mur_clim = art['sst_climatology']; sh_clim = art['shore_sst_climatology']; bclim = art['buoy_climatology']
    rows = []
    INSHORE = {'sd_bay', 'mission_bay', 'surf_zone'}
    for z in ZONES:
        if z['id'] in INSHORE and not sh_clim.empty:
            c = sh_clim[sh_clim.tide_station == z['tide_station']].rename(
                columns={'shoresst_norm': 'sst_norm', 'shoresst_sd': 'sst_sd'})[['doy', 'sst_norm', 'sst_sd']]
            src = 'CO-OPS shore climatology'
        elif not mur_clim.empty and (mur_clim.zone_id == z['id']).any():
            c = mur_clim[mur_clim.zone_id == z['id']][['doy', 'sst_norm', 'sst_sd']]
            src = 'MUR satellite climatology'
        else:
            continue
        if c.empty:
            continue
        b = bclim[bclim.station == z['buoys'][0]][['doy', 'wave_norm', 'wind_norm']] if not bclim.empty else pd.DataFrame()
        c = c.merge(b, on='doy', how='left') if not b.empty else c.assign(wave_norm=np.nan, wind_norm=np.nan)
        for _, r in c.iterrows():
            doy = int(r.doy)
            month = (datetime(2025, 1, 1) + timedelta(days=doy - 1)).month
            base = {'month': month, 'sst_norm': r.sst_norm, 'wave_norm': r.wave_norm,
                    'wind_norm': r.wind_norm, 'wave_ft': r.wave_norm, 'wind_kt': r.wind_norm}
            for sp in SPECIES:
                if z['id'] not in sp['zones']:
                    continue
                s = score_row(sp, base, z, clim_mode=True)
                if s:
                    rows.append({'zone_id': z['id'], 'species_id': sp['id'], 'doy': doy, 'month': month,
                                 'climatological_score': s['score'], 'sst_norm_f': r.sst_norm,
                                 'wave_norm_ft': r.wave_norm, 'wind_norm_kt': r.wind_norm,
                                 'climatology_source': src})
    return pd.DataFrame(rows)

# ============================================================ 7. catch / CPUE + validation
TRIP_ZONE = {"half_day": ["sd_bay", "mission_bay", "pt_loma_kelp"], "three_quarter_day": ["pt_loma_kelp", "la_jolla"],
             "full_day": ["coronados", "pt_loma_kelp", "nine_mile"], "overnight": ["coronados", "outer_banks"],
             "1.5_day": ["outer_banks", "nine_mile"], "2_day": ["outer_banks", "cortez_tanner"],
             "2.5_day": ["outer_banks", "cortez_tanner"], "3_day": ["cortez_tanner", "outer_banks"],
             "3.5_day": ["cortez_tanner"], "4_day": ["cortez_tanner"], "7_day": ["cortez_tanner"]}
TRIP_DAYS = {"half_day": 0.5, "three_quarter_day": 0.75, "full_day": 1.0, "twilight": 0.4,
             "overnight": 1.0, "1.5_day": 1.5, "2_day": 2.0, "2.5_day": 2.5, "3_day": 3.0,
             "3.5_day": 3.5, "4_day": 4.0, "5_day": 5.0, "7_day": 7.0}

def catch_frame():
    p = f"{RAW}/catch_reports.json"
    if not os.path.exists(p):
        old = os.path.join(BASELINE, "catch_reports.csv")
        old_daily = os.path.join(BASELINE, "catch_daily_cpue.csv")
        if os.path.exists(old):
            return pd.read_csv(old), pd.read_csv(old_daily) if os.path.exists(old_daily) else pd.DataFrame()
        return pd.DataFrame(), pd.DataFrame()
    j = json.load(open(p))
    rows = []
    for r in j["reports"]:
        if not r.get("date") or not r.get("anglers"):
            continue
        for sid, n in (r.get("catch") or {}).items():
            rows.append({"date": pd.Timestamp(r["date"]), "landing": r.get("landing"), "boat": r.get("boat"),
                         "trip_type": r.get("trip_type"), "anglers": r["anglers"], "species_id": sid,
                         "kept": n, "released": (r.get("released") or {}).get(sid, 0),
                         "zone_hint": r.get("zone_hint"), "source_url": r.get("source_url")})
    df = pd.DataFrame(rows)
    old = os.path.join(BASELINE, "catch_reports.csv")
    if os.path.exists(old):
        prior = pd.read_csv(old)
        prior["date"] = pd.to_datetime(prior["date"], errors="coerce")
        if not df.empty:
            # A successful daily scrape is authoritative only for the date(s) it
            # contains. Preserve all earlier rows at table level; reconstructing
            # trips from species rows loses distinct same-boat outings.
            current_dates = set(pd.to_datetime(df["date"]).dt.normalize())
            prior = prior[~prior["date"].dt.normalize().isin(current_dates)]
            df = pd.concat([prior, df], ignore_index=True, sort=False)
        else:
            df = prior
    if df.empty:
        return df, df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["trip_days"] = df.trip_type.map(TRIP_DAYS).fillna(1.0)
    df["cpue"] = df.kept / df.anglers
    df["cpue_per_angler_day"] = df.kept / (df.anglers * df.trip_days)
    daily = (df.groupby(["date", "species_id"])
               .agg(kept=("kept", "sum"), released=("released", "sum"), anglers=("anglers", "sum"),
                    angler_days=("anglers", lambda s: float((df.loc[s.index, "anglers"] * df.loc[s.index, "trip_days"]).sum())),
                    trips=("boat", "size")).reset_index())
    daily["cpue"] = daily.kept / daily.anglers
    daily["cpue_per_angler_day"] = daily.kept / daily.angler_days
    return df, daily

VALIDATION_COLUMNS = [
    "species_id", "species", "n_days", "spearman_rho", "pearson_r",
    "spearman_rho_raw_cpue", "mean_cpue_per_angler_day", "mean_score",
    "total_fish", "total_anglers",
]


def prior_model_validation():
    path = os.path.join(BASELINE, "model_validation.csv")
    if os.path.exists(path):
        try:
            prior = pd.read_csv(path)
            if set(VALIDATION_COLUMNS).issubset(prior.columns):
                return prior[VALIDATION_COLUMNS]
        except (OSError, ValueError):
            pass
    return pd.DataFrame(columns=VALIDATION_COLUMNS)


def validate(scores, catch_daily):
    """Rank-correlate modeled opportunity vs observed fish-per-angler where both exist."""
    if catch_daily.empty or scores.empty:
        return prior_model_validation()
    obs = scores[~scores.is_forecast]
    rows = []
    for sid, g in catch_daily.groupby("species_id"):
        if sid not in SMAP:
            continue
        zs = SMAP[sid]["zones"]
        m = (obs[(obs.species_id == sid) & (obs.zone_id.isin(zs))]
             .groupby("date").score.max().reset_index())
        j = g.merge(m, on="date", how="inner").dropna(subset=["score", "cpue_per_angler_day"])
        if len(j) < 5 or j.cpue_per_angler_day.std() == 0 or j.score.std() == 0:
            continue
        sr = j[["score", "cpue_per_angler_day"]].rank().corr().iloc[0, 1]
        pr = j[["score", "cpue_per_angler_day"]].corr().iloc[0, 1]
        sr_raw = j[["score", "cpue"]].rank().corr().iloc[0, 1]
        rows.append({"species_id": sid, "species": SMAP[sid]["name"], "n_days": len(j),
                     "spearman_rho": round(sr, 3), "pearson_r": round(pr, 3),
                     "spearman_rho_raw_cpue": round(sr_raw, 3),
                     "mean_cpue_per_angler_day": round(j.cpue_per_angler_day.mean(), 3),
                     "mean_score": round(j.score.mean(), 1),
                     "total_fish": int(j.kept.sum()), "total_anglers": int(j.anglers.sum())})
    if not rows:
        return prior_model_validation()
    return pd.DataFrame(rows, columns=VALIDATION_COLUMNS).sort_values(
        "spearman_rho", ascending=False
    )

# ============================================================ 8. run + export
def main():
    art = assemble()
    cond = art["conditions_daily"]
    scores = build_scores(cond)
    curve = climatological_curve(art)
    art['climatological_curve'] = curve
    if not curve.empty and not scores.empty:
        idx = {k: np.sort(g.climatological_score.values) for k, g in curve.groupby(['zone_id', 'species_id'])}
        def pct(r):
            a = idx.get((r.zone_id, r.species_id))
            if a is None or not len(a) or pd.isna(r.score):
                return np.nan
            return round(float(np.searchsorted(a, r.score) / len(a) * 100), 1)
        scores['annual_percentile'] = scores.apply(pct, axis=1)
    art['scores_daily'] = scores
    trips, catch_daily = catch_frame()
    art["catch_reports"] = trips
    art["catch_daily_cpue"] = catch_daily
    art["model_validation"] = validate(scores, catch_daily)
    art["dim_zones"] = pd.DataFrame(ZONES).assign(buoys=lambda d: d.buoys.map(lambda x: ",".join(x)))
    art["dim_species"] = pd.DataFrame([{
        "species_id": s["id"], "species": s["name"], "group": s["group"],
        "temp_reference": s.get("temp_ref", "surface"),
        "sst_min_f": s["sst_f"][0], "sst_opt_lo_f": s["sst_f"][1], "sst_opt_hi_f": s["sst_f"][2],
        "sst_max_f": s["sst_f"][3], "anomaly_pref": s["anomaly_pref"],
        "tide_pref": s["tide_pref"], "moon_pref": s["moon_pref"],
        "swell_tolerance_ft": s["swell_tolerance_ft"], "depth_min_ft": s["depth_ft"][0],
        "depth_max_ft": s["depth_ft"][1], "zones": ",".join(s["zones"]),
        "season_index_monthly": ",".join(str(x) for x in s["season_index"]),
        "weights_json": json.dumps(s["weights"]), "notes": s["notes"],
        "sources": " ; ".join(s["sources"])} for s in SPECIES])
    for k, v in art.items():
        if isinstance(v, pd.DataFrame) and not v.empty:
            v.to_csv(f"{OUT}/{k}.csv", index=False)
            print(f"{k}: {len(v):,} rows -> {k}.csv")
        else:
            print(f"{k}: EMPTY")
    def yr_span(key, col="date"):
        t = art.get(key)
        if t is None or getattr(t, "empty", True):
            return None
        y = pd.to_datetime(t[col]).dt.year
        return f"{int(y.min())}–{int(y.max())}"

    json.dump({"run_utc": RUN_TS.isoformat(), "zones": len(ZONES), "species": len(SPECIES),
               "score_rows": int(len(scores)),
               "baseline": yr_span("mur_history") or "2015–present",
               "baseline_satellite": yr_span("mur_history"),
               "baseline_shore": yr_span("shore_sst_daily"),
               "baseline_buoy": yr_span("buoy_daily")},
              open(f"{OUT}/run_meta.json", "w"), indent=2)

if __name__ == "__main__":
    main()
