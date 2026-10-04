#!/usr/bin/env python3
"""Observed-outcome store: raw dock-total trips (append-only) and normalized labels.

Layers
------
1. ``data/outcome_raw/dock_trips_YYYY-MM.csv`` - every boat trip exactly as parsed from
   the public San Diego dock-totals page, append-only, one row per distinct version.
   Zero-tracked-catch trips are kept (they are effort).
2. ``data/outcome_raw/fetch_log.csv`` - every page fetch: report date, time, success and
   the ids of the trips present, so the newest complete page can be reconstructed.
3. ``data/outcome_ledger.csv`` - normalized training/evaluation labels per fishing date x
   region x species, regenerated deterministically from layers 1-2 on every run.
4. ``data/label_reference_L1.csv`` - the frozen per-species/region reference catch rate
   that anchors label version L1. Written once, never refitted silently.

Labelling rules (L1)
--------------------
* Trips are attributed to a region from the trip description; ambiguous or out-of-domain
  trips (plain "Full Day", 3+ day long-range) are kept raw but excluded from labels.
* Effort is anglers x trip length (angler-days), split evenly over the days fished.
* A species is only eligible on a trip when its configured zones overlap the trip region,
  so tuna absent from a half-day trip is not a "zero".
* No trips, or too little effort, yields NO label. A Poor label from zero catch exists only
  when eligible effort meets the minimum AND the species has a frozen species-region
  reference (it was demonstrably caught there in the reference window): the
  effort-adjusted negative signal. Species-region pairs without a reference are left
  unlabelled ("no_reference") rather than scored as Poor.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_common import (  # noqa: E402
    CLASSES, LABEL_VERSION, PARSER_VERSION, REGIONS, ROOT, atomic_write, csv_bytes,
    score_class, score_class_index, sha256_bytes, utc_now_iso, parse_ts, GOOD_INDEX,
)
from refresh_support import fetch_bytes, parse_counts, trip_type  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
DOCK_URL = "https://www.sandiegofishreports.com/dock_totals/boats.php"
SOURCE_NAME = "San Diego Fish Reports dock totals"

RAW_COLUMNS = ["raw_id", "report_date", "landing", "boat", "trip_type", "trip_type_raw", "trip_days",
               "anglers", "kept_json", "released_json", "tracked_fish", "raw_text", "source",
               "source_url", "fetched_at_utc", "ingest_method", "parser_version"]
FETCH_COLUMNS = ["report_date", "fetched_at_utc", "ingest_method", "ok", "n_trips", "raw_ids", "error"]
LABEL_COLUMNS = ["label_id", "label_version", "fishing_date", "region_id", "species_id", "n_trips",
                 "anglers", "angler_days", "fish_kept", "fish_released", "fish_total", "catch_index",
                 "reference_index", "index_ratio", "observed_score", "observed_class",
                 "observed_good_or_better", "effort_sufficient", "label_status", "reporting_lag_days",
                 "available_at_utc", "publication_lag_days", "report_dates", "source", "n_raw_records",
                 "dq_flags"]
REF_COLUMNS = ["species_id", "region_id", "reference_index", "n_days", "basis", "window_start",
               "window_end", "label_version", "frozen_utc"]

TRIP_DAYS = {"half_day": 0.5, "three_quarter_day": 0.75, "full_day": 1.0, "twilight": 0.4,
             "overnight": 1.0, "1.5_day": 1.5, "2_day": 2.0, "2.5_day": 2.5, "3_day": 3.0,
             "3.5_day": 3.5, "4_day": 4.0, "5_day": 5.0, "7_day": 7.0}
MIN_TRIPS = 2
MIN_ANGLER_DAYS = 30.0
REFERENCE_WINDOW = ("2026-08-15", "2026-08-27")   # frozen L1 anchor window (pre-evaluation)
DEFAULT_REFERENCE = 0.5
SPARSE_SPECIES = {"white_seabass", "california_halibut"}
# Observed index ratio -> 0-100 observed score; anchored so class boundaries line up with the
# forecast class cut-offs 40/60/75/90.
RATIO_KNOTS = [(0.0, 0.0), (0.25, 40.0), (0.6, 60.0), (1.2, 75.0), (2.0, 90.0), (4.0, 100.0)]


# ---------------------------------------------------------------- parsing
def parse_dock_trips(data: bytes, requested: date, url: str) -> tuple[date, list[dict]]:
    """Parse every boat trip, including trips with no tracked-species catch."""
    soup = BeautifulSoup(data, "html.parser")
    text = soup.get_text(" ", strip=True)
    full = re.search(r"(?:January|February|March|April|May|June|July|August|September|October|"
                     r"November|December)\s+\d{1,2},\s+\d{4}", text)
    if not full:
        raise ValueError("dock page date not found")
    page_date = datetime.strptime(full.group(0), "%B %d, %Y").date()
    if page_date > requested:
        raise ValueError("dock page reported a future date")
    landing_map = {"Fisherman's Landing": "Fishermans", "H&M Landing": "H&M",
                   "Point Loma Sportfishing": "Point Loma", "Seaforth Sportfishing": "Seaforth"}
    trips, landing = [], None
    for node in soup.find_all(["h2", "h3", "table"]):
        if node.name in ("h2", "h3"):
            heading = node.get_text(" ", strip=True)
            landing = next((short for long, short in landing_map.items() if long in heading), landing)
            continue
        if not landing:
            continue
        for tr in node.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3 or cells[0].lower().startswith(("boat", "dock total")):
                continue
            boat, trip, counts = cells[0], cells[1], cells[2]
            am = re.search(r"(\d+)\s*Anglers?", trip, re.I)
            if not am:
                continue
            kept, released = parse_counts(counts)
            raw_trip = re.sub(r"\d+\s*Anglers?", "", trip, flags=re.I).strip()
            boat_name = re.split(r"\s+(?:Fisherman's Landing|H&M Landing|Point Loma Sportfishing|"
                                 r"Seaforth Sportfishing)\b", boat)[0].strip()
            tt = trip_type(raw_trip)
            trips.append({
                "report_date": page_date.isoformat(), "landing": landing, "boat": boat_name,
                "trip_type": tt, "trip_type_raw": raw_trip, "trip_days": TRIP_DAYS.get(tt, 1.0),
                "anglers": int(am.group(1)),
                "kept_json": json.dumps(kept, sort_keys=True), "released_json": json.dumps(released, sort_keys=True),
                "tracked_fish": int(sum(kept.values()) + sum(released.values())),
                "raw_text": f"{boat} | {trip} | {counts}"[:400], "source": SOURCE_NAME, "source_url": url,
            })
    if not trips:
        raise ValueError("dock parser produced zero boat trips")
    return page_date, trips


def raw_id(t: dict) -> str:
    key = "|".join(str(t.get(k, "")) for k in ("report_date", "landing", "boat", "trip_type_raw",
                                                 "anglers", "kept_json", "released_json"))
    return sha256_bytes(key.encode())[:16]


def fetch_dates(dates: list[date], *, method: str, today: date, pause_s: float = 0.0) -> list[dict]:
    """Fetch dock pages for several report dates; each date fails independently."""
    out = []
    for i, d in enumerate(dates):
        if i and pause_s:
            time.sleep(pause_s)
        url = DOCK_URL + "?date=" + d.isoformat()
        fetched = utc_now_iso()
        try:
            blob = fetch_bytes(url)
            page_date, trips = parse_dock_trips(blob, max(d, today), url)
            if page_date != d:
                raise ValueError(f"requested {d} but page reported {page_date}")
            out.append({"report_date": d.isoformat(), "fetched_at_utc": fetched, "ok": True,
                        "trips": trips, "error": None, "method": method})
        except Exception as exc:  # one bad page never blocks the others
            out.append({"report_date": d.isoformat(), "fetched_at_utc": fetched, "ok": False,
                        "trips": [], "error": f"{type(exc).__name__}: {exc}"[:300], "method": method})
    return out


# ---------------------------------------------------------------- raw persistence
def raw_dir(data_root: Path) -> Path:
    return Path(data_root) / "outcome_raw"


def _append_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    if not rows:
        return
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    if not path.exists():
        w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    old = path.read_bytes() if path.exists() else b""
    atomic_write(path, old + buf.getvalue().encode())


def read_raw(data_root: Path) -> pd.DataFrame:
    files = sorted(raw_dir(data_root).glob("dock_trips_*.csv"))
    if not files:
        return pd.DataFrame(columns=RAW_COLUMNS)
    return pd.concat([pd.read_csv(f, dtype={"raw_id": str}) for f in files], ignore_index=True)


def read_fetch_log(data_root: Path) -> pd.DataFrame:
    p = raw_dir(data_root) / "fetch_log.csv"
    if not p.exists():
        return pd.DataFrame(columns=FETCH_COLUMNS)
    return pd.read_csv(p, dtype={"raw_ids": str, "error": str})


def ingest(data_root: Path, fetches: list[dict]) -> dict:
    """Append new trip versions and the fetch log. Never rewrites existing rows."""
    existing = read_raw(data_root)
    seen = set(existing.raw_id.astype(str)) if not existing.empty else set()
    new_rows, log_rows = {}, []
    for f in fetches:
        ids = []
        for t in f["trips"]:
            rid = raw_id(t)
            ids.append(rid)
            if rid in seen or rid in new_rows:
                continue
            new_rows[rid] = {**t, "raw_id": rid, "fetched_at_utc": f["fetched_at_utc"],
                             "ingest_method": f["method"], "parser_version": PARSER_VERSION}
        log_rows.append({"report_date": f["report_date"], "fetched_at_utc": f["fetched_at_utc"],
                         "ingest_method": f["method"], "ok": bool(f["ok"]), "n_trips": len(f["trips"]),
                         "raw_ids": ";".join(ids), "error": f.get("error")})
    by_month: dict[str, list[dict]] = {}
    for r in new_rows.values():
        by_month.setdefault(r["report_date"][:7], []).append(r)
    for month, rows in sorted(by_month.items()):
        rows.sort(key=lambda r: (r["report_date"], r["landing"], r["boat"], r["trip_type_raw"]))
        _append_csv(raw_dir(data_root) / f"dock_trips_{month}.csv", rows, RAW_COLUMNS)
    _append_csv(raw_dir(data_root) / "fetch_log.csv", log_rows, FETCH_COLUMNS)
    return {"pages": len(fetches), "pages_ok": sum(1 for f in fetches if f["ok"]),
            "new_trip_versions": len(new_rows)}


def verify_raw(staged: Path, baseline: Path | None) -> list[str]:
    """Raw files are append-only: each baseline file must be a byte prefix of the staged one."""
    errors = []
    if baseline is None or not raw_dir(baseline).exists():
        return errors
    for old in raw_dir(baseline).glob("*.csv"):
        new = raw_dir(staged) / old.name
        if not new.exists():
            errors.append(f"raw outcome file removed: {old.name}")
            continue
        ob = old.read_bytes()
        if new.read_bytes()[:len(ob)] != ob:
            errors.append(f"raw outcome file rewritten (append-only violated): {old.name}")
    return errors


# ---------------------------------------------------------------- region attribution
def classify_trip(raw_desc, ttype) -> tuple[str | None, str | None]:
    s = str(raw_desc or "").lower()
    tt = str(ttype or "")
    if "coronado" in s:
        return "coronados", None
    days = TRIP_DAYS.get(tt)
    if days is not None and days >= 3.0:
        return None, "out_of_domain_long_range"
    if tt in ("2_day", "2.5_day"):
        return "extended_offshore", None
    if tt == "1.5_day":
        return "offshore_banks", None
    if tt == "overnight":
        return "offshore_banks", "overnight_destination_uncertain"
    if "offshore" in s:
        return "offshore_banks", None
    if tt in ("half_day", "three_quarter_day", "twilight") or "local" in s:
        return "kelp_nearshore", None
    if tt == "full_day":
        return None, "ambiguous_full_day"
    return None, "unknown_trip_type"


def fishing_days(report_date: date, trip_days: float) -> list[date]:
    n = 1 if trip_days <= 1.5 else int(math.floor(trip_days))
    return [report_date - timedelta(days=k) for k in range(n - 1, -1, -1)]


def ratio_to_score(r: float) -> float:
    if r <= 0:
        return 0.0
    for (x0, y0), (x1, y1) in zip(RATIO_KNOTS, RATIO_KNOTS[1:]):
        if r <= x1:
            return round(y0 + (y1 - y0) * (r - x0) / (x1 - x0), 1)
    return 100.0


def species_regions(species_cfg: pd.DataFrame) -> dict[str, set[str]]:
    out = {}
    for r in species_cfg.itertuples():
        zones = set(str(r.zones).split(","))
        out[r.species_id] = {rid for rid, reg in REGIONS.items() if reg["labelled"] and zones & set(reg["zones"])}
    return out


def current_trips(data_root: Path) -> pd.DataFrame:
    """Trips on the newest successful fetch of each report date (supersedes older versions)."""
    raw = read_raw(data_root)
    log = read_fetch_log(data_root)
    if raw.empty or log.empty:
        return raw.iloc[0:0]
    ok = log[log.ok.astype(str).str.lower() == "true"].sort_values("fetched_at_utc")
    latest = ok.groupby("report_date").tail(1)
    rows = []
    raw_idx = raw.drop_duplicates("raw_id").set_index("raw_id")
    for f in latest.itertuples():
        ids = [i for i in str(f.raw_ids or "").split(";") if i]
        sub = raw_idx.loc[[i for i in ids if i in raw_idx.index]].reset_index()
        sub["page_fetched_at_utc"] = f.fetched_at_utc
        sub["page_method"] = f.ingest_method
        rows.append(sub)
    return pd.concat(rows, ignore_index=True) if rows else raw.iloc[0:0]


def _aggregate(trips: pd.DataFrame, sp_regions: dict) -> pd.DataFrame:
    recs = []
    for t in trips.itertuples():
        region, flag = classify_trip(t.trip_type_raw, t.trip_type)
        if region is None:
            continue
        kept = json.loads(t.kept_json) if isinstance(t.kept_json, str) else {}
        rel = json.loads(t.released_json) if isinstance(t.released_json, str) else {}
        rd = date.fromisoformat(str(t.report_date))
        fdays = fishing_days(rd, float(t.trip_days))
        share = 1.0 / len(fdays)
        for sp, regs in sp_regions.items():
            if region not in regs:
                continue
            for fd in fdays:
                recs.append({"fishing_date": fd.isoformat(), "region_id": region, "species_id": sp,
                             "trip_key": t.raw_id, "share": share, "anglers": t.anglers * share,
                             "angler_days": t.anglers * float(t.trip_days) * share,
                             "kept": kept.get(sp, 0) * share, "released": rel.get(sp, 0) * share,
                             "report_date": rd.isoformat(), "lag": (rd - fd).days,
                             "fetched": t.page_fetched_at_utc, "method": t.page_method,
                             "multi_day": len(fdays) > 1, "flag": flag})
    return pd.DataFrame(recs)


def build_reference(agg: pd.DataFrame) -> pd.DataFrame:
    lo, hi = REFERENCE_WINDOW
    daily = _daily(agg)
    win = daily[(daily.fishing_date >= lo) & (daily.fishing_date <= hi) & daily.effort_sufficient]
    out, frozen = [], utc_now_iso()
    sp_med = win.groupby("species_id").catch_index.median()
    for (sp, reg), g in daily.groupby(["species_id", "region_id"]):
        w = win[(win.species_id == sp) & (win.region_id == reg)]
        med = w.catch_index.median() if len(w) else float("nan")
        if len(w) >= 5 and med > 0:
            ref, n, basis = med, len(w), "species-region median"
        elif sp in sp_med and sp_med[sp] > 0:
            ref, n, basis = sp_med[sp], int((win.species_id == sp).sum()), "species median (region fallback)"
        else:
            ref, n, basis = DEFAULT_REFERENCE, 0, "default (no reference data)"
        out.append({"species_id": sp, "region_id": reg, "reference_index": round(float(ref), 4), "n_days": int(n),
                    "basis": basis, "window_start": lo, "window_end": hi, "label_version": LABEL_VERSION,
                    "frozen_utc": frozen})
    return pd.DataFrame(out, columns=REF_COLUMNS)


def _daily(agg: pd.DataFrame) -> pd.DataFrame:
    if agg.empty:
        return pd.DataFrame(columns=["fishing_date", "region_id", "species_id", "catch_index", "effort_sufficient"])
    g = agg.groupby(["fishing_date", "region_id", "species_id"])
    d = g.agg(n_trips=("trip_key", "nunique"), anglers=("anglers", "sum"), angler_days=("angler_days", "sum"),
              fish_kept=("kept", "sum"), fish_released=("released", "sum"), lag=("lag", "max"),
              available=("fetched", "max"), n_raw=("trip_key", "size"),
              report_dates=("report_date", lambda s: ";".join(sorted(set(s)))),
              methods=("method", lambda s: ";".join(sorted(set(map(str, s))))),
              multi=("multi_day", "any"),
              flags=("flag", lambda s: ";".join(sorted({x for x in s if isinstance(x, str)})))).reset_index()
    d["fish_total"] = d.fish_kept + d.fish_released
    d["catch_index"] = (d.fish_total / d.angler_days).where(d.angler_days > 0)
    d["effort_sufficient"] = (d.n_trips >= MIN_TRIPS) & (d.angler_days >= MIN_ANGLER_DAYS)
    return d


def build_labels(data_root: Path, species_cfg: pd.DataFrame, asof_local: date) -> pd.DataFrame:
    trips = current_trips(data_root)
    sp_regions = species_regions(species_cfg)
    agg = _aggregate(trips, sp_regions)
    ref_path = Path(data_root) / "label_reference_L1.csv"
    if ref_path.exists():
        ref = pd.read_csv(ref_path)
    else:
        ref = build_reference(agg)
        if ref.empty:
            return pd.DataFrame(columns=LABEL_COLUMNS)
        atomic_write(ref_path, ref.to_csv(index=False, lineterminator="\n").encode())
    refidx = {(r.species_id, r.region_id): (r.reference_index, r.basis) for r in ref.itertuples()}
    d = _daily(agg)
    log = read_fetch_log(data_root)
    ok = log[log.ok.astype(str).str.lower() == "true"]
    # A report date counts as "settled" once a successful fetch happened on a later local day.
    settled = set()
    for f in ok.itertuples():
        ft = parse_ts(f.fetched_at_utc)
        if ft and ft.astimezone(PT).date() > date.fromisoformat(str(f.report_date)):
            settled.add(str(f.report_date))
    out = []
    for r in d.itertuples():
        fd = date.fromisoformat(r.fishing_date)
        if fd > asof_local:
            continue
        ref_v, ref_basis = refidx.get((r.species_id, r.region_id), (DEFAULT_REFERENCE, "default (no reference data)"))
        flags = [x for x in str(r.flags).split(";") if x]
        if "backfill" in str(r.methods):
            flags.append("backfilled")
        if r.multi:
            flags.append("multi_day_split")
        if "fallback" in ref_basis:
            flags.append("reference_species_fallback")
        elif ref_basis.startswith("default"):
            flags.append("reference_default")
        if r.species_id in SPARSE_SPECIES:
            flags.append("sparse_species")
        if r.effort_sufficient and r.angler_days < 100:
            flags.append("low_effort")
        if fd.isoformat() >= REFERENCE_WINDOW[0] and fd.isoformat() <= REFERENCE_WINDOW[1]:
            flags.append("in_reference_window")
        has_ref = ref_basis == "species-region median"
        if r.effort_sufficient and not has_ref:
            # The species is not demonstrably targeted/caught in this region in the frozen
            # reference window, so zero catch here is NOT an effort-adjusted negative signal.
            ratio, obs, cls, status = None, None, None, "no_reference"
        elif r.effort_sufficient:
            ratio = float(r.catch_index) / float(ref_v) if ref_v else float("nan")
            obs = ratio_to_score(ratio)
            cls = score_class(obs)
            if r.fish_total == 0:
                flags.append("effort_adjusted_zero")
            final = (asof_local - fd).days >= 3 and all(x in settled for x in r.report_dates.split(";"))
            status = "final" if final else "provisional"
        else:
            ratio, obs, cls, status = None, None, None, "insufficient_effort"
        avail = parse_ts(r.available)
        pub_lag = (avail.astimezone(PT).date() - fd).days if avail else None
        out.append({
            "label_id": f"{LABEL_VERSION}|{r.fishing_date}|{r.region_id}|{r.species_id}",
            "label_version": LABEL_VERSION, "fishing_date": r.fishing_date, "region_id": r.region_id,
            "species_id": r.species_id, "n_trips": int(r.n_trips), "anglers": round(r.anglers, 1),
            "angler_days": round(r.angler_days, 1), "fish_kept": round(r.fish_kept, 1),
            "fish_released": round(r.fish_released, 1), "fish_total": round(r.fish_total, 1),
            "catch_index": None if pd.isna(r.catch_index) else round(float(r.catch_index), 4),
            "reference_index": ref_v, "index_ratio": None if ratio is None else round(ratio, 3),
            "observed_score": obs, "observed_class": cls,
            "observed_good_or_better": None if cls is None else int(score_class_index(obs) >= GOOD_INDEX),
            "effort_sufficient": bool(r.effort_sufficient), "label_status": status,
            "reporting_lag_days": int(r.lag), "available_at_utc": r.available,
            "publication_lag_days": pub_lag, "report_dates": r.report_dates, "source": SOURCE_NAME,
            "n_raw_records": int(r.n_raw), "dq_flags": ";".join(dict.fromkeys(flags)) or None,
        })
    out.sort(key=lambda x: (x["fishing_date"], x["region_id"], x["species_id"]))
    return pd.DataFrame(out, columns=LABEL_COLUMNS)


def write_labels(data_root: Path, labels: pd.DataFrame) -> None:
    atomic_write(Path(data_root) / "outcome_ledger.csv",
                 csv_bytes(labels.astype(object).where(labels.notna(), None).to_dict("records"), LABEL_COLUMNS))


# ---------------------------------------------------------------- CLI (manual backfill)
def main() -> int:
    ap = argparse.ArgumentParser(description="Manual, rate-limited dock-total backfill into the raw outcome store")
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between page requests (be polite)")
    ap.add_argument("--max-days", type=int, default=60)
    a = ap.parse_args()
    days = [(a.start + timedelta(days=i)) for i in range((a.end - a.start).days + 1)]
    if len(days) > a.max_days:
        ap.error(f"{len(days)} days requested; raise --max-days deliberately for larger backfills")
    today = datetime.now(PT).date()
    fetches = fetch_dates(days, method="backfill", today=today, pause_s=a.pause)
    res = ingest(a.data, fetches)
    labels = build_labels(a.data, pd.read_csv(ROOT / "dataset/csv/dim_species.csv"), today)
    write_labels(a.data, labels)
    res["labels"] = int(labels.observed_class.notna().sum())
    res["failed_dates"] = [f["report_date"] for f in fetches if not f["ok"]]
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
