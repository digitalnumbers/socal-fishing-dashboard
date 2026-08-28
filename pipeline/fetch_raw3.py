"""Long-history in-situ SST/met acquisition (NDBC archives + CO-OPS water temp) — ERDDAP-independent."""
import sys, json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/user/workspace/socal")
from fetch_raw import ndbc_hist, coops, save, get, task, TODAY, COOPS

STATIONS = {"46232": range(2004, 2026), "46258": range(2004, 2026),
            "46086": range(2003, 2026), "46225": range(2004, 2026)}

def coops_year(station, product, yr):
    b, e = f"{yr}0101", f"{yr}1231"
    return coops(station, product, b, e)

if __name__ == "__main__":
    which = sys.argv[1]
    jobs = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        if which == "ndbc":
            for s, yrs in STATIONS.items():
                for y in yrs:
                    if y >= 2019 and s != "46225":
                        continue
                    jobs.append(ex.submit(task, ndbc_hist, s, y))
        elif which == "coops":
            for st in COOPS:
                for y in range(2000, 2027):
                    jobs.append(ex.submit(task, coops_year, st, "water_temperature", y))
        for f in as_completed(jobs):
            r = f.result()
            if isinstance(r, str) and r.startswith("FAIL"):
                print(r, flush=True)
    print("DONE", which)
