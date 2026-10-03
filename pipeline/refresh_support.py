"""Network, parsing, and freshness helpers for the transactional daily refresh."""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

PT = ZoneInfo("America/Los_Angeles")
USER_AGENT = (
    "SoCalFishingIntelligence/1.2 "
    "(non-commercial public-data dashboard; automated daily refresh)"
)


def now_pair() -> tuple[str, str]:
    utc = datetime.now(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z"), utc.astimezone(PT).isoformat()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def fetch_bytes(url: str, timeout: int = 90, attempts: int = 3) -> bytes:
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
            if not data:
                raise ValueError("empty response")
            return data
        except (OSError, urllib.error.URLError, ValueError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(12, (2 ** attempt) + random.random()))
    raise RuntimeError(f"{type(last).__name__}: {last}") from last


def valid_json(data: bytes, row_key: str | None = None) -> dict:
    obj = json.loads(data)
    if not isinstance(obj, dict) or obj.get("error"):
        raise ValueError(f"invalid API response: {str(obj.get('error', 'not an object'))[:180]}")
    if row_key is not None and not isinstance(obj.get(row_key), list):
        raise ValueError(f"missing list field {row_key}")
    return obj


def status_record(source_id: str, display: str, category: str, cadence: str,
                  attempted_utc: str, attempted_local: str, previous: dict | None = None,
                  *, success: bool, newest: str | None = None, freshness: str = "fresh",
                  cached: bool = False, error: str | None = None, note: str = "",
                  deployment_critical: bool = False) -> dict:
    previous = previous or {}
    return {
        "source_identifier": source_id,
        "display_name": display,
        "refresh_category": category,
        "last_attempted_fetch_utc": attempted_utc,
        "last_attempted_fetch_local": attempted_local,
        "last_successful_fetch_utc": attempted_utc if success else previous.get("last_successful_fetch_utc"),
        "last_successful_fetch_local": attempted_local if success else previous.get("last_successful_fetch_local"),
        "newest_valid_source_timestamp": newest or previous.get("newest_valid_source_timestamp"),
        "expected_cadence": cadence,
        "freshness": freshness,
        "used_cached_data": bool(cached),
        "deployment_critical": bool(deployment_critical),
        "error": (str(error)[:300] if error else None),
        "note": note,
    }


def ndbc_latest(data: bytes) -> datetime:
    text = data.decode("utf-8", "replace")
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        p = line.split()
        if len(p) < 15:
            continue
        ts = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), tzinfo=timezone.utc)
        if ts > datetime.now(timezone.utc) + timedelta(hours=2):
            raise ValueError("future observation timestamp")
        return ts
    raise ValueError("no parseable NDBC observation")


def coops_latest(obj: dict, key: str) -> datetime:
    rows = obj.get(key) or []
    parsed = [datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=PT)
              for r in rows if r.get("t") and r.get("v") not in (None, "")]
    if not parsed:
        raise ValueError("no valid CO-OPS rows")
    return max(parsed)


def csv_latest_time(data: bytes) -> tuple[str, int]:
    text = data.decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) < 2:
        raise ValueError("ERDDAP response has no data rows")
    rows = rows[1:] if rows and str(next(iter(rows[0].values()), "")).lower() in {"utc", "degree_c"} else rows
    times = [r.get("time") for r in rows if r.get("time") and r.get("analysed_sst") not in (None, "", "NaN")]
    if not times:
        raise ValueError("ERDDAP response has no valid SST rows")
    return max(times), len(times)


def forecast_coverage(obj: dict) -> tuple[date, date]:
    daily = obj.get("daily") or {}
    dates = [date.fromisoformat(x) for x in daily.get("time", [])]
    if not dates or not any(isinstance(v, list) and any(x is not None for x in v)
                            for k, v in daily.items() if k != "time"):
        raise ValueError("forecast has no usable daily values")
    return min(dates), max(dates)


def due(previous: dict | None, category: str, local_day: date, force: bool = False) -> bool:
    if force or not previous or not previous.get("last_successful_fetch_local"):
        return True
    try:
        last = datetime.fromisoformat(previous["last_successful_fetch_local"]).date()
    except Exception:
        return True
    age = (local_day - last).days
    return age >= {"daily": 1, "weekly": 7, "monthly": 28}.get(category, 1)


