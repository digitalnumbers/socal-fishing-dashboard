"""Parsing + feature engineering for SoCal Fishing Intelligence."""
import json, os, glob, math
from datetime import datetime, date, timedelta, timezone
import numpy as np
import pandas as pd

ROOT = os.environ.get("SOCAL_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.environ.get("SOCAL_RAW", os.path.join(ROOT, "data", "raw"))
CFG = os.path.join(ROOT, "pipeline", "config")
ZONES = json.load(open(f"{CFG}/zones.json"))
SPECIES = json.load(open(f"{CFG}/species.json"))
MISS = {"MM", "999", "99.0", "999.0", "9999.0", "99.00", "9999", "99"}

def c2f(c):
    return None if c is None or (isinstance(c, float) and math.isnan(c)) else c * 9 / 5 + 32

# ------------------------------------------------------------------ NDBC
NDBC_COLS = ["YY","MM","DD","hh","mm","WDIR","WSPD","GST","WVHT","DPD","APD","MWD",
             "PRES","ATMP","WTMP","DEWP","VIS","PTDY","TIDE"]

def _ndbc_frame(path):
    rows = []
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.split()
            if len(p) < 15:
                continue
            rows.append(p)
    if not rows:
        return pd.DataFrame()
    n = len(rows[0])
    cols = NDBC_COLS[:n] if n <= len(NDBC_COLS) else NDBC_COLS + [f"X{i}" for i in range(n - len(NDBC_COLS))]
    # historical files omit PTDY
    if n == 18 and "PTDY" in cols:
        cols = [c for c in NDBC_COLS if c != "PTDY"][:n]
    df = pd.DataFrame(rows, columns=cols[:n])
    for c in df.columns:
        df[c] = pd.to_numeric(df[c].replace(list(MISS), np.nan), errors="coerce")
    for c, lim in [("WSPD",90),("GST",90),("WVHT",90),("DPD",90),("APD",90),("PRES",9000),
                   ("ATMP",900),("WTMP",900),("DEWP",900),("VIS",90),("WDIR",900),("MWD",900)]:
        if c in df:
            df.loc[df[c] >= lim, c] = np.nan
    df["ts"] = pd.to_datetime(dict(year=df.YY, month=df.MM, day=df.DD, hour=df.hh, minute=df.mm),
                              errors="coerce", utc=True)
    return df.dropna(subset=["ts"]).sort_values("ts")

def ndbc_all():
    """Return (hourly_df, daily_df) across all stations, realtime + historical."""
    frames = []
    for p in sorted(glob.glob(f"{RAW}/ndbc_rt_*.txt")) + sorted(glob.glob(f"{RAW}/ndbc_hist_*.txt")):
        base = os.path.basename(p)
        sid = base.replace("ndbc_rt_", "").replace("ndbc_hist_", "").split("_")[0].replace(".txt", "")
        d = _ndbc_frame(p)
        if d.empty:
            continue
        d["station"] = sid
        d["src"] = "realtime" if "_rt_" in base else "historical"
        frames.append(d[["ts","station","src","WDIR","WSPD","GST","WVHT","DPD","APD","MWD","PRES","ATMP","WTMP"]])
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["station","ts"], keep="first")
    df["wind_kt"] = df.WSPD * 1.94384
    df["gust_kt"] = df.GST * 1.94384
    df["wave_ft"] = df.WVHT * 3.28084
    df["wtmp_f"] = df.WTMP * 9 / 5 + 32
    df["atmp_f"] = df.ATMP * 9 / 5 + 32
    hourly = (df.set_index("ts").groupby("station")
                .resample("1h")[["wind_kt","gust_kt","wave_ft","DPD","APD","MWD","PRES","wtmp_f","atmp_f","WDIR"]]
                .mean().reset_index())
    hourly["date"] = hourly.ts.dt.tz_convert("America/Los_Angeles").dt.date
    agg = {"wind_kt":["mean","max"],"gust_kt":"max","wave_ft":["mean","max"],"DPD":"mean",
           "MWD":"mean","PRES":["mean","min"],"wtmp_f":"mean","atmp_f":"mean","WDIR":"mean"}
    daily = hourly.groupby(["station","date"]).agg(agg)
    daily.columns = ["_".join(c).strip("_") for c in daily.columns]
    daily = daily.reset_index().rename(columns={
        "wind_kt_mean":"wind_kt","wind_kt_max":"wind_kt_max","gust_kt_max":"gust_kt_max",
        "wave_ft_mean":"wave_ft","wave_ft_max":"wave_ft_max","DPD_mean":"swell_period_s",
        "MWD_mean":"swell_dir_deg","PRES_mean":"pressure_hpa","PRES_min":"pressure_hpa_min",
        "wtmp_f_mean":"buoy_sst_f","atmp_f_mean":"air_temp_f","WDIR_mean":"wind_dir_deg"})
    daily["date"] = pd.to_datetime(daily.date)
    daily = daily.sort_values(["station","date"])
    daily["pressure_trend_24h"] = daily.groupby("station").pressure_hpa.diff()
    return hourly, daily

