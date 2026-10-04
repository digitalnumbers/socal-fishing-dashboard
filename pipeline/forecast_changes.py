#!/usr/bin/env python3
""""Why this forecast changed": latest issuance vs the prior issuance, per target day.

For each zone x species x target date present in both issuances the score change is
decomposed in the engine's own log space: each driver's weighted log suitability, the
anomaly multiplier and the access gate. The decomposition is rescaled to score points so
the named drivers add up to the actual change (soft-ceiling compression included).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_common import TERMS, atomic_write, csv_bytes  # noqa: E402
from forecast_ledger import ledger_root, read_index  # noqa: E402

COLUMNS = ["current_issuance", "prior_issuance", "target_date", "zone_id", "species_id", "lead_now",
           "lead_prior", "tier_now", "tier_prior", "score_prior", "score_now", "delta", "class_prior",
           "class_now", "top_drivers", "driver_points_json", "input_changes", "notes"]
LABEL = {"sst": "water temperature fit", "season": "seasonality", "tide": "tide", "moon": "moon",
         "pressure": "pressure trend", "swell": "swell", "front": "SST front", "chl": "chlorophyll",
         "anomaly": "ENSO/SST anomaly boost", "access": "fishability (wind/sea access)"}


def _log_parts(r, w: dict) -> dict:
    present = {k: getattr(r, f"term_{k}") for k in TERMS}
    present = {k: v for k, v in present.items() if v is not None and not (isinstance(v, float) and math.isnan(v))
               and w.get(k, 0) > 0}
    if not present:
        return {}
    ws = sum(w[k] for k in present)
    out = {k: w[k] / ws * math.log(max(v, 0.02)) for k, v in present.items()}
    at = r.anomaly_term
    out["anomaly"] = math.log(at) if at and not (isinstance(at, float) and math.isnan(at)) and at > 0 else 0.0
    f = r.fishability
    f = 0.7 if f is None or (isinstance(f, float) and math.isnan(f)) else f / 100.0
    out["access"] = math.log(0.35 + 0.65 * f)
    return out


def _fmt(v, d=1):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{d}f}"


def compute(data_root: Path, species_cfg: pd.DataFrame) -> pd.DataFrame:
    import gzip
    idx = read_index(data_root)
    if len(idx) < 2:
        return pd.DataFrame(columns=COLUMNS)
    cur_i, pri_i = idx.iloc[-1], idx.iloc[-2]

    def load(r):
        with gzip.open(ledger_root(data_root) / r.file, "rt") as fh:
            return pd.read_csv(fh)

    cur, pri = load(cur_i), load(pri_i)
    weights = {r.species_id: json.loads(r.weights_json) for r in species_cfg.itertuples()}
    key = ["target_date", "zone_id", "species_id"]
    m = cur.merge(pri, on=key, suffixes=("", "_p"))
    pcols = {c[:-2]: c for c in m.columns if c.endswith("_p")}
    rows = []
    for r in m.itertuples(index=False):
        rd = r._asdict()
        prior = type("P", (), {k: rd[v] for k, v in pcols.items()})
        w = weights.get(r.species_id, {})
        a, b = _log_parts(r, w), _log_parts(prior, w)
        delta = (r.bite_score or 0) - (prior.bite_score or 0)
        keys = sorted(set(a) | set(b))
        dl = {k: a.get(k, 0.0) - b.get(k, 0.0) for k in keys}
        notes = []
        added = [k for k in keys if k in a and k not in b and k not in ("anomaly", "access")]
        dropped = [k for k in keys if k in b and k not in a and k not in ("anomaly", "access")]
        if added:
            notes.append("driver now available: " + ", ".join(LABEL[k] for k in added))
        if dropped:
            notes.append("driver dropped (no data at this lead): " + ", ".join(LABEL[k] for k in dropped))
        total = sum(dl.values())
        pts = {k: (delta * v / total if abs(total) > 1e-9 else 0.0) for k, v in dl.items()}
        top = sorted(((k, v) for k, v in pts.items() if abs(v) >= 1.0), key=lambda kv: -abs(kv[1]))[:3]
        top_txt = "; ".join(f"{LABEL[k]} {v:+.1f}" for k, v in top) if top else (
            "no material driver change" if abs(delta) < 1 else "small changes across several drivers")
        ic = []
        for col, lab, unit in (("sst_f", "SST", "°F"), ("sst_anom_f", "SST anomaly", "°F"),
                               ("wave_ft", "seas", " ft"), ("wind_kt", "wind", " kt"),
                               ("tide_range_ft", "tide range", " ft")):
            x, y = rd.get(col), rd.get(col + "_p")
            if x is None or y is None or (isinstance(x, float) and math.isnan(x)) or (isinstance(y, float) and math.isnan(y)):
                if (x is None or (isinstance(x, float) and math.isnan(x))) != (y is None or (isinstance(y, float) and math.isnan(y))):
                    ic.append(f"{lab} {'withdrawn' if x is None or (isinstance(x, float) and math.isnan(x)) else 'now forecast'}")
                continue
            thr = 0.3 if "sst" in col else (0.5 if col != "wind_kt" else 2.0)
            if abs(x - y) >= thr:
                ic.append(f"{lab} {_fmt(y)}→{_fmt(x)}{unit}")
        if rd.get("sst_source") != rd.get("sst_source_p"):
            ic.append(f"SST source {rd.get('sst_source_p')} → {rd.get('sst_source')}")
        if r.horizon_tier != prior.horizon_tier:
            notes.append(f"confidence tier {int(prior.horizon_tier)}→{int(r.horizon_tier)} as the date came closer")
        if r.model_version != prior.model_version:
            notes.append(f"model {prior.model_version}→{r.model_version}")
        rows.append({
            "current_issuance": cur_i.issuance_id, "prior_issuance": pri_i.issuance_id,
            "target_date": r.target_date, "zone_id": r.zone_id, "species_id": r.species_id,
            "lead_now": int(r.lead_days), "lead_prior": int(prior.lead_days),
            "tier_now": int(r.horizon_tier), "tier_prior": int(prior.horizon_tier),
            "score_prior": prior.bite_score, "score_now": r.bite_score, "delta": round(delta, 1),
            "class_prior": prior.bite_class, "class_now": r.bite_class, "top_drivers": top_txt,
            "driver_points_json": json.dumps({k: round(v, 1) for k, v in pts.items() if abs(v) >= 0.1}, sort_keys=True),
            "input_changes": "; ".join(ic) or None, "notes": "; ".join(notes) or None,
        })
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(["target_date", "zone_id", "species_id"])


def write(data_root: Path, df: pd.DataFrame) -> None:
    atomic_write(Path(data_root) / "forecast_changes_latest.csv",
                 csv_bytes(df.astype(object).where(df.notna(), None).to_dict("records"), COLUMNS))
