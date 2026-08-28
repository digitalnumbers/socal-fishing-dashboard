"""Fill any MUR zone chunk that timed out, using smaller year-range sub-chunks.

The 2021->present chunk is ~2000 days and sometimes exceeds ERDDAP's patience at 280 s.
Sub-chunks are written under the same naming convention build_dataset.py globs
(mur_<zone>_<startyear>_<endyear>.csv), so no downstream change is needed.
Strictly one request at a time: ERDDAP returns HTTP 408 on concurrent requests per client.
"""
import os, sys, time
sys.path.insert(0, "/home/user/workspace/socal")
from fetch_mur import zone_chunk, CHUNKS, END
from fetch_raw import ZONES, RAW

SUB = [("2021-01-01", "2022-12-31"), ("2023-01-01", "2024-12-31"), ("2025-01-01", END)]


def have(zid, a, b):
    p = os.path.join(RAW, f"mur_{zid}_{a[:4]}_{b[:4]}.csv")
    return os.path.exists(p) and os.path.getsize(p) > 500


def main():
    for z in ZONES:
        if z.get("sst_source") == "shore":
            continue
        for a, b in CHUNKS:
            if have(z["id"], a, b):
                print(f"ok       {z['id']} {a[:4]}_{b[:4]}", flush=True)
                continue
            try:
                print(zone_chunk(z, a, b), flush=True)
                time.sleep(2)
                continue
            except Exception as e:
                print(f"FAIL     {z['id']} {a[:4]}_{b[:4]}: {type(e).__name__} {e}", flush=True)
            if a != "2021-01-01":
                continue
            for sa, sb in SUB:            # split the long recent chunk
                if have(z["id"], sa, sb):
                    print(f"ok  sub  {z['id']} {sa[:4]}_{sb[:4]}", flush=True)
                    continue
                try:
                    print("sub", zone_chunk(z, sa, sb), flush=True)
                except Exception as e2:
                    print(f"FAIL sub {z['id']} {sa[:4]}_{sb[:4]}: {type(e2).__name__} {e2}", flush=True)
                time.sleep(2)
    print("FILLDONE", flush=True)


if __name__ == "__main__":
    main()
