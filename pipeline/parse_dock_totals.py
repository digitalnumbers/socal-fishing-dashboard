#!/usr/bin/env python3
"""Parse San Diego dock-total pages into normalized catch_reports.json"""
import json, re, datetime, os

SRC = "/home/user/workspace/socal/data/raw/pages/sdfr_boats.json"
OUT = "/home/user/workspace/socal/data/raw/catch_reports.json"

SPECIES_MAP = {
    "california halibut": "california_halibut", "halibut": "california_halibut",
    "calico bass": "calico_bass", "calico": "calico_bass", "kelp bass": "calico_bass",
    "sand bass": "sand_bass", "sandbass": "sand_bass", "barred sand bass": "sand_bass",
    "spotted bay bass": "spotted_bay_bass", "bass (spotted bay)": "spotted_bay_bass",
    "corbina": "corbina", "california corbina": "corbina",
    "barred surfperch": "barred_surfperch", "surfperch": "barred_surfperch",
    "rockfish": "rockfish", "red snapper": "rockfish", "vermilion": "rockfish",
    "vermilion rockfish": "rockfish", "rockcod": "rockfish", "rock cod": "rockfish",
    "sheephead": "sheephead", "california sheephead": "sheephead",
    "barracuda": "barracuda", "california barracuda": "barracuda",
    "bonito": "bonito", "boneheads": "bonito", "pacific bonito": "bonito",
    "yellowtail": "yellowtail", "yellows": "yellowtail", "firecracker yellowtail": "yellowtail",
    "white seabass": "white_seabass", "wsb": "white_seabass",
    "bluefin tuna": "bluefin_tuna", "bluefin": "bluefin_tuna",
    "yellowfin tuna": "yellowfin_tuna", "yellowfin": "yellowfin_tuna",
    "dorado": "dorado", "mahi": "dorado", "mahi mahi": "dorado", "mahi-mahi": "dorado",
    # explicitly kept distinct, non-canonical
    "whitefish": "ocean_whitefish", "ocean whitefish": "ocean_whitefish",
}

LANDINGS_SD = {
    "Fisherman's Landing": "Fishermans",
    "H&M Landing": "H&M",
    "Point Loma Sportfishing": "Point Loma",
    "Seaforth Sportfishing": "Seaforth",
    "Sea Forth Sportfishing": "Seaforth",
    "Islandia Sportfishing": "Islandia",
    "Fisherman's Landing Fish Counts": "Fishermans",
}


def norm_trip(t):
    s = t.lower()
    if "1/2 day" in s or "half day" in s or "twilight" in s: return "half_day"
    if "3/4 day" in s: return "three_quarter_day"
    if "3.5 day" in s or "3 1/2 day" in s: return "3.5_day"
    if "2.5 day" in s or "2 1/2 day" in s: return "2.5_day"
    if "1.5 day" in s or "1 1/2 day" in s: return "1.5_day"
    dm = re.search(r"(\d+)\s*day", s)
    if dm and int(dm.group(1)) >= 2: return f"{int(dm.group(1))}_day"
    if "overnight" in s: return "overnight"
    if "full day" in s: return "full_day"
    if "extended" in s or "day" in s: return "multi_day"
    return "unknown"


def zone_hint(trip_raw, trip_type):
    s = trip_raw.lower()
    if "coronado" in s: return "coronados"
    if "offshore" in s: return "offshore"
    if trip_type in ("overnight", "1.5_day", "2_day", "2.5_day", "3_day", "3.5_day",
                     "4_day", "5_day", "multi_day"): return "offshore"
    if "bay" in s: return "bay"
    if "local" in s or trip_type in ("half_day", "three_quarter_day", "full_day"):
        return "point loma kelp"
    return "unknown"