# ------------------------------------------------------------------ CO-OPS
def coops_json(pattern, key):
    out = []
    for p in sorted(glob.glob(f"{RAW}/{pattern}")):
        j = json.load(open(p))
        rows = j.get(key) or j.get("data") or []
        st = os.path.basename(p).split("_")[1]
        for r in rows:
            out.append({"station": st, "t": r["t"], "v": r.get("v"), "type": r.get("type")})
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["ts"] = pd.to_datetime(df.t)
    df["v"] = pd.to_numeric(df.v, errors="coerce")
    return df.dropna(subset=["v"]).sort_values(["station","ts"])

def tide_features():
    """Daily tide metrics from hi/lo predictions: range, exchange rate, timing of best water."""
    hilo = coops_json("coops_*_predictions_*.json", "predictions")
    hilo = hilo[hilo.type.notna()].drop_duplicates(subset=["station","ts"])
    rows = []
    for st, g in hilo.groupby("station"):
        g = g.sort_values("ts").reset_index(drop=True)
        g["dt_h"] = g.ts.diff().dt.total_seconds() / 3600
        g["dv"] = g.v.diff()
        g["rate"] = (g.dv / g.dt_h).abs()
        g["date"] = g.ts.dt.date
        for d, gd in g.groupby("date"):
            if len(gd) < 2:
                continue
            best = gd.loc[gd.rate.idxmax()] if gd.rate.notna().any() else gd.iloc[-1]
            mid = best.ts - timedelta(hours=float(best.dt_h or 0) / 2)
            rows.append({"tide_station": st, "date": pd.Timestamp(d),
                         "tide_high_ft": gd.v.max(), "tide_low_ft": gd.v.min(),
                         "tide_range_ft": gd.v.max() - gd.v.min(),
                         "tide_exchanges": len(gd),
                         "max_exchange_rate_ft_h": float(gd.rate.max(skipna=True)) if gd.rate.notna().any() else np.nan,
                         "best_water_hour": mid.hour + mid.minute / 60,
                         "first_high_hour": (gd[gd.type=="H"].ts.dt.hour.min()
                                             if (gd.type=="H").any() else np.nan)})
    return pd.DataFrame(rows).drop_duplicates(subset=["tide_station","date"])

def coops_obs():
    wt = coops_json("coops_*_water_temperature_*.json", "data")
    pr = coops_json("coops_*_air_pressure_*.json", "data")
    out = {}
    if not wt.empty:
        wt["date"] = wt.ts.dt.date
        out["shore_sst_f"] = wt.groupby(["station","date"]).v.mean()
    if not pr.empty:
        pr["date"] = pr.ts.dt.date
        out["shore_pressure_mb"] = pr.groupby(["station","date"]).v.mean()
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out).reset_index().rename(columns={"station":"tide_station"})
    df["date"] = pd.to_datetime(df.date)
    return df

# ------------------------------------------------------------------ MUR SST
def mur_zone_series():
    frames = []
    for z in ZONES:
        p = f"{RAW}/mur_series_{z['id']}.csv"
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p, skiprows=[1])
        d = d.rename(columns={"analysed_sst": "sst_c"})
        d["date"] = pd.to_datetime(d.time).dt.tz_localize(None).dt.normalize()
        d["zone_id"] = z["id"]
        d["sst_f"] = d.sst_c * 9 / 5 + 32
        frames.append(d[["zone_id","date","sst_f"]])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def sst_climatology(sst):
    """Day-of-year climatology per zone using a centered +/-7 day window across all years."""
    sst = sst.copy()
    sst["doy"] = sst.date.dt.dayofyear
    sst["year"] = sst.date.dt.year
    rows = []
    for zid, g in sst.groupby("zone_id"):
        by_doy = {d: v.sst_f.values for d, v in g.groupby("doy")}
        for doy in range(1, 367):
            vals = np.concatenate([by_doy.get(((doy + k - 1) % 366) + 1, np.array([]))
                                   for k in range(-7, 8)]) if by_doy else np.array([])
            vals = vals[~np.isnan(vals)]
            if len(vals) == 0:
                continue
            rows.append({"zone_id": zid, "doy": doy, "sst_norm_f": vals.mean(),
                         "sst_sd_f": vals.std(ddof=1) if len(vals) > 1 else np.nan,
                         "sst_p10_f": np.percentile(vals, 10), "sst_p90_f": np.percentile(vals, 90),
                         "n_obs": len(vals)})
    clim = pd.DataFrame(rows)
    out = sst.merge(clim, on=["zone_id","doy"], how="left")
    out["sst_anom_f"] = out.sst_f - out.sst_norm_f
    out["sst_pctile"] = np.nan
    for zid, g in out.groupby("zone_id"):
        for doy, gd in g.groupby("doy"):
            ref = gd.sst_f.values
            out.loc[gd.index, "sst_pctile"] = [float((ref < v).mean() * 100) for v in gd.sst_f]
    out = out.sort_values(["zone_id","date"])
    out["sst_trend_7d"] = out.groupby("zone_id").sst_f.diff(7)
    return out, clim

