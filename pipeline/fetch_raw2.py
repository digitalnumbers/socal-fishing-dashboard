import json, sys, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/user/workspace/socal")
from fetch_raw import (ZONES, mur_series, mur_grid, cuti, chl, nws, nws_marine, coops,
                       ndbc_realtime, ndbc_hist, TODAY, COOPS, save, get, task)
from datetime import timedelta

def run():
    jobs = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for z in ZONES:
            jobs.append(ex.submit(task, mur_series, z))
        jobs.append(ex.submit(task, mur_grid))
        jobs.append(ex.submit(task, cuti))
        jobs.append(ex.submit(task, chl))
        jobs.append(ex.submit(task, nws_marine))
        jobs.append(ex.submit(task, nws, "offshore_9mile", 32.62, -117.42))
        b = (TODAY - timedelta(days=45)).strftime("%Y%m%d")
        for st in COOPS:
            jobs.append(ex.submit(task, coops, st, "water_temperature", b, TODAY.strftime("%Y%m%d")))
            jobs.append(ex.submit(task, coops, st, "air_pressure", b, TODAY.strftime("%Y%m%d")))
        for f in as_completed(jobs):
            print(f.result(), flush=True)

run()
print("DONE")
