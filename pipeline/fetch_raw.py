"""SoCal Fishing Intelligence — raw data acquisition layer.

Pulls every source in the approved registry into data/raw/ as-is (no cleaning),
so the transform layer is reproducible and auditable.
"""
import json, os, sys, time, gzip, io
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

RAW = "/home/user/workspace/socal/data/raw"
os.makedirs(RAW, exist_ok=True)
UA = {"User-Agent": "SoCalFishingIntelligence/1.0 (personal research; contact jeffjcho6@gmail.com)"}

# ---------------------------------------------------------------- zones
ZONES = json.load(open("/home/user/workspace/socal/config/zones.json"))

def get(url, timeout=120, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if url.endswith(".gz"):
        b = gzip.decompress(b)
    return b if binary else b.decode("utf-8", "replace")

def save(name, text):
    p = os.path.join(RAW, name)
    with open(p, "w") as f:
        f.write(text)
    return f"{name} ({len(text):,} chars)"

def task(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return f"FAIL {fn.__name__} {a[:1]}: {type(e).__name__} {e}"

# ---------------------------------------------------------------- NDBC
NDBC_STATIONS = ["46232", "46258", "46231", "46086", "46235", "46225", "SDBC1", "LJAC1", "PTOC1"]
NDBC_HIST_YEARS = list(range(2019, 2026))

def ndbc_realtime(sid):
    return save(f"ndbc_rt_{sid}.txt", get(f"https://www.ndbc.noaa.gov/data/realtime2/{sid}.txt"))

def ndbc_hist(sid, yr):
    u = (f"https://www.ndbc.noaa.gov/view_text_file.php?filename={sid.lower()}h{yr}.txt.gz"
         f"&dir=data/historical/stdmet/")
    return save(f"ndbc_hist_{sid}_{yr}.txt", get(u))

# ---------------------------------------------------------------- CO-OPS
COOPS = {"9410170": "San Diego Bay (Broadway Pier)", "9410230": "La Jolla (Scripps Pier)"}
TODAY = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-7))).date()

def coops(station, product, begin, end, extra=""):
    u = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?"
         f"product={product}&application=SoCalFishingIntelligence&station={station}"
         f"&begin_date={begin}&end_date={end}&time_zone=lst_ldt&units=english&format=json{extra}")
    return save(f"coops_{station}_{product}_{begin}_{end}.json", get(u))

# ---------------------------------------------------------------- ERDDAP MUR SST
def mur_series(zone):
    """Daily 1km SST time series 2003-06-01 -> now at zone centroid."""
    lat, lon = zone["lat"], zone["lon"]
    end = (TODAY - timedelta(days=2)).isoformat()
    u = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv?analysed_sst"
         f"%5B(2003-01-01T09:00:00Z):1:({end}T09:00:00Z)%5D%5B({lat})%5D%5B({lon})%5D")
    return save(f"mur_series_{zone['id']}.csv", get(u, timeout=300))

def mur_grid():
    """Recent 1km SST grid over the whole fishing footprint for front/gradient analysis."""
    end = (TODAY - timedelta(days=2)).isoformat()
    start = (TODAY - timedelta(days=9)).isoformat()
    u = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv?analysed_sst"
         f"%5B({start}T09:00:00Z):1:({end}T09:00:00Z)%5D%5B(32.0):(33.3)%5D%5B(-119.4):(-117.05)%5D")
    return save("mur_grid_recent.csv", get(u, timeout=600))

# ---------------------------------------------------------------- ENSO
def enso():
    out = [save("cpc_oni.ascii.txt", get("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"))]
    try:
        out.append(save("cpc_nino34_weekly.txt",
                        get("https://www.cpc.ncep.noaa.gov/data/indices/wksst8110.for")))
    except Exception as e:
        out.append(f"weekly nino34 unavailable: {e}")
    return " | ".join(out)