def mur_fronts():
    """Daily max SST gradient (front strength, F per nautical mile) near each zone."""
    p = f"{RAW}/mur_grid_recent.csv"
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_csv(p, skiprows=[1]).rename(columns={"analysed_sst": "sst_c"})
    d["date"] = pd.to_datetime(d.time).dt.tz_localize(None).dt.normalize()
    rows = []
    for dt, g in d.groupby("date"):
        piv = g.pivot_table(index="latitude", columns="longitude", values="sst_c")
        lats, lons = piv.index.values, piv.columns.values
        arr = piv.values
        gy, gx = np.gradient(arr)
        dy_nm = (lats[1] - lats[0]) * 60 if len(lats) > 1 else 0.6
        dx_nm = (lons[1] - lons[0]) * 60 * math.cos(math.radians(np.nanmean(lats))) if len(lons) > 1 else 0.6
        grad = np.sqrt((gy / dy_nm) ** 2 + (gx / dx_nm) ** 2) * 9 / 5  # F per nm
        for z in ZONES:
            iy = np.argmin(np.abs(lats - z["lat"])); ix = np.argmin(np.abs(lons - z["lon"]))
            w = 8
            sub = grad[max(0, iy-w):iy+w+1, max(0, ix-w):ix+w+1]
            sst_sub = arr[max(0, iy-w):iy+w+1, max(0, ix-w):ix+w+1]
            if np.all(np.isnan(sub)):
                continue
            rows.append({"zone_id": z["id"], "date": dt,
                         "front_f_per_nm": float(np.nanmax(sub)),
                         "front_mean_f_per_nm": float(np.nanmean(sub)),
                         "sst_spread_f": float((np.nanmax(sst_sub) - np.nanmin(sst_sub)) * 9 / 5)})
    return pd.DataFrame(rows)

# ------------------------------------------------------------------ ENSO
def oni():
    rows = []
    for ln in open(f"{RAW}/cpc_oni.ascii.txt").read().splitlines()[1:]:
        p = ln.split()
        if len(p) != 4:
            continue
        rows.append({"season": p[0], "year": int(p[1]), "sst_c": float(p[2]), "oni": float(p[3])})
    df = pd.DataFrame(rows)
    seasons = ["DJF","JFM","FMA","MAM","AMJ","MJJ","JJA","JAS","ASO","SON","OND","NDJ"]
    df["month"] = df.season.map({s: i + 1 for i, s in enumerate(seasons)})
    def cls(v):
        if v >= 1.5: return "Strong El Nino"
        if v >= 0.5: return "El Nino"
        if v <= -1.5: return "Strong La Nina"
        if v <= -0.5: return "La Nina"
        return "Neutral"
    df["regime"] = df.oni.map(cls)
    df["simple_regime"] = df.oni.map(lambda v: "El Nino" if v >= 0.5 else ("La Nina" if v <= -0.5 else "Neutral"))
    return df.sort_values(["year","month"])

def enso_zone_composite(sst_feat, o):
    """Mean SST anomaly by ENSO regime x month x zone -> the data-derived regime adjustment."""
    s = sst_feat.copy()
    s["year"] = s.date.dt.year; s["month"] = s.date.dt.month
    m = s.merge(o[["year","month","oni","simple_regime"]], on=["year","month"], how="left")
    comp = (m.groupby(["zone_id","month","simple_regime"])
              .agg(mean_anom_f=("sst_anom_f","mean"), n_days=("sst_anom_f","size")).reset_index())
    sens = (m.dropna(subset=["oni","sst_anom_f"]).groupby(["zone_id","month"])
              .apply(lambda g: np.polyfit(g.oni, g.sst_anom_f, 1)[0] if len(g) > 30 and g.oni.std() > 0.15 else np.nan,
                     include_groups=False)
              .rename("anom_f_per_oni_unit").reset_index())
    return comp, sens, m