COUNT_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def parse_counts(cell):
    """Return (catch, released, unmapped, unmapped_released)"""
    catch, released, unmapped, unmapped_rel = {}, {}, {}, {}
    # split on commas not inside parentheses
    parts, depth, cur = [], 0, ""
    for ch in cell:
        if ch == "(": depth += 1
        if ch == ")": depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip(): parts.append(cur)

    for p in parts:
        p = p.strip()
        if not p: continue
        m = COUNT_RE.match(p)
        if not m: continue
        n = int(m.group(1))
        name = m.group(2)
        name = re.sub(r"\([^)]*\)", "", name)               # drop "(up to 30 pounds)"
        name = re.sub(r"\bup to .*$", "", name, flags=re.I)
        is_rel = bool(re.search(r"\breleased?\b", name, flags=re.I))
        name = re.sub(r"\breleased?\b", "", name, flags=re.I)
        name = re.sub(r"\s+", " ", name).strip(" .-").lower()
        if not name: continue
        key = SPECIES_MAP.get(name)
        if key is None:
            key = SPECIES_MAP.get(name.rstrip("s"))
        if key is None and re.search(r"rockfish$", name):
            key = "rockfish"   # e.g. "red rockfish", "vermilion rockfish"
        canonical = key in {
            "california_halibut","calico_bass","sand_bass","spotted_bay_bass","corbina",
            "barred_surfperch","rockfish","sheephead","barracuda","bonito","yellowtail",
            "white_seabass","bluefin_tuna","yellowfin_tuna","dorado"}
        if key and canonical:
            tgt = released if is_rel else catch
            tgt[key] = tgt.get(key, 0) + n
        else:
            k = key or re.sub(r"[^a-z0-9]+", "_", name).strip("_")
            tgt = unmapped_rel if is_rel else unmapped
            tgt[k] = tgt.get(k, 0) + n
    return catch, released, unmapped, unmapped_rel


EXTRA_SOURCES = [
 {"url": "https://www.sandiegofishreports.com/dock_totals/boats.php",
  "name": "San Diego Fish Reports - Fish Counts by Boat (primary structured source)",
  "fetched_ok": True,
  "notes": "Primary source. Per-date pages via ?date=YYYY-MM-DD give boat, landing, angler count, trip type and species counts in tables; all reports below come from these pages."},
 {"url": "https://www.fishermanslanding.com/fishcounts.php",
  "name": "Fisherman's Landing fish counts (narrative)",
  "fetched_ok": True,
  "notes": "Fetched OK; used only to cross-validate 2026-08-26/27 figures (Dolphin, Liberty, Pegasus, Pacific Queen, Pacific Dawn all matched). Narrative format, no separate rows extracted."},
 {"url": "https://www.seaforthlanding.com/fishcounts.php",
  "name": "Seaforth Sportfishing fish counts (narrative)",
  "fetched_ok": True,
  "notes": "Fetched OK; used only to cross-validate 2026-08-27 Seaforth figures (Pacifica, Tribute, Voyager, Sea Watch, New Seaforth all matched). Narrative format, no separate rows extracted."},
 {"url": "https://www.pointlomasportfishing.com/fishcounts.php",
  "name": "Point Loma Sportfishing fish counts",
  "fetched_ok": True,
  "notes": "Fetched OK but rendered as boat names and date headers only, counts loaded dynamically; no usable numbers extracted. Point Loma data taken from San Diego Fish Reports instead."},
 {"url": "https://www.hmlanding.com/fishcounts.php",
  "name": "H&M Landing fish counts",
  "fetched_ok": False,
  "notes": "HTTP client error (blocked). H&M data taken from San Diego Fish Reports instead."},
 {"url": "https://www.sportfishingreport.com/dock-totals/index.php",
  "name": "SportfishingReport.com dock totals index",
  "fetched_ok": False,
  "notes": "HTTP client error on both the dock-totals index and the San Diego fish-counts page; contributed no data."},
 {"url": "https://www.976-tuna.com/",
  "name": "976-TUNA",
  "fetched_ok": True,
  "notes": "Loaded but homepage returned stale syndicated posts (May 2026) plus advertising, no current San Diego dock totals; contributed no data."},
]

ROW_RE = re.compile(r"^\|(.+?)\|(.+?)\|(.+?)\|\s*$")
ANG_RE = re.compile(r"(\d+)\s*Anglers?", re.I)