def safe_extract_zip(blob: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        if not names or any(Path(n).is_absolute() or ".." in Path(n).parts for n in names):
            raise ValueError("unsafe or empty ZIP")
        tmp = dest.with_name(dest.name + ".tmp")
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        zf.extractall(tmp)
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        os.replace(tmp, dest)


SPECIES_MAP = {
    "california halibut": "california_halibut", "halibut": "california_halibut",
    "calico bass": "calico_bass", "kelp bass": "calico_bass",
    "sand bass": "sand_bass", "barred sand bass": "sand_bass",
    "spotted bay bass": "spotted_bay_bass", "corbina": "corbina",
    "barred surfperch": "barred_surfperch", "surfperch": "barred_surfperch",
    "rockfish": "rockfish", "vermilion rockfish": "rockfish",
    "sheephead": "sheephead", "california sheephead": "sheephead",
    "barracuda": "barracuda", "california barracuda": "barracuda",
    "bonito": "bonito", "pacific bonito": "bonito", "yellowtail": "yellowtail",
    "white seabass": "white_seabass", "bluefin tuna": "bluefin_tuna",
    "yellowfin tuna": "yellowfin_tuna", "dorado": "dorado",
    "mahi": "dorado", "mahi mahi": "dorado", "mahi-mahi": "dorado",
}


def parse_counts(text: str) -> tuple[dict, dict]:
    kept, released = {}, {}
    for item in re.split(r",\s*", text):
        m = re.match(r"\s*(\d+)\s+(.+?)\s*$", item)
        if not m:
            continue
        n, name = int(m.group(1)), m.group(2)
        name = re.sub(r"\([^)]*\)|\bup to .*$", "", name, flags=re.I)
        is_released = bool(re.search(r"\breleased?\b", name, re.I))
        name = re.sub(r"\breleased?\b", "", name, flags=re.I).strip(" .-").lower()
        sid = SPECIES_MAP.get(name) or SPECIES_MAP.get(name.rstrip("s"))
        if not sid and name.endswith("rockfish"):
            sid = "rockfish"
        if sid:
            target = released if is_released else kept
            target[sid] = target.get(sid, 0) + n
    return kept, released


def trip_type(raw: str) -> str:
    s = raw.lower()
    if "twilight" in s:
        return "twilight"
    if "1/2 day" in s or "half day" in s:
        return "half_day"
    if "3/4 day" in s:
        return "three_quarter_day"
    if "full day" in s:
        return "full_day"
    if "overnight" in s:
        return "overnight"
    m = re.search(r"(\d+(?:\.5)?)\s*day", s)
    return (m.group(1) + "_day") if m else "unknown"


def parse_dock_html(data: bytes, requested: date, url: str) -> tuple[date, list[dict]]:
    soup = BeautifulSoup(data, "html.parser")
    text = soup.get_text(" ", strip=True)
    matches = re.findall(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", text)
    full = re.search(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", text)
    if not full:
        raise ValueError("dock page date not found")
    page_date = datetime.strptime(full.group(0), "%B %d, %Y").date()
    reports = []
    landing = None
    landing_map = {
        "Fisherman's Landing": "Fishermans", "H&M Landing": "H&M",
        "Point Loma Sportfishing": "Point Loma", "Seaforth Sportfishing": "Seaforth",
    }
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
            kept, released = parse_counts(counts)
            if not am or not (kept or released):
                continue
            raw_trip = re.sub(r"\d+\s*Anglers?", "", trip, flags=re.I).strip()
            reports.append({
                "date": page_date.isoformat(), "landing": landing, "boat": boat.split(landing)[0].strip(),
                "trip_type": trip_type(raw_trip), "trip_type_raw": raw_trip, "anglers": int(am.group(1)),
                "zone_hint": "offshore" if re.search(r"offshore|overnight|\d+(?:\.5)?\s*day", raw_trip, re.I)
                             else ("coronados" if "coronado" in raw_trip.lower() else "point loma kelp"),
                "catch": kept, "released": released, "source_url": url,
                "raw_text": f"{boat} | {trip} | {counts}"[:400],
            })
    if not reports:
        raise ValueError("dock parser produced zero valid boat reports")
    if page_date > requested:
        raise ValueError("dock page reported a future date")
    return page_date, reports