# ------------------------------------------------------------------ upwelling
def cuti_daily():
    p = f"{RAW}/cuti_daily.csv"
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_csv(p, skiprows=[1])
    d["date"] = pd.to_datetime(d.time).dt.tz_localize(None).dt.normalize()
    d = d[np.isclose(d.latitude, 33.0) | np.isclose(d.latitude, 32.5)]
    return d.groupby("date").CUTI.mean().reset_index().rename(columns={"CUTI": "cuti"})

# ------------------------------------------------------------------ chlorophyll
def chl_zone():
    p = f"{RAW}/chl_recent.csv"
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_csv(p, skiprows=[1])
    var = [c for c in d.columns if "chl" in c.lower()][-1]
    d["date"] = pd.to_datetime(d.time).dt.tz_localize(None).dt.normalize()
    rows = []
    for dt, g in d.groupby("date"):
        for z in ZONES:
            sub = g[(g.latitude.sub(z["lat"]).abs() < 0.12) & (g.longitude.sub(z["lon"]).abs() < 0.12)]
            v = pd.to_numeric(sub[var], errors="coerce").dropna()
            if len(v):
                rows.append({"zone_id": z["id"], "date": dt, "chl_mg_m3": float(v.median())})
    return pd.DataFrame(rows)

# ------------------------------------------------------------------ moon / sun
def moon_phase(d):
    """Meeus low-precision: returns (illumination 0-1, phase_angle_deg, phase_name, days_since_new)."""
    dt = datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)
    jd = dt.timestamp() / 86400.0 + 2440587.5
    T = (jd - 2451545.0) / 36525
    D = (297.8501921 + 445267.1114034*T - 0.0018819*T**2) % 360
    M = (357.5291092 + 35999.0502909*T) % 360
    Mp = (134.9633964 + 477198.8675055*T) % 360
    r = math.radians
    i = (180 - D - 6.289*math.sin(r(Mp)) + 2.100*math.sin(r(M))
         - 1.274*math.sin(r(2*D - Mp)) - 0.658*math.sin(r(2*D))
         - 0.214*math.sin(r(2*Mp)) - 0.110*math.sin(r(D))) % 360
    illum = (1 + math.cos(r(i))) / 2
    age = ((1 - i / 180) * 14.765) % 29.53 if i <= 180 else ((1 + (360 - i) / 180) * 14.765) % 29.53
    syn = 29.530588
    age = ((jd - 2451550.1) / syn % 1) * syn
    names = [(1.5,"New"),(6.0,"Waxing Crescent"),(9.0,"First Quarter"),(13.5,"Waxing Gibbous"),
             (16.5,"Full"),(21.0,"Waning Gibbous"),(24.0,"Last Quarter"),(28.1,"Waning Crescent"),(30,"New")]
    name = next(n for lim, n in names if age <= lim)
    return illum, i, name, age

def sun_times(d, lat=32.72, lon=-117.22):
    """NOAA solar equations -> (sunrise_hour, sunset_hour, daylight_hours) in local standard time."""
    n = d.timetuple().tm_yday
    gamma = 2*math.pi/365*(n - 1 + 0.5)
    eqtime = 229.18*(0.000075 + 0.001868*math.cos(gamma) - 0.032077*math.sin(gamma)
                     - 0.014615*math.cos(2*gamma) - 0.040849*math.sin(2*gamma))
    decl = (0.006918 - 0.399912*math.cos(gamma) + 0.070257*math.sin(gamma)
            - 0.006758*math.cos(2*gamma) + 0.000907*math.sin(2*gamma)
            - 0.002697*math.cos(3*gamma) + 0.00148*math.sin(3*gamma))
    la = math.radians(lat)
    cosH = (math.cos(math.radians(90.833)) / (math.cos(la)*math.cos(decl))) - math.tan(la)*math.tan(decl)
    cosH = max(-1, min(1, cosH))
    ha = math.degrees(math.acos(cosH))
    sunrise = (720 + 4*(-lon - ha) - eqtime) / 60 - 8  # PST offset
    sunset = (720 + 4*(-lon + ha) - eqtime) / 60 - 8
    dst = 3 <= d.month <= 10
    if dst:
        sunrise += 1; sunset += 1
    return sunrise, sunset, sunset - sunrise
