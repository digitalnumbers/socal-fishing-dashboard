"""CO-OPS water temperature + air pressure history in 31-day chunks (API hard limit)."""
import sys, calendar
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/user/workspace/socal")
from fetch_raw import coops, task, COOPS, TODAY

def month_chunk(station, product, yr, mo):
    last = calendar.monthrange(yr, mo)[1]
    if date(yr, mo, 1) > TODAY:
        return "skip future"
    end = min(date(yr, mo, last), TODAY)
    return coops(station, product, f"{yr}{mo:02d}01", end.strftime("%Y%m%d"))

if __name__ == "__main__":
    y0, y1 = int(sys.argv[1]), int(sys.argv[2])
    products = sys.argv[3].split(",")
    jobs, ok, fail = [], 0, 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for st in COOPS:
            for prod in products:
                for y in range(y0, y1 + 1):
                    for m in range(1, 13):
                        jobs.append(ex.submit(task, month_chunk, st, prod, y, m))
        for f in as_completed(jobs):
            r = f.result()
            if isinstance(r, str) and r.startswith("FAIL"):
                fail += 1
            else:
                ok += 1
    print(f"ok={ok} fail={fail}")