# ---------------------------------------------------------------- upwelling (CUTI/BEUTI)
def cuti():
    u = ("https://oceanview.pfeg.noaa.gov/erddap/tabledap/erdCUTI.csv?"
         "time,latitude,CUTI&time%3E=2003-01-01&latitude%3E=32.5&latitude%3C=33.5")
    return save("cuti_daily.csv", get(u, timeout=300))

# ---------------------------------------------------------------- NWS forecast
NWS_POINTS = {"coastal_sd": (32.72, -117.22), "offshore_9mile": (32.62, -117.42)}
def nws(name, lat, lon):
    meta = json.loads(get(f"https://api.weather.gov/points/{lat},{lon}"))
    fc = meta["properties"]["forecast"]
    grid = meta["properties"]["forecastGridData"]
    a = save(f"nws_forecast_{name}.json", get(fc))
    b = save(f"nws_grid_{name}.json", get(grid, timeout=180))
    return a + " | " + b

def nws_marine():
    out = []
    for zid in ["PZZ750", "PZZ775", "PZZ725"]:
        try:
            out.append(save(f"nws_marine_{zid}.json",
                            get(f"https://api.weather.gov/zones/forecast/{zid}/forecast")))
        except Exception as e:
            out.append(f"{zid} FAIL {e}")
    return " | ".join(out)

# ---------------------------------------------------------------- chlorophyll
CHL_CANDIDATES = [
    ("nesdisVHNSQchlaDaily", "chlor_a"), ("nesdisVHNSQchlaDaily", "chla"),
    ("nesdisVHNchlaWeekly", "chlor_a"), ("erdVHNchla3day", "chla"),
    ("erdMH1chla1day", "chlorophyll"), ("erdMH1chla8day", "chlorophyll"),
]
def chl():
    end = (TODAY - timedelta(days=3)).isoformat()
    start = (TODAY - timedelta(days=20)).isoformat()
    for ds, var in CHL_CANDIDATES:
        for zdim in ["%5B(0.0)%5D", ""]:
            u = (f"https://coastwatch.pfeg.noaa.gov/erddap/griddap/{ds}.csv?{var}"
                 f"%5B({start}):1:({end})%5D{zdim}%5B(32.2):(33.2)%5D%5B(-118.6):(-117.1)%5D")
            try:
                t = get(u, timeout=300)
                if t.startswith("time") and len(t) > 200:
                    return save("chl_recent.csv", t) + f"  [{ds}/{var}]"
            except Exception:
                continue
    return "CHL: no dataset resolved"

# ---------------------------------------------------------------- run
if __name__ == "__main__":
    jobs = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for s in NDBC_STATIONS:
            jobs.append(ex.submit(task, ndbc_realtime, s))
        for s in ["46232", "46258", "46086", "46231"]:
            for y in NDBC_HIST_YEARS:
                jobs.append(ex.submit(task, ndbc_hist, s, y))
        b = (TODAY - timedelta(days=45)).strftime("%Y%m%d")
        e = (TODAY + timedelta(days=9)).strftime("%Y%m%d")
        for st in COOPS:
            jobs.append(ex.submit(task, coops, st, "predictions", b, e, "&datum=MLLW&interval=hilo"))
            jobs.append(ex.submit(task, coops, st, "predictions", TODAY.strftime("%Y%m%d"), e,
                                  "&datum=MLLW&interval=30"))
            jobs.append(ex.submit(task, coops, st, "water_temperature", b, TODAY.strftime("%Y%m%d")))
            jobs.append(ex.submit(task, coops, st, "air_pressure", b, TODAY.strftime("%Y%m%d")))
        for z in ZONES:
            jobs.append(ex.submit(task, mur_series, z))
        jobs.append(ex.submit(task, mur_grid))
        jobs.append(ex.submit(task, enso))
        jobs.append(ex.submit(task, cuti))
        jobs.append(ex.submit(task, chl))
        jobs.append(ex.submit(task, nws_marine))
        for n, (la, lo) in NWS_POINTS.items():
            jobs.append(ex.submit(task, nws, n, la, lo))
        for f in as_completed(jobs):
            print(f.result(), flush=True)
