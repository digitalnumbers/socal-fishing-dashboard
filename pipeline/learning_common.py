"""Shared constants and helpers for the forecast-learning / backtesting system.

Everything in this module is *governance configuration*: class cut-offs, lead buckets,
region definitions, sample-size rules and the fixed v1 probability mapping. None of it
changes how the production Bite Score (Model v1) is computed. The scoring engine itself
lives, unchanged, in section 6 of ``build_dataset.py``.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The live scoring engine implements exactly this model id. A registry whose
# production pointer names any other model fails validation: promotion therefore
# always requires a deliberate, reviewed code change, never a data edit alone.
ENGINE_MODEL_VERSION = "v1"
LEDGER_SCHEMA_VERSION = 1
LABEL_VERSION = "L1"
PARSER_VERSION = "dock-trips-1"

# ---------------------------------------------------------------- classes
CLASSES = ["Poor", "Fair", "Good", "Very Good", "Excellent"]
CLASS_CUTS = [40.0, 60.0, 75.0, 90.0]           # score >= cut moves up one class
GOOD_INDEX = 2                                  # "Good or better" = class index >= 2
GOOD_CUT = CLASS_CUTS[1]


def score_class_index(score: float | None) -> int | None:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return None
    return sum(1 for c in CLASS_CUTS if score >= c)


def score_class(score):
    i = score_class_index(score)
    return None if i is None else CLASSES[i]


# ---------------------------------------------------------------- lead buckets
LEAD_BUCKETS = [("0-3", 0, 3), ("4-7", 4, 7), ("8-14", 8, 14), ("15-30", 15, 30)]


def lead_bucket(lead: int) -> str | None:
    for name, lo, hi in LEAD_BUCKETS:
        if lo <= lead <= hi:
            return name
    return None


def horizon_group(lead: int) -> str:
    return "short_0_7" if lead <= 7 else "outlook_8_30"


# Fixed, pre-registered v1 probability mapping for P(Good or better).
# A logistic around the Good cut-off whose spread widens with lead time, so that
# longer leads are automatically less confident. It is part of the v1 wrapper and
# is NOT refitted; calibration changes belong to challenger models.
P_SPREAD = {"0-3": 8.0, "4-7": 10.0, "8-14": 14.0, "15-30": 20.0}


def p_good(score, lead: int) -> float | None:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return None
    s = P_SPREAD[lead_bucket(int(lead))]
    return round(1.0 / (1.0 + math.exp(-(float(score) - GOOD_CUT) / s)), 4)


def tier_for_lead(lead: int) -> tuple[int, str, str]:
    if lead <= 6:
        return 1, "high", "High confidence"
    if lead <= 14:
        return 2, "moderate", "Moderate confidence"
    return 3, "outlook", "Outlook / trend only"


# ---------------------------------------------------------------- regions
# Outcome regions are where dock-total trips can be attributed. Forecast zones map
# into them; outer_banks legitimately belongs to two trip regions.
REGIONS = {
    "kelp_nearshore": {"name": "Kelp & nearshore (Point Loma, La Jolla)",
                       "zones": ["pt_loma_kelp", "la_jolla"], "labelled": True},
    "coronados": {"name": "Coronado Islands", "zones": ["coronados"], "labelled": True},
    "offshore_banks": {"name": "Offshore banks (9-Mile, 302/371/Hidden)",
                       "zones": ["nine_mile", "outer_banks"], "labelled": True},
    "extended_offshore": {"name": "Extended offshore (outer banks, Cortez/Tanner)",
                          "zones": ["outer_banks", "cortez_tanner"], "labelled": True},
    "bays": {"name": "San Diego & Mission Bay", "zones": ["sd_bay", "mission_bay"], "labelled": False},
    "surf": {"name": "Surf zone", "zones": ["surf_zone"], "labelled": False},
}
ZONE_PRIMARY_REGION = {
    "pt_loma_kelp": "kelp_nearshore", "la_jolla": "kelp_nearshore", "coronados": "coronados",
    "nine_mile": "offshore_banks", "outer_banks": "offshore_banks", "cortez_tanner": "extended_offshore",
    "sd_bay": "bays", "mission_bay": "bays", "surf_zone": "surf",
}

# Governance priorities used by promotion guardrails.
PRIORITY_SPECIES = ["yellowtail", "yellowfin_tuna", "bluefin_tuna", "calico_bass", "rockfish"]
PRIORITY_REGIONS = ["kelp_nearshore", "offshore_banks"]

# ---------------------------------------------------------------- sample sizes
MIN_PAIRS_PRELIMINARY = 30
MIN_PAIRS_REPORTABLE = 100
MIN_EVENTS_PR = 10          # Good-or-better events needed before precision/recall is shown
MIN_TARGET_DATES = 10       # distinct target dates for anything beyond "insufficient"
MIN_TARGET_DATES_REPORTABLE = 30
MIN_ISSUANCES_PRELIMINARY = 3
MIN_ISSUANCES_REPORTABLE = 10


def sample_flag(n_pairs: int, n_dates: int, n_issuances: int = 999) -> str:
    """Pairs from one day or one issuance are strongly correlated, so the flag depends on
    distinct target dates and distinct issuances as well as on the raw pair count."""
    if n_pairs < MIN_PAIRS_PRELIMINARY or n_dates < MIN_TARGET_DATES or n_issuances < MIN_ISSUANCES_PRELIMINARY:
        return "insufficient"
    if n_pairs < MIN_PAIRS_REPORTABLE or n_dates < MIN_TARGET_DATES_REPORTABLE or n_issuances < MIN_ISSUANCES_REPORTABLE:
        return "preliminary"
    return "reportable"


# ---------------------------------------------------------------- seasons
def met_season(month: int) -> str:
    return {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
            6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}[int(month)]


SEASON_START_MONTH = 3      # fishing "season-to-date" runs from 1 March


def season_start(d: date) -> date:
    y = d.year if d.month >= SEASON_START_MONTH else d.year - 1
    return date(y, SEASON_START_MONTH, 1)


# ---------------------------------------------------------------- scoring mirror
def soft_ceiling(v, knee=0.90, span=0.10):
    if v <= knee:
        return v
    return knee + span * (1.0 - math.exp(-(v - knee) / span))


def inverse_soft_ceiling(v, knee=0.90, span=0.10):
    if v <= knee:
        return v
    x = min((v - knee) / span, 0.999999)
    return knee - span * math.log(1.0 - x)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


TERMS = ["sst", "season", "tide", "moon", "pressure", "swell", "front", "chl"]


def rescore_from_terms(terms: dict, weights: dict, anomaly, fishability_pct) -> float | None:
    """Recompute a v1-style score from as-issued stored driver terms.

    Mirrors ``score_row`` in build_dataset.py exactly (weighted geometric mean, anomaly
    multiplier, access gate, soft ceiling). Used only offline for challenger weight tests
    and for the reproduction test that proves the ledger holds enough to re-score."""
    used = {k: v for k, v in terms.items()
            if v is not None and not (isinstance(v, float) and math.isnan(v)) and weights.get(k, 0) > 0}
    if not used:
        return None
    wsum = sum(weights[k] for k in used)
    core = math.exp(sum(weights[k] * math.log(max(v, 0.02)) for k, v in used.items()) / wsum)
    at = 1.0 if anomaly is None or (isinstance(anomaly, float) and math.isnan(anomaly)) else anomaly
    core = clamp(core * at, 0.01, 1.35)
    fish = 0.7 if fishability_pct is None or (isinstance(fishability_pct, float) and math.isnan(fishability_pct)) \
        else fishability_pct / 100.0
    return round(soft_ceiling(clamp(core * (0.35 + 0.65 * fish), 0.01, 1.35)) * 100, 1)


# ---------------------------------------------------------------- io helpers
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(Path(p).read_bytes())


def deterministic_gzip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(data)
    return buf.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json(path: Path, obj) -> None:
    atomic_write(path, (json.dumps(obj, indent=2, default=str, allow_nan=False) + "\n").encode())


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(v):
    """Parse an ISO-ish timestamp to aware UTC datetime; None when not a timestamp."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    if not s or len(s) < 10 or not s[:4].isdigit():
        return None
    try:
        if len(s) == 10:
            return None  # bare dates are coverage labels, not instants
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def clean_scalar(v):
    if v is None:
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            return str(v)
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, 4)
    return v


def data_dir(root: Path | None = None) -> Path:
    return Path(root or ROOT) / "data"


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return buf.getvalue().encode()
