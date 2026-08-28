"""Sequential (one-at-a-time) MUR SST fetch — ERDDAP allows only one concurrent request per client."""
import sys, os, time
sys.path.insert(0, "/home/user/workspace/socal")
from fetch_raw import get, save, ZONES, RAW, TODAY
from datetime import timedelta

END = (TODAY - timedelta(days=2)).isoformat()
CHUNKS = [("2015-01-01", "2020-12-31"), ("2021-01-01", END)]

def zone_chunk(z, a, b):
    name = f"mur_{z['id']}_{a[:4]}_{b[:4]}.csv"
    if os.path.exists(os.path.join(RAW, name)) and os.path.getsize(os.path.join(RAW, name)) > 500:
        return f"cached {name}"
    u = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv?analysed_sst"
         f"%5B({a}T09:00:00Z):1:({b}T09:00:00Z)%5D%5B({z.get('sst_lat', z['lat'])})%5D%5B({z.get('sst_lon', z['lon'])})%5D")
    return save(name, get(u, timeout=280))

def grid(days=6):
    name = "mur_grid_recent.csv"
    a = (TODAY - timedelta(days=days + 2)).isoformat()
    u = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv?analysed_sst"
         f"%5B({a}T09:00:00Z):1:({END}T09:00:00Z)%5D%5B(32.05):(33.25)%5D%5B(-119.35):(-117.1)%5D")
    return save(name, get(u, timeout=500))

def extras():
    out = []
    try:
        u = ("https://oceanview.pfeg.noaa.gov/erddap/tabledap/erdCUTI.csv?"
             "time,latitude,CUTI&time%3E=2015-01-01&latitude%3E=32.4&latitude%3C=33.6")
        out.append(save("cuti_daily.csv", get(u, timeout=280)))
    except Exception as e:
        out.append(f"CUTI FAIL {e}")
    time.sleep(2)
    from fetch_raw import chl
    try:
        out.append(chl())
    except Exception as e:
        out.append(f"CHL FAIL {e}")
    return " | ".join(out)

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "extras":
        print(extras(), flush=True); sys.exit(0)
    if mode == "grid":
        print(grid(int(sys.argv[2]) if len(sys.argv) > 2 else 6), flush=True)
    else:
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
        for z in ZONES[lo:hi]:
            for a, b in CHUNKS:
                try:
                    print(zone_chunk(z, a, b), flush=True)
                except Exception as e:
                    print(f"FAIL {z['id']} {a}: {type(e).__name__} {e}", flush=True)
                time.sleep(1)