def main():
    pages = json.load(open(SRC))
    reports = []
    sources = []
    seen_pages = {}

    for date in sorted(pages):
        pg = pages[date]
        content = pg.get("content") or ""
        title = pg.get("title") or ""
        tm = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", title)
        page_date = None
        if tm:
            page_date = datetime.datetime.strptime(tm.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        note = ""
        if page_date and page_date != date:
            note = f"requested date={date} but page reported {page_date}; counts not yet posted for {date}"
        sources.append({
            "url": pg["url"],
            "name": f"San Diego Fish Reports - dock totals by boat ({date})",
            "fetched_ok": pg.get("error") is None and len(content) > 500,
            "notes": note or f"parsed as report date {page_date}",
        })
        if page_date is None or page_date in seen_pages:
            continue   # skip duplicate/stale page (e.g. 8/28 served 8/27 data)
        seen_pages[page_date] = date

        landing = None
        for line in content.split("\n"):
            hm = re.match(r"^##\s+(.*?)\s+Fish Counts\s*$", line.strip())
            if hm:
                landing = hm.group(1).strip()
                continue
            if not line.startswith("|"): continue
            if line.strip().startswith("|--") or "|Boat|Trip Details|" in line: continue
            m = ROW_RE.match(line)
            if not m: continue
            boatcell, tripcell, countcell = (x.strip() for x in m.groups())
            if landing is None: continue
            short = LANDINGS_SD.get(landing)
            city_sd = boatcell.endswith("San Diego, CA")
            if short is None or not city_sd:
                continue
            boat = boatcell
            # strip trailing "<Landing><City, ST>"
            boat = re.sub(re.escape("San Diego, CA") + r"$", "", boat).strip()
            boat = re.sub(re.escape(landing) + r"$", "", boat).strip()
            am = ANG_RE.search(tripcell)
            anglers = int(am.group(1)) if am else None
            trip_raw = ANG_RE.sub("", tripcell).strip()
            trip_type = norm_trip(trip_raw)
            catch, released, unmapped, unmapped_rel = parse_counts(countcell)
            rec = {
                "date": page_date,
                "landing": short,
                "boat": boat,
                "trip_type": trip_type,
                "trip_type_raw": trip_raw,
                "anglers": anglers,
                "zone_hint": zone_hint(trip_raw, trip_type),
                "catch": catch,
                "released": released,
                "source_url": pg["url"],
                "raw_text": f"{boat} | {tripcell} | {countcell}"[:400],
            }
            if unmapped: rec["unmapped"] = unmapped
            if unmapped_rel: rec["unmapped_released"] = unmapped_rel
            reports.append(rec)

    reports.sort(key=lambda r: (r["date"] or "", r["landing"], r["boat"], r["trip_type"]))
    return reports, sources


if __name__ == "__main__":
    reports, sources = main()
    dates = sorted({r["date"] for r in reports})
    landings = sorted({r["landing"] for r in reports})
    sources = EXTRA_SOURCES + sources
    from collections import Counter as _C
    per_date = _C(r["date"] for r in reports)
    doc = {
        "collected_at_utc": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": sources,
        "reports": reports,
        "coverage_notes": (
            f"Covers {len(dates)} consecutive report dates {dates[0]} to {dates[-1]} "
            f"(13 of the trailing 14 days). Reports per date: "
            + ", ".join(f"{d}={per_date[d]}" for d in dates) + ". "
            "Landings included (San Diego city only): Fisherman's Landing, H&M Landing, "
            "Point Loma Sportfishing, Seaforth Sportfishing. "
            "MISSING: 2026-08-28 (today, PDT) — the dock-totals page for 2026-08-28 served the "
            "2026-08-27 counts, i.e. same-day counts were not yet posted at collection time; that "
            "duplicate page was excluded rather than double-counted. "
            "Non-San-Diego landings that appear on the same source pages (Oceanside Sea Center, "
            "Helgren's/Oceanside, Dana Wharf, LA-area landings) were deliberately excluded. "
            "Private/charter-only listings without a trip-type string (e.g. 'Lucky B Sportfishing', "
            "'Patriot (SD)') are kept with trip_type='unknown'; two such rows carry no species counts "
            "at all in the source and therefore have empty catch objects — they are not zero-catch "
            "claims. No date was ambiguous: every retained page carried an explicit headline date "
            "('Fish Counts by Boat - <Month D, YYYY>'), so no report has date=null. "
            "Counts are dock-reported kept fish; 'X Released' entries were split into the 'released' "
            "object. Size qualifiers such as '(up to 30 pounds)' were stripped from species names and "
            "preserved in raw_text. Species outside the canonical list (sculpin, skipjack tuna, "
            "ocean whitefish, lingcod, bullet tuna, striped marlin, wahoo, cabezon, black seabass, "
            "mako shark, triggerfish, croaker, perch, rock sole) are kept verbatim under 'unmapped' / "
            "'unmapped_released' and were never forced into a canonical key. "
            "Cross-checked against the Fisherman's Landing and Seaforth Sportfishing narrative "
            "fish-count pages for 2026-08-27; boat/angler/species figures matched. "
            "sportfishingreport.com (dock-totals index and San Diego page) and hmlanding.com returned "
            "HTTP client errors and contributed no data; 976-tuna.com loaded but its homepage served "
            "stale (May 2026) syndicated posts and no current San Diego dock totals."
        ),
    }
    json.dump(doc, open(OUT, "w"), indent=1)
    print("reports:", len(reports), "dates:", len(dates), dates)
    print("landings:", landings)
    from collections import Counter
    c = Counter()
    for r in reports:
        c.update(r["catch"].keys())
    print("species:", dict(c))
    u = Counter()
    for r in reports:
        u.update(r.get("unmapped", {}).keys())
    print("unmapped:", dict(u))
    print("trip_types:", dict(Counter(r["trip_type"] for r in reports)))
