"""Fetch extended-range (day 8-30) source data for the SoCal Fishing Intelligence outlook.

This script is additive: it touches none of the existing 3-7 day fetch scripts and writes to its
own raw directory. Four families of source are pulled:

  1. CPC 8-14 day temperature / precipitation outlook  (real probabilistic forecast, days 8-14)
  2. CPC weeks 3-4 temperature / precipitation outlook (real probabilistic forecast, days 15-28)
  3. CPC monthly / seasonal temperature outlook        (real probabilistic forecast, tail of window)
  4. CO-OPS harmonic tide predictions, 40 days ahead   (astronomical -- exact at any lead time)
  5. Open-Meteo 16-day deterministic marine + atmosphere (model forecast, useful to ~day 14 only)

Everything the extended outlook needs that is NOT in this list (day-of-year SST/wave/wind
climatology, ENSO composites, the climatological opportunity curve) already exists in
dataset/csv/ and is read straight from there by build_extended.py -- nothing is refetched.

Usage:  python3 pipeline/fetch_extended.py [--raw DIR]
"""
import argparse
import datetime as dt
import io
import json
import os
import ssl
import sys
import urllib.request
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RAW = os.environ.get("SOCAL_RAW_EXT", "/home/user/workspace/socal/data/raw_extended")

CPC_GIS = "https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/"
COOPS = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
         "?product=predictions&application=socal_fishing_intelligence"
         "&begin_date={b}&end_date={e}&datum=MLLW&station={st}"
         "&time_zone=lst_ldt&units=english&interval=hilo&format=json")
OM_MARINE = ("https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
             "&daily=wave_height_max,wave_period_max,swell_wave_height_max,wave_direction_dominant"
             "&forecast_days=16&timezone=America%2FLos_Angeles")
OM_WX = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
         "&daily=wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,"
         "pressure_msl_mean,cloud_cover_mean,temperature_2m_max&forecast_days=16"
         "&timezone=America%2FLos_Angeles")

TIDE_STATIONS = ["9410170", "9410230"]
CTX = ssl.create_default_context()


def _get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "socal-fishing-intelligence/1.1"})
    return urllib.request.urlopen(req, timeout=timeout, context=CTX).read()


def latest_cpc(product, run_date, back=8):
    """CPC GIS products are posted per issuance date; walk back to the most recent one."""
    for i in range(back):
        d = run_date - dt.timedelta(days=i)
        name = f"{product}_{d:%Y%m%d}.zip"
        try:
            return name, _get(CPC_GIS + name)
        except Exception:
            continue
    return None, None


def latest_cpc_monthly(product, run_date, back=6):
    """Monthly / seasonal products are posted per year-month."""
    y, m = run_date.year, run_date.month
    for _ in range(back):
        name = f"{product}_{y}{m:02d}.zip"
        try:
            return name, _get(CPC_GIS + name)
        except Exception:
            m -= 1
            if m == 0:
                m, y = 12, y - 1
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=DEFAULT_RAW)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD override for reproducible builds")
    a = ap.parse_args()
    raw = a.raw
    os.makedirs(raw, exist_ok=True)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    log = {"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "today": today.isoformat(),
           "ok": [], "failed": []}

    # ---- 1/2/3. CPC probabilistic outlooks (shapefile zips) -------------------------------
    cpc_daily = ["814temp", "814prcp", "wk34temp", "wk34prcp"]
    cpc_monthly = ["seastemp", "seasprcp", "monthupd_temp", "monthupd_prcp"]
    for p in cpc_daily:
        name, blob = latest_cpc(p, today)
        if blob:
            d = os.path.join(raw, "cpc", p)
            os.makedirs(d, exist_ok=True)
            zipfile.ZipFile(io.BytesIO(blob)).extractall(d)
            open(os.path.join(d, "_source.txt"), "w").write(CPC_GIS + name)
            log["ok"].append({"source": "cpc_" + p, "file": name, "bytes": len(blob)})
            print(f"cpc {p:14s} {name}  {len(blob)/1e6:.1f} MB")
        else:
            log["failed"].append("cpc_" + p)
            print(f"cpc {p:14s} MISSING", file=sys.stderr)
    for p in cpc_monthly:
        name, blob = latest_cpc_monthly(p, today)
        if blob:
            d = os.path.join(raw, "cpc", p)
            os.makedirs(d, exist_ok=True)
            zipfile.ZipFile(io.BytesIO(blob)).extractall(d)
            open(os.path.join(d, "_source.txt"), "w").write(CPC_GIS + name)
            log["ok"].append({"source": "cpc_" + p, "file": name, "bytes": len(blob)})
            print(f"cpc {p:14s} {name}  {len(blob)/1e6:.1f} MB")
        else:
            log["failed"].append("cpc_" + p)

    # ---- 4. CO-OPS harmonic tide predictions, out to day 40 -------------------------------
    b, e = today, today + dt.timedelta(days=40)
    for st in TIDE_STATIONS:
        try:
            blob = _get(COOPS.format(b=f"{b:%Y%m%d}", e=f"{e:%Y%m%d}", st=st))
            open(os.path.join(raw, f"coops_{st}_predictions_ext.json"), "wb").write(blob)
            log["ok"].append({"source": "coops_predictions_ext", "station": st, "bytes": len(blob)})
            print(f"coops tide     {st}  {len(blob)/1024:.0f} KB")
        except Exception as ex:
            log["failed"].append(f"coops_{st}")
            print(f"coops tide     {st} FAILED {ex}", file=sys.stderr)

    # ---- 5. Open-Meteo 16-day deterministic model (marine + atmosphere) -------------------
    zones = json.load(open(os.path.join(REPO, "pipeline", "config", "zones.json")))
    om = {}
    for z in zones:
        rec = {}
        for key, tmpl in (("marine", OM_MARINE), ("weather", OM_WX)):
            try:
                rec[key] = json.loads(_get(tmpl.format(lat=z["lat"], lon=z["lon"])))
            except Exception as ex:
                rec[key] = {"error": str(ex)}
        om[z["id"]] = rec
        print(f"open-meteo 16d {z['id']}")
    json.dump(om, open(os.path.join(raw, "openmeteo_16day.json"), "w"))
    log["ok"].append({"source": "openmeteo_16day", "zones": len(zones)})

    json.dump(log, open(os.path.join(raw, "fetch_extended_log.json"), "w"), indent=1)
    print(f"\nraw -> {raw}   ok={len(log['ok'])} failed={len(log['failed'])}")


if __name__ == "__main__":
    main()
