#!/usr/bin/env python3
"""Transactional, idempotent daily refresh for the SoCal Fishing Dashboard."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from refresh_support import (
    PT, atomic_write, coops_latest, csv_latest_time, due, fetch_bytes,
    forecast_coverage, ndbc_latest, now_pair, parse_dock_html, safe_extract_zip,
    status_record, valid_json,
)

ROOT = Path(__file__).resolve().parents[1]
ZONES = json.loads((ROOT / "pipeline/config/zones.json").read_text())
NDBC = sorted({str(s) for z in ZONES for s in z["buoys"]} | {"LJAC1"})
COOPS = ["9410170", "9410230"]
STATE_PATH = ROOT / ".refresh-cache/daily_state.json"
DOCK_URL = "https://www.sandiegofishreports.com/dock_totals/boats.php"
ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv"


def committed_snapshot_date(root: Path) -> date:
    """Infer the declared build date from the 14-day hindcast + 7-day table."""
    path = root / "dataset/csv/conditions_daily.csv"
    values = sorted(pd.to_datetime(pd.read_csv(path, usecols=["date"])["date"]).dt.date.unique())
    if len(values) < 7:
        raise ValueError("conditions_daily.csv cannot identify the committed snapshot date")
    return values[-7]


class Refresh:
    def __init__(self, args):
        self.args = args
        self.utc, self.local = now_pair()
        self.local_dt = datetime.fromisoformat(self.local)
        if args.today:
            self.today = date.fromisoformat(args.today)
        elif args.dry_run:
            self.today = committed_snapshot_date(ROOT)
        else:
            self.today = self.local_dt.date()
        self.previous = {}
        previous_status = ROOT / "dataset/csv/source_status.json"
        if previous_status.exists():
            try:
                self.previous = {r["source_identifier"]: r for r in json.loads(previous_status.read_text()).get("sources", [])}
            except Exception:
                pass
        self.status: list[dict] = []
        self.failures: list[str] = []
        self.stage = Path(tempfile.mkdtemp(prefix=".daily-build-", dir=ROOT))
        self.raw = self.stage / ".refresh-cache/daily"
        self.ext = self.stage / ".refresh-cache/extended"

    def prepare(self) -> None:
        for rel in ("dataset", "app", "docs", "pipeline/config", "data"):
            src, dst = ROOT / rel, self.stage / rel
            if src.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
        cache = ROOT / ".refresh-cache"
        if cache.exists():
            shutil.copytree(cache, self.stage / ".refresh-cache", dirs_exist_ok=True)
        self.raw.mkdir(parents=True, exist_ok=True)
        self.ext.mkdir(parents=True, exist_ok=True)
        (self.stage / "build-reports").mkdir(exist_ok=True)

    def ensure_staged_run_meta(self) -> None:
        """Supply the legacy site builder's metadata contract from committed tables."""
        target = self.stage / "dataset/csv/run_meta.json"
        if target.exists():
            return
        csv_dir = target.parent

        def span(name):
            path = csv_dir / name
            if not path.exists():
                return None
            frame = pd.read_csv(path, usecols=["date"])
            years = pd.to_datetime(frame["date"], errors="coerce").dropna().dt.year
            return f"{int(years.min())}–{int(years.max())}" if not years.empty else None

        scores = pd.read_csv(csv_dir / "scores_daily.csv")
        meta = {
            "run_utc": self.utc,
            "zones": len(ZONES),
            "species": int(scores["species_id"].nunique()),
            "score_rows": int(len(scores)),
            "baseline": span("mur_history.csv") or "2015–present",
            "baseline_satellite": span("mur_history.csv"),
            "baseline_shore": span("shore_sst_daily.csv"),
            "baseline_buoy": span("buoy_daily.csv"),
        }
        atomic_write(target, (json.dumps(meta, indent=2) + "\n").encode())

    def record(self, sid, display, category, cadence, *, success, newest=None,
               freshness="fresh", cached=False, error=None, note="",
               deployment_critical=False) -> None:
        row = status_record(
            sid, display, category, cadence, self.utc, self.local, self.previous.get(sid),
            success=success, newest=newest, freshness=freshness, cached=cached, error=error,
            note=note, deployment_critical=deployment_critical,
        )
        if freshness == "not_due":
            previous = self.previous.get(sid) or {}
            row["last_attempted_fetch_utc"] = previous.get("last_attempted_fetch_utc")
            row["last_attempted_fetch_local"] = previous.get("last_attempted_fetch_local")
        self.status.append(row)

    def fetch_one(self, sid, display, category, cadence, url, path, validator,
                  note="", critical=False) -> None:
        try:
            blob = fetch_bytes(url)
            newest, freshness = validator(blob)
            atomic_write(path, blob)
            self.record(sid, display, category, cadence, success=True, newest=newest,
                        freshness=freshness, note=note)
        except Exception as exc:
            cached = path.exists() and path.stat().st_size > 0
            self.record(sid, display, category, cadence, success=False, freshness="failed",
                        cached=cached, error=exc, note=note, deployment_critical=critical)
            if critical and not cached:
                self.failures.append(f"{sid}: {exc}")

    def fetch_ndbc(self) -> None:
        def one(st):
            path = self.raw / f"ndbc_rt_{st}.txt"
            url = f"https://www.ndbc.noaa.gov/data/realtime2/{st}.txt"
            def check(blob):
                ts = ndbc_latest(blob)
                age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                state = "fresh" if age <= 3 else ("delayed" if age <= 12 else "stale")
                return ts.isoformat(), state
            self.fetch_one(f"ndbc:{st}", f"NDBC {st}", "realtime", "10-60 minutes",
                           url, path, check, note="Station delays and missing fields are normal; last valid history is retained.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, NDBC))

    def coops_url(self, product, station, begin, end, interval=None):
        params = {
            "product": product, "application": "socal_fishing_dashboard",
            "begin_date": begin.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
            "station": station, "time_zone": "lst_ldt", "units": "english", "format": "json",
        }
        if product == "predictions":
            params.update(datum="MLLW", interval=interval or "hilo")
        return "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?" + urllib.parse.urlencode(params)

    def fetch_coops(self) -> None:
        begin, end = self.today - timedelta(days=7), self.today
        jobs = []
        for station in COOPS:
            for product in ("water_temperature", "air_pressure"):
                path = self.raw / f"coops_{station}_{product}_recent.json"
                jobs.append((station, product, path))
        def one(item):
            station, product, path = item
            def check(blob):
                obj = valid_json(blob, "data")
                ts = coops_latest(obj, "data")
                age = (datetime.now(PT) - ts).total_seconds() / 3600
                return ts.isoformat(), "fresh" if age <= 3 else ("delayed" if age <= 12 else "stale")
            self.fetch_one(f"coops:{station}:{product}", f"CO-OPS {station} {product}",
                           "realtime", "6 minutes", self.coops_url(product, station, begin, end),
                           path, check, note="Seven-day overlap captures late corrections; station/time keys are deduplicated.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, jobs))

        tide_end = self.today + timedelta(days=40)
        for station in COOPS:
            url = self.coops_url("predictions", station, self.today, tide_end, "hilo")
            paths = [
                self.raw / f"coops_{station}_predictions_{self.today:%Y%m%d}_{tide_end:%Y%m%d}.json",
                self.ext / f"coops_{station}_predictions_ext.json",
            ]
            try:
                blob = fetch_bytes(url)
                obj = valid_json(blob, "predictions")
                latest = coops_latest(obj, "predictions")
                if latest.date() < self.today + timedelta(days=30):
                    raise ValueError("prediction horizon is shorter than 30 days")
                for path in paths:
                    atomic_write(path, blob)
                self.record(f"coops:{station}:predictions", f"CO-OPS {station} harmonic tides",
                            "daily", "daily refresh / deterministic harmonic prediction",
                            success=True, newest=latest.isoformat(), freshness="fresh",
                            note="Astronomical harmonic prediction, not an observation; excludes weather-driven surge.")
            except Exception as exc:
                cached = paths[1].exists()
                still_valid = False
                if cached:
                    try:
                        still_valid = coops_latest(valid_json(paths[1].read_bytes(), "predictions"), "predictions").date() >= self.today + timedelta(days=30)
                        if still_valid:
                            atomic_write(paths[0], paths[1].read_bytes())
                    except Exception:
                        pass
                self.record(f"coops:{station}:predictions", f"CO-OPS {station} harmonic tides",
                            "daily", "daily refresh / deterministic harmonic prediction",
                            success=False, freshness="cached" if still_valid else "failed",
                            cached=still_valid, error=exc,
                            note="Astronomical harmonic prediction reused only while its horizon remains valid.")
                if not still_valid:
                    self.failures.append(f"CO-OPS tide horizon {station}: {exc}")

    def fetch_mur(self) -> None:
        start = self.today - timedelta(days=self.args.mur_overlap_days)
        newest_all = None
        ok = 0
        errors = []
        for z in ZONES:  # Intentionally sequential: ERDDAP limits one request per client.
            zid, lat, lon = z["id"], z["sst_lat"], z["sst_lon"]
            path = self.raw / f"mur_{zid}_{start:%Y%m%d}_{self.today:%Y%m%d}.csv"
            success = False
            for end in (self.today - timedelta(days=i) for i in range(0, 5)):
                query = (f"analysed_sst[({start}T09:00:00Z):1:({end}T09:00:00Z)]"
                         f"[({lat}):1:({lat})][({lon}):1:({lon})]")
                url = ERDDAP + "?" + urllib.parse.quote(query, safe="[]():,.-")
                try:
                    blob = fetch_bytes(url, timeout=120, attempts=2)
                    newest, _ = csv_latest_time(blob)
                    atomic_write(path, blob)
                    parsed = datetime.fromisoformat(newest.replace("Z", "+00:00")).date()
                    newest_all = parsed if newest_all is None else min(newest_all, parsed)
                    ok += 1
                    success = True
                    break
                except Exception as exc:
                    errors.append(f"{zid}: {exc}")
            if not success:
                continue
        if ok == len(ZONES):
            age = (self.today - newest_all).days if newest_all else 99
            freshness = "fresh" if age <= 1 else ("delayed" if age <= 3 else "stale")
            self.record("mur_sst", "NASA JPL MUR SST via CoastWatch ERDDAP", "daily", "daily analyzed field",
                        success=True, newest=newest_all.isoformat(), freshness=freshness,
                        note=f"Nine zone points fetched sequentially with a {self.args.mur_overlap_days}-day overlap; latest actually available field is reported.")
        else:
            baseline = ROOT / "dataset/csv/mur_history.csv"
            cached = baseline.exists() and baseline.stat().st_size > 1000
            self.record("mur_sst", "NASA JPL MUR SST via CoastWatch ERDDAP", "daily", "daily analyzed field",
                        success=False, freshness="failed", cached=cached,
                        error="; ".join(errors[-3:]) or f"only {ok}/9 zone requests succeeded",
                        note="MUR can publish after the morning run; the latest valid prior field is retained.")
            if not cached:
                self.failures.append("MUR failed and no historical fallback exists")

    def fetch_forecasts(self) -> None:
        marine = ("https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
                  "&daily=wave_height_max,wave_period_max,wave_direction_dominant,swell_wave_height_max,"
                  "swell_wave_period_max,sea_surface_temperature_max,sea_surface_temperature_min"
                  "&hourly=sea_surface_temperature,wave_height&timezone=America%2FLos_Angeles"
                  "&forecast_days=7&past_days=14")
        weather = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                   "&daily=wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,"
                   "pressure_msl_mean,cloud_cover_mean,temperature_2m_max,precipitation_sum"
                   "&hourly=wind_speed_10m,pressure_msl&timezone=America%2FLos_Angeles"
                   "&forecast_days=7&past_days=14&wind_speed_unit=kn")
        def zone(z):
            for kind, template in (("marine", marine), ("weather", weather)):
                path = self.raw / f"om_{'marine' if kind == 'marine' else 'wx'}_{z['id']}.json"
                def check(blob):
                    first, last = forecast_coverage(valid_json(blob))
                    if last < self.today + timedelta(days=6):
                        raise ValueError(f"coverage ends {last}")
                    return last.isoformat(), "fresh"
                self.fetch_one(f"openmeteo:{z['id']}:{kind}", f"Open-Meteo {kind} {z['name']}",
                               "daily", "multiple model runs daily", template.format(**z), path,
                               check, note="Bounded 14-day hindcast plus 7-day forecast.", critical=True)
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(zone, ZONES))

        # NWS retired the former PZZ7xx zone endpoints used by the snapshot scripts.
        # The official SGX Coastal Waters Forecast product covers current zones PZZ740
        # and PZZ745 and is a more stable machine-readable discovery route.
        path = self.raw / "nws_marine_SGX_CWF.json"
        sid = "nws:SGX:CWF"
        try:
            index = valid_json(fetch_bytes("https://api.weather.gov/products/types/CWF/locations/SGX"))
            products = index.get("@graph") or []
            if not products or not products[0].get("@id"):
                raise ValueError("NWS CWF index has no current product")
            obj = valid_json(fetch_bytes(products[0]["@id"]))
            text = obj.get("productText") or ""
            issued = obj.get("issuanceTime")
            if len(text) < 500 or not issued or not all(z in text for z in ("PZZ740", "PZZ745")):
                raise ValueError("NWS CWF product is incomplete or missing current marine zones")
            issued_dt = datetime.fromisoformat(issued.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - issued_dt.astimezone(timezone.utc)).total_seconds() / 3600
            freshness = "fresh" if age <= 12 else ("delayed" if age <= 24 else "stale")
            atomic_write(path, json.dumps(obj, separators=(",", ":")).encode())
            self.record(sid, "NWS San Diego Coastal Waters Forecast", "daily",
                        "multiple updates daily", success=True, newest=issued,
                        freshness=freshness,
                        note="Official SGX narrative for PZZ740/PZZ745; used as a cross-check only. "
                             "Degenerate coastal land-grid wave values are never treated as marine waves.")
        except Exception as exc:
            cached = path.exists() and path.stat().st_size > 500
            self.record(sid, "NWS San Diego Coastal Waters Forecast", "daily",
                        "multiple updates daily", success=False, freshness="failed",
                        cached=cached, error=exc,
                        note="Last-known-good NWS narrative is retained; Open-Meteo remains the bounded "
                             "numerical marine forecast. Degenerate land-grid waves are never used.")

    def fetch_extended_forecast(self) -> None:
        marine = ("https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
                  "&daily=wave_height_max,wave_period_max,swell_wave_height_max,wave_direction_dominant"
                  "&forecast_days=16&timezone=America%2FLos_Angeles")
        weather = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                   "&daily=wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,"
                   "pressure_msl_mean,cloud_cover_mean,temperature_2m_max&forecast_days=16"
                   "&timezone=America%2FLos_Angeles")
        output = {}
        failures = []
        newest = None
        for z in ZONES:
            rec = {}
            for kind, template in (("marine", marine), ("weather", weather)):
                try:
                    obj = valid_json(fetch_bytes(template.format(**z)))
                    _, last = forecast_coverage(obj)
                    rec[kind] = obj
                    newest = max(newest, last) if newest else last
                except Exception as exc:
                    failures.append(f"{z['id']} {kind}: {exc}")
            output[z["id"]] = rec
        path = self.ext / "openmeteo_16day.json"
        if not failures:
            atomic_write(path, json.dumps(output, separators=(",", ":")).encode())
            self.record("openmeteo:extended", "Open-Meteo 16-day model", "daily", "multiple model runs daily",
                        success=True, newest=newest.isoformat(), freshness="fresh",
                        note="Deterministic wind and swell are used only through day 14; days 15-30 remain null by design.")
        else:
            cached = path.exists()
            self.record("openmeteo:extended", "Open-Meteo 16-day model", "daily", "multiple model runs daily",
                        success=False, freshness="failed", cached=cached, error="; ".join(failures[-3:]),
                        note="Deterministic fields are never extrapolated into days 15-30.")
            if not cached:
                self.failures.append("extended Open-Meteo failed without cache")

    def fetch_periodic(self) -> None:
        products = [
            ("cpc_oni", "CPC ONI", "monthly", "monthly", "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
             self.raw / "cpc_oni.ascii.txt", "Monthly observed 3-month index; may lag current conditions."),
            ("cpc_nino34", "CPC weekly Niño 3.4", "weekly", "weekly", "https://www.cpc.ncep.noaa.gov/data/indices/wksst8110.for",
             self.raw / "cpc_nino34_weekly.txt", "Weekly context only; not used directly in scoring."),
        ]
        for sid, display, category, cadence, url, path, note in products:
            prev = self.previous.get(sid)
            if not due(prev, category, self.today, self.args.full_rebuild):
                self.record(sid, display, category, cadence, success=False, freshness="not_due",
                            cached=path.exists(), note=note)
                continue
            def check(blob):
                text = blob.decode("utf-8", "replace")
                if len(text.splitlines()) < 10:
                    raise ValueError("response is unexpectedly short")
                return self.today.isoformat(), "fresh"
            self.fetch_one(sid, display, category, cadence, url, path, check, note=note,
                           critical=(sid == "cpc_oni"))

        cpc = [
            ("814temp", "daily"), ("814prcp", "daily"),
            ("wk34temp", "weekly"), ("wk34prcp", "weekly"),
            ("seastemp", "monthly"), ("seasprcp", "monthly"),
        ]
        for product, category in cpc:
            sid = f"cpc:{product}"
            dest = self.ext / "cpc" / product
            if not due(self.previous.get(sid), category, self.today, self.args.full_rebuild):
                self.record(sid, f"CPC {product}", category, category, success=False,
                            freshness="not_due", cached=dest.exists(),
                            note="Land-based CPC outlook is regional context, not a direct offshore SST forecast.")
                continue
            found = None
            if category == "monthly":
                for back in range(4):
                    d = (self.today.replace(day=1) - timedelta(days=back * 28)).replace(day=1)
                    name = f"{product}_{d:%Y%m}.zip"
                    try:
                        found = name, fetch_bytes(f"https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/{name}")
                        break
                    except Exception:
                        pass
            else:
                for back in range(9):
                    d = self.today - timedelta(days=back)
                    name = f"{product}_{d:%Y%m%d}.zip"
                    try:
                        found = name, fetch_bytes(f"https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/{name}")
                        break
                    except Exception:
                        pass
            if found:
                try:
                    safe_extract_zip(found[1], dest)
                    atomic_write(dest / "_source.txt",
                                 f"https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/{found[0]}\n".encode())
                    self.record(sid, f"CPC {product}", category, category, success=True,
                                newest=found[0], freshness="fresh",
                                note="Land-based CPC outlook is regional context, not a direct offshore SST forecast.")
                except Exception as exc:
                    self.record(sid, f"CPC {product}", category, category, success=False,
                                freshness="failed", cached=dest.exists(), error=exc)
            else:
                self.record(sid, f"CPC {product}", category, category, success=False,
                            freshness="failed", cached=dest.exists(), error="no recent archive found")

    def fetch_dock(self) -> None:
        path = self.raw / "catch_reports.json"
        try:
            url = DOCK_URL + "?" + urllib.parse.urlencode({"date": self.today.isoformat()})
            blob = fetch_bytes(url)
            page_date, new = parse_dock_html(blob, self.today, url)
            doc = {
                "collected_at_utc": self.utc,
                "sources": [{"url": url, "name": "San Diego Fish Reports dock totals",
                             "fetched_ok": True, "notes": f"parsed as report date {page_date}"}],
                "reports": new,
                "coverage_notes": "This raw cache contains only the successfully parsed report date. "
                                  "The builder replaces that date and preserves all prior canonical rows.",
            }
            atomic_write(path, json.dumps(doc, indent=2).encode())
            lag = (self.today - page_date).days
            self.record("dock_totals", "San Diego dock totals", "daily", "daily",
                        success=True, newest=page_date.isoformat(),
                        freshness="fresh" if lag == 0 else ("delayed" if lag <= 1 else "stale"),
                        note="Third-party HTML; parser must yield valid boat reports before last-known-good records are merged.")
        except Exception as exc:
            cached = path.exists() or (ROOT / "dataset/csv/catch_reports.csv").exists()
            self.record("dock_totals", "San Diego dock totals", "daily", "daily",
                        success=False, freshness="failed", cached=cached, error=exc,
                        note="Parser/page changes never clear or truncate prior catch history; non-commercial attribution applies.")

    def fetch_dock_outcomes(self) -> None:
        """Re-read the last few report dates (late boat updates) for the outcome ledger.

        Non-critical: failures only delay outcome labels and never block the refresh."""
        from outcome_ledger import fetch_dates
        dates = [self.today - timedelta(days=k) for k in range(3, -1, -1)]
        fetches = fetch_dates(dates, method="daily_refresh", today=self.today, pause_s=1.0)
        doc = {"collected_at_utc": self.utc, "fetches": fetches}
        atomic_write(self.raw / "dock_trips.json", json.dumps(doc, indent=1).encode())
        ok = [f for f in fetches if f["ok"]]
        self.record("dock_outcomes", "Dock totals for outcome ledger (today-3..today)", "daily", "daily",
                    success=bool(ok), newest=max((f["report_date"] for f in ok), default=None),
                    freshness="fresh" if ok else "failed", cached=bool(ok),
                    error=None if ok else "; ".join(f["error"] or "" for f in fetches)[:300],
                    note="Raw trips are appended to data/outcome_raw; labels are derived separately.")

    def learning(self, mode: str) -> None:
        self.run_cmd([sys.executable, "pipeline/learning_step.py", "--root", str(self.stage),
                      "--mode", mode, "--today", self.today.isoformat()])

    def inject_learning(self, env) -> None:
        self.run_cmd([sys.executable, "pipeline/inject_learning.py", "--repo", str(self.stage)], env)

    def write_status(self, completed=False) -> None:
        counts = {}
        for row in self.status:
            counts[row["freshness"]] = counts.get(row["freshness"], 0) + 1
        status = {
            "schema_version": 1,
            "operational_timezone": "America/Los_Angeles",
            "build_started_at_utc": self.utc,
            "build_started_at_local": self.local,
            "build_local_date": self.today.isoformat(),
            "build_completed_at_utc": now_pair()[0] if completed else None,
            "data_cutoff_policy": "Use newest valid source data available when the build starts; retain validated last-known-good data for noncritical delayed feeds.",
            "summary": counts,
            "sources": sorted(self.status, key=lambda r: r["source_identifier"]),
            "freshness_thresholds": {
                "NDBC": "fresh <=3h; delayed <=12h; otherwise stale",
                "CO-OPS observations": "fresh <=3h; delayed <=12h; otherwise stale",
                "MUR": "fresh latest field <=1 local day old; delayed <=3 days; otherwise stale",
                "Open-Meteo/NWS": "fresh only when required forecast horizon parses and is covered",
                "tides": "usable only when harmonic prediction horizon covers at least 30 days",
                "dock totals": "fresh same report date; delayed one day; stale thereafter",
                "CPC/ENSO": "daily, weekly, or monthly checks according to product cadence",
            },
        }
        atomic_write(self.stage / "dataset/csv/source_status.json", (json.dumps(status, indent=2) + "\n").encode())

    def run_cmd(self, command, env=None) -> None:
        print("+", " ".join(map(str, command)), flush=True)
        subprocess.run(command, cwd=ROOT, env={**os.environ, **(env or {})}, check=True)

    def build(self) -> None:
        csv_dir = self.stage / "dataset/csv"
        env = {
            "SOCAL_ROOT": str(ROOT), "SOCAL_RAW": str(self.raw), "SOCAL_RAW_EXT": str(self.ext),
            "SOCAL_OUT": str(csv_dir), "SOCAL_BASELINE_CSV": str(ROOT / "dataset/csv"),
            "SOCAL_DOCS": str(self.stage / "docs"), "SOCAL_SITE": str(self.stage / "app"),
            "SOCAL_DIST": str(self.stage / "dataset"),
            "SOCAL_XLSX": str(self.stage / "dataset/socal_fishing_dataset.xlsx"),
            "TZ": "America/Los_Angeles",
        }
        self.run_cmd([sys.executable, "pipeline/build_dataset.py"], env)
        self.run_cmd([sys.executable, "pipeline/build_extended.py", "--raw", str(self.ext),
                      "--csv", str(csv_dir), "--today", self.today.isoformat()], env)
        self.run_cmd([sys.executable, "pipeline/gen_docs.py"], env)
        self.run_cmd([sys.executable, "pipeline/export_xlsx.py"], env)
        self.run_cmd([sys.executable, "pipeline/append_extended_xlsx.py"], env)
        self.run_cmd([sys.executable, "pipeline/build_site.py"], env)
        self.run_cmd([sys.executable, "pipeline/inject_extended.py", "--repo", str(self.stage)], env)
        self.run_cmd([sys.executable, "pipeline/stage_downloads.py"], {**env, "SOCAL_ROOT": str(self.stage)})

    def compact_cpc_cache(self) -> None:
        """Keep sampled CPC values and provenance, not nationwide GIS archives."""
        cpc_root = self.ext / "cpc"
        if not cpc_root.exists():
            return
        for product_dir in cpc_root.iterdir():
            if not product_dir.is_dir():
                continue
            for path in list(product_dir.iterdir()):
                if path.is_file() and (
                    path.name == "_source.txt" or path.name.startswith("_sample_")
                ):
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

    def validate(self) -> None:
        report = self.stage / "build-reports/validation.json"
        self.run_cmd([sys.executable, "pipeline/validate_build.py", "--root", str(self.stage),
                      "--baseline", str(ROOT / "dataset/csv"), "--today", self.today.isoformat(),
                      "--report", str(report)])

    def promote(self) -> None:
        for rel in ("dataset/csv", "dataset/socal_fishing_dataset.xlsx", "app", "docs", ".refresh-cache", "data"):
            src, dst = self.stage / rel, ROOT / rel
            if src.is_dir():
                for path in src.rglob("*"):
                    if path.is_file():
                        target = dst / path.relative_to(src)
                        atomic_write(target, path.read_bytes())
            elif src.exists():
                atomic_write(dst, src.read_bytes())
        state = {
            "schema_version": 1, "last_successful_local_date": self.today.isoformat(),
            "last_successful_at_utc": now_pair()[0], "last_successful_commit": os.environ.get("GITHUB_SHA"),
        }
        atomic_write(STATE_PATH, (json.dumps(state, indent=2) + "\n").encode())

    def execute(self) -> int:
        try:
            self.prepare()
            if self.args.dry_run:
                self.record("dry_run", "Dry-run validation", "manual", "on demand",
                            success=True, newest=self.today.isoformat(), freshness="cached",
                            cached=True, note="No network requests or repository files were changed.")
                self.write_status(completed=True)
                self.ensure_staged_run_meta()
                env = {"SOCAL_ROOT": str(ROOT), "SOCAL_OUT": str(self.stage / "dataset/csv"),
                       "SOCAL_SITE": str(self.stage / "app"), "SOCAL_DOCS": str(self.stage / "docs")}
                self.run_cmd([sys.executable, "pipeline/gen_docs.py"], env)
                self.run_cmd([sys.executable, "pipeline/build_site.py"], env)
                self.run_cmd([sys.executable, "pipeline/inject_extended.py", "--repo", str(self.stage)], env)
                self.run_cmd([sys.executable, "pipeline/stage_downloads.py"], {**env, "SOCAL_ROOT": str(self.stage)})
                if (self.stage / "data").exists():
                    self.learning("dry_run")
                    self.inject_learning(env)
                self.validate()
                print("DRY RUN PASSED: no network requests and no repository changes")
                return 0

            if STATE_PATH.exists() and not self.args.force:
                try:
                    state = json.loads(STATE_PATH.read_text())
                    if state.get("last_successful_local_date") == self.today.isoformat():
                        report = {
                            "ok": True,
                            "skipped": True,
                            "promoted": False,
                            "local_date": self.today.isoformat(),
                            "reason": "A successful refresh already completed for this Pacific local date.",
                        }
                        atomic_write(
                            ROOT / "build-report.json",
                            (json.dumps(report, indent=2) + "\n").encode(),
                        )
                        print(json.dumps(report, indent=2))
                        return 0
                except Exception:
                    pass
            self.fetch_ndbc()
            self.fetch_coops()
            self.fetch_mur()
            self.fetch_forecasts()
            self.fetch_extended_forecast()
            self.fetch_periodic()
            self.fetch_dock()
            try:
                self.fetch_dock_outcomes()
            except Exception as exc:  # outcome collection is never critical
                self.record("dock_outcomes", "Dock totals for outcome ledger", "daily", "daily",
                            success=False, freshness="failed", cached=False, error=exc)
            if self.failures:
                raise RuntimeError("critical fetch failures: " + "; ".join(self.failures))
            self.write_status()
            self.build()
            self.compact_cpc_cache()
            self.write_status(completed=True)
            # Record this run's forecasts in the immutable ledger, then evaluate (never retrains v1).
            self.learning("live")
            # Rebuild payload/downloads once to embed final completion metadata.
            env = {"SOCAL_ROOT": str(ROOT), "SOCAL_OUT": str(self.stage / "dataset/csv"),
                   "SOCAL_SITE": str(self.stage / "app"), "SOCAL_DOCS": str(self.stage / "docs")}
            self.run_cmd([sys.executable, "pipeline/build_site.py"], env)
            self.run_cmd([sys.executable, "pipeline/inject_extended.py", "--repo", str(self.stage)], env)
            self.run_cmd([sys.executable, "pipeline/stage_downloads.py"], {**env, "SOCAL_ROOT": str(self.stage)})
            self.inject_learning(env)
            self.validate()
            if not self.args.no_promote:
                self.promote()
            report = {
                "ok": True, "local_date": self.today.isoformat(), "promoted": not self.args.no_promote,
                "source_summary": json.loads((self.stage / "dataset/csv/source_status.json").read_text())["summary"],
            }
            atomic_write(ROOT / "build-report.json", (json.dumps(report, indent=2) + "\n").encode())
            print(json.dumps(report, indent=2))
            return 0
        except Exception as exc:
            report = {"ok": False, "local_date": self.today.isoformat(), "error": str(exc), "critical_failures": self.failures}
            if not self.args.dry_run:
                atomic_write(ROOT / "build-report.json", (json.dumps(report, indent=2) + "\n").encode())
            print(json.dumps(report, indent=2), file=sys.stderr)
            return 1
        finally:
            if not self.args.keep_stage:
                shutil.rmtree(self.stage, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No network or repository changes; validate a staged copy")
    ap.add_argument("--force", action="store_true", help="Ignore same-local-day success state")
    ap.add_argument("--full-rebuild", action="store_true", help="Recompute derived history and force periodic-source checks")
    ap.add_argument("--no-promote", action="store_true", help="Fetch/build/validate but leave the checkout unchanged")
    ap.add_argument("--keep-stage", action="store_true", help="Keep the temporary staging tree for diagnostics")
    ap.add_argument("--today", help="YYYY-MM-DD local-date override for reproducible testing")
    ap.add_argument("--mur-overlap-days", type=int, default=7)
    args = ap.parse_args()
    if not 3 <= args.mur_overlap_days <= 14:
        ap.error("--mur-overlap-days must be between 3 and 14")
    return Refresh(args).execute()


if __name__ == "__main__":
    raise SystemExit(main())
