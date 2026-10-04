#!/usr/bin/env python3
"""Walk-forward evaluation of as-issued forecasts against observed outcome labels.

Only as-issued ledger records are scored, so every v1 and baseline forecast used
information available at its issue time by construction (rolling-origin evaluation
with one origin per issuance). Challenger calibrations are refitted per issuance on
labels whose ``available_at_utc`` precedes that issuance, so no future observation
or later-revised label ever reaches a past forecast.

Models compared on identical pairs
  baseline_climatology  A. seasonal climatology (day-of-year climatological score)
  baseline_enso         B. climatology shifted by the ENSO-regime composite SST anomaly
  v1                    C. production champion (unchanged Bite Score)
  <challengers>         D. registry models with status candidate/shadow
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_common import (  # noqa: E402
    CLASSES, GOOD_INDEX, LEAD_BUCKETS, MIN_EVENTS_PR, PRIORITY_REGIONS, PRIORITY_SPECIES, REGIONS,
    TERMS, horizon_group, met_season, p_good, parse_ts, rescore_from_terms, sample_flag,
    score_class_index, season_start, utc_now_iso, write_json, csv_bytes, atomic_write,
)
from forecast_ledger import load_ledger  # noqa: E402

BASE_MODELS = ["baseline_climatology", "baseline_enso", "v1"]
SCORE_COL = {"v1": "bite_score", "baseline_climatology": "baseline_clim_score",
             "baseline_enso": "baseline_enso_score"}
WINDOWS = ["rolling_30d", "rolling_90d", "season_to_date", "all"]
DIMENSIONS = ["overall", "horizon_group", "lead_bucket", "species_id", "region_id", "month",
              "season", "enso_regime", "label_status"]
EVAL_COLUMNS = ["as_of", "window", "dimension", "stratum", "model_id", "n_pairs", "n_target_dates",
                "n_issuances", "sample_flag", "exact_hit", "within_one", "mae", "brier",
                "brier_skill_vs_clim", "mae_skill_vs_clim", "precision_good", "recall_good",
                "n_obs_good", "n_fcst_good", "obs_good_rate", "mean_forecast_score", "mean_observed_score"]


# ---------------------------------------------------------------- pair construction
def species_weights(species_cfg: pd.DataFrame) -> dict:
    return {r.species_id: json.loads(r.weights_json) for r in species_cfg.itertuples()}


def apply_weight_challengers(led: pd.DataFrame, challengers: list[dict], weights: dict) -> pd.DataFrame:
    for ch in challengers:
        if ch.get("kind") != "weights":
            continue
        over = ch.get("weights_override") or {}
        col = f"score__{ch['model_id']}"
        vals = []
        for r in led.itertuples():
            w = dict(weights.get(r.species_id, {}))
            w.update(over.get(r.species_id, {}) or over.get("*", {}))
            terms = {k: getattr(r, f"term_{k}") for k in TERMS}
            vals.append(rescore_from_terms(terms, w, r.anomaly_term, r.fishability))
        led[col] = vals
    return led


def region_forecasts(led: pd.DataFrame, extra_cols: list[str]) -> pd.DataFrame:
    """Average zone forecasts into each labelled outcome region (outer_banks feeds two)."""
    parts = []
    for rid, reg in REGIONS.items():
        if not reg["labelled"]:
            continue
        sub = led[led.zone_id.isin(reg["zones"])].copy()
        if sub.empty:
            continue
        sub["eval_region"] = rid
        parts.append(sub)
    if not parts:
        return pd.DataFrame()
    z = pd.concat(parts, ignore_index=True)
    agg = {"bite_score": "mean", "baseline_clim_score": "mean", "baseline_enso_score": "mean",
           "lead_days": "first", "lead_bucket": "first", "issue_utc": "first", "issue_local_date": "first",
           "enso_regime": "first", "zone_id": "nunique", "dq_flags": lambda s: ";".join(sorted({
               f for v in s.dropna() for f in str(v).split(";") if f}))}
    for c in extra_cols:
        agg[c] = "mean"
    g = z.groupby(["issuance_id", "target_date", "eval_region", "species_id"]).agg(agg).reset_index()
    return g.rename(columns={"eval_region": "region_id", "zone_id": "n_zones"})


def build_pairs(ledger: pd.DataFrame, labels: pd.DataFrame, challengers: list[dict],
                species_cfg: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    info = {"forecast_rows": int(len(ledger)), "excluded_late_lead0": 0}
    if ledger.empty or labels.empty:
        return pd.DataFrame(), info
    led = ledger.copy()
    late = led.dq_flags.astype(str).str.contains("lead0_issued_after_local_noon")
    info["excluded_late_lead0"] = int(late.sum())
    led = led[~late]
    led = apply_weight_challengers(led, challengers, species_weights(species_cfg))
    extra = [c for c in led.columns if c.startswith("score__")]
    rf = region_forecasts(led, extra)
    lab = labels[labels.observed_class.notna()].copy()
    pairs = rf.merge(lab, left_on=["target_date", "region_id", "species_id"],
                     right_on=["fishing_date", "region_id", "species_id"], how="inner")
    if pairs.empty:
        return pairs, info
    # Strict as-of rule: a forecast must be issued before its target day ends.
    issue = pd.to_datetime(pairs.issue_utc, utc=True)
    tend = pd.to_datetime(pairs.target_date).dt.tz_localize("America/Los_Angeles") + pd.Timedelta(days=1)
    pairs = pairs[issue < tend.dt.tz_convert("UTC")].copy()
    pairs["obs_idx"] = pairs.observed_class.map({c: i for i, c in enumerate(CLASSES)})
    pairs["y_good"] = pairs.observed_good_or_better.astype(int)
    pairs["horizon_group"] = pairs.lead_days.astype(int).map(horizon_group)
    td = pd.to_datetime(pairs.target_date)
    pairs["month"] = td.dt.month.astype(str).str.zfill(2)
    pairs["season"] = td.dt.month.map(met_season)
    pairs["overall"] = "all"
    for m, col in SCORE_COL.items():
        pairs[f"score__{m}"] = pairs[col]
    calib = [c for c in challengers if c.get("kind") == "calibration"]
    for ch in calib:
        pairs[f"score__{ch['model_id']}"] = walk_forward_calibration(pairs, lab, ledger, ch)
    info["pairs"] = int(len(pairs))
    return pairs, info


def walk_forward_calibration(pairs: pd.DataFrame, labels: pd.DataFrame, ledger: pd.DataFrame,
                             ch: dict) -> pd.Series:
    """Per issuance, fit observed_score ~ a + b*v1_score per horizon group on pairs whose
    labels were available before that issuance; apply to the issuance. Identity when
    training data are insufficient (the challenger then equals v1, recorded honestly)."""
    min_train = int(ch.get("min_training_pairs", 60))
    out = pd.Series(pairs.bite_score.values, index=pairs.index, dtype=float)
    avail = pd.to_datetime(pairs.available_at_utc, utc=True)
    for iss, g in pairs.groupby("issuance_id"):
        t0 = pd.to_datetime(g.issue_utc.iloc[0], utc=True)
        prior = pairs[(avail < t0) & (pd.to_datetime(pairs.issue_utc, utc=True) < t0)]
        for hg, gg in g.groupby("horizon_group"):
            tr = prior[prior.horizon_group == hg]
            if len(tr) < min_train or tr.bite_score.std() == 0:
                continue
            b, a = np.polyfit(tr.bite_score.astype(float), tr.observed_score.astype(float), 1)
            out.loc[gg.index] = np.clip(a + b * gg.bite_score.astype(float), 0, 99.9)
    return out


# ---------------------------------------------------------------- metrics
def metrics(df: pd.DataFrame, model: str) -> dict:
    s = df[f"score__{model}"].astype(float)
    ok = s.notna()
    d, s = df[ok], s[ok]
    n = len(d)
    out = {"n_pairs": n, "n_target_dates": int(d.target_date.nunique()) if n else 0,
           "n_issuances": int(d.issuance_id.nunique()) if n else 0}
    out["sample_flag"] = sample_flag(out["n_pairs"], out["n_target_dates"], out["n_issuances"])
    if n == 0:
        return out
    fidx = s.map(score_class_index)
    p = [p_good(v, l) for v, l in zip(s, d.lead_days)]
    p = np.array(p, dtype=float)
    y = d.y_good.values.astype(float)
    out["exact_hit"] = round(float((fidx.values == d.obs_idx.values).mean()), 4)
    out["within_one"] = round(float((np.abs(fidx.values - d.obs_idx.values) <= 1).mean()), 4)
    out["mae"] = round(float(np.abs(s.values - d.observed_score.values.astype(float)).mean()), 2)
    out["brier"] = round(float(((p - y) ** 2).mean()), 4)
    pred = fidx.values >= GOOD_INDEX
    tp = int((pred & (y == 1)).sum())
    out["n_obs_good"] = int(y.sum())
    out["n_fcst_good"] = int(pred.sum())
    out["obs_good_rate"] = round(float(y.mean()), 4)
    out["precision_good"] = round(tp / pred.sum(), 4) if pred.sum() >= MIN_EVENTS_PR else None
    out["recall_good"] = round(tp / y.sum(), 4) if y.sum() >= MIN_EVENTS_PR else None
    out["mean_forecast_score"] = round(float(s.mean()), 1)
    out["mean_observed_score"] = round(float(d.observed_score.astype(float).mean()), 1)
    return out


def with_skill(row: dict, ref: dict) -> dict:
    if row.get("brier") is not None and ref.get("brier"):
        row["brier_skill_vs_clim"] = round(1 - row["brier"] / ref["brier"], 4)
    if row.get("mae") is not None and ref.get("mae"):
        row["mae_skill_vs_clim"] = round(1 - row["mae"] / ref["mae"], 4)
    return row


def window_filter(pairs: pd.DataFrame, window: str, as_of: date) -> pd.DataFrame:
    td = pd.to_datetime(pairs.target_date).dt.date
    if window == "rolling_30d":
        return pairs[td > as_of - timedelta(days=30)]
    if window == "rolling_90d":
        return pairs[td > as_of - timedelta(days=90)]
    if window == "season_to_date":
        return pairs[td >= season_start(as_of)]
    return pairs


def block_bootstrap_skill(df: pd.DataFrame, model: str, ref: str = "baseline_climatology",
                          reps: int = 400, seed: int = 7) -> dict | None:
    """Bootstrap Brier skill vs a reference, resampling whole target dates (keeps the
    strong same-day correlation between regions/species)."""
    dates = df.target_date.unique()
    if len(dates) < 5:
        return None
    rng = np.random.default_rng(seed)
    groups = {d: g for d, g in df.groupby("target_date")}
    vals = []
    for _ in range(reps):
        pick = rng.choice(dates, size=len(dates), replace=True)
        s = pd.concat([groups[d] for d in pick])
        m, r = metrics(s, model), metrics(s, ref)
        if r.get("brier"):
            vals.append(1 - m["brier"] / r["brier"])
    if not vals:
        return None
    v = np.array(vals)
    return {"p05": round(float(np.percentile(v, 5)), 4), "p50": round(float(np.percentile(v, 50)), 4),
            "p95": round(float(np.percentile(v, 95)), 4), "p_positive": round(float((v > 0).mean()), 3),
            "reps": reps}


def evaluate(pairs: pd.DataFrame, models: list[str], as_of: date) -> list[dict]:
    rows = []
    if pairs.empty:
        return rows
    for window in WINDOWS:
        w = window_filter(pairs, window, as_of)
        for dim in DIMENSIONS:
            for stratum, g in w.groupby(dim):
                ref = metrics(g, "baseline_climatology")
                for m in models:
                    r = metrics(g, m)
                    with_skill(r, ref)
                    rows.append({"as_of": as_of.isoformat(), "window": window, "dimension": dim,
                                 "stratum": str(stratum), "model_id": m, **r})
    return rows


def reliability(pairs: pd.DataFrame, model: str = "v1") -> list[dict]:
    if pairs.empty:
        return []
    out = []
    for hg, g in pairs.groupby("horizon_group"):
        p = np.array([p_good(v, l) for v, l in zip(g[f"score__{model}"], g.lead_days)], dtype=float)
        bins = np.clip((p * 5).astype(int), 0, 4)
        for b in range(5):
            m = bins == b
            if m.sum() == 0:
                continue
            out.append({"horizon_group": hg, "bin": f"{b * 20}-{b * 20 + 20}%", "n": int(m.sum()),
                        "mean_p": round(float(p[m].mean()), 3), "obs_rate": round(float(g.y_good.values[m].mean()), 3)})
    return out


def timeline(pairs: pd.DataFrame, as_of: date, days: int = 60) -> list[dict]:
    """Observed vs forecast per target day (shortest available lead), last `days` days."""
    if pairs.empty:
        return []
    p = pairs[pd.to_datetime(pairs.target_date).dt.date > as_of - timedelta(days=days)]
    p = p.sort_values("lead_days").groupby(["target_date", "region_id", "species_id"]).head(1)
    cols = ["target_date", "region_id", "species_id", "lead_days", "observed_score", "observed_class",
            "label_status", "bite_score", "baseline_clim_score", "baseline_enso_score"]
    out = p[cols].sort_values(["target_date", "region_id", "species_id"]).copy()
    for c in ("bite_score", "baseline_clim_score", "baseline_enso_score"):
        out[c] = out[c].astype(float).round(1)
    return json.loads(out.to_json(orient="records"))


# ---------------------------------------------------------------- challenger assessment
def assess_challenger(pairs: pd.DataFrame, ch: dict, criteria: dict) -> dict:
    mid = ch["model_id"]
    res = {"model_id": mid, "checks": [], "recommendation": "insufficient_data"}
    if pairs.empty or f"score__{mid}" not in pairs:
        res["reason"] = "no labelled as-issued pairs yet"
        return res
    c, v = metrics(pairs, mid), metrics(pairs, "v1")
    res["challenger"], res["champion"] = c, v

    def check(name, passed, detail):
        res["checks"].append({"check": name, "passed": bool(passed), "detail": detail})

    enough = (c["n_pairs"] >= criteria["min_pairs"] and c["n_target_dates"] >= criteria["min_target_dates"]
              and c["n_issuances"] >= criteria["min_issuances"])
    check("minimum sample", enough, f"{c['n_pairs']} pairs / {c['n_target_dates']} target dates / "
          f"{c['n_issuances']} issuances (need {criteria['min_pairs']}/{criteria['min_target_dates']}/{criteria['min_issuances']})")
    imp = (1 - c["brier"] / v["brier"]) if v.get("brier") else 0.0
    check("aggregate Brier improvement", imp >= criteria["min_aggregate_brier_improvement"],
          f"{imp:+.1%} vs v1 (need >= {criteria['min_aggregate_brier_improvement']:+.0%})")
    check("aggregate MAE not worse", c["mae"] <= v["mae"] + criteria["max_mae_degradation_pts"],
          f"{c['mae']:.1f} vs {v['mae']:.1f} pts")
    boot = None
    if enough:
        tmp = pairs.copy()
        boot = block_bootstrap_skill(tmp, mid, ref="v1")
    check("bootstrap confidence", bool(boot and boot["p_positive"] >= criteria["min_bootstrap_p_positive"]),
          f"P(improves Brier) = {boot['p_positive'] if boot else 'n/a'}")
    for dim, keys in (("species_id", PRIORITY_SPECIES), ("region_id", PRIORITY_REGIONS)):
        for k in keys:
            g = pairs[pairs[dim] == k]
            if len(g) < criteria["min_stratum_pairs"]:
                continue
            cc, vv = metrics(g, mid), metrics(g, "v1")
            deg = (cc["brier"] / vv["brier"] - 1) if vv.get("brier") else 0.0
            check(f"priority {k} not degraded", deg <= criteria["max_priority_brier_degradation"],
                  f"Brier {cc['brier']:.3f} vs {vv['brier']:.3f} ({deg:+.1%})")
    for lb in ("0-3", "4-7"):
        g = pairs[pairs.lead_bucket == lb]
        if len(g) < criteria["min_stratum_pairs"]:
            continue
        cc, vv = metrics(g, mid), metrics(g, "v1")
        deg = (cc["brier"] / vv["brier"] - 1) if vv.get("brier") else 0.0
        check(f"short-range lead {lb} not degraded", deg <= criteria["max_short_range_brier_degradation"]
              and cc["exact_hit"] >= vv["exact_hit"] - criteria["max_short_range_hit_drop"],
              f"Brier {deg:+.1%}; exact hit {cc['exact_hit']:.0%} vs {vv['exact_hit']:.0%}")
    if not enough:
        res["recommendation"] = "insufficient_data"
    elif all(x["passed"] for x in res["checks"]):
        res["recommendation"] = "recommend_promotion_pending_human_approval"
    else:
        res["recommendation"] = "do_not_promote"
    return res


# ---------------------------------------------------------------- driver
def _live_lag(lab):
    if not len(lab):
        return None
    live = lab[~lab.dq_flags.astype(str).str.contains("backfilled")]
    return float(live.publication_lag_days.median()) if len(live) else None


def run(data_root: Path, species_cfg: pd.DataFrame, registry: dict, as_of: date) -> dict:
    data_root = Path(data_root)
    ledger = load_ledger(data_root)
    labels_p = data_root / "outcome_ledger.csv"
    labels = pd.read_csv(labels_p) if labels_p.exists() else pd.DataFrame()
    challengers = [m for m in registry.get("models", []) if m.get("status") in ("candidate", "shadow")]
    pairs, info = build_pairs(ledger, labels, challengers, species_cfg)
    models = BASE_MODELS + [c["model_id"] for c in challengers]
    rows = evaluate(pairs, models, as_of)
    atomic_write(data_root / "model_evaluation.csv", csv_bytes(rows, EVAL_COLUMNS))
    criteria = registry["governance"]["promotion_criteria"]
    headline = [r for r in rows if r["dimension"] in ("overall", "horizon_group")]
    by_lead = [r for r in rows if r["window"] == "all" and r["dimension"] == "lead_bucket"]
    strata = [r for r in rows if r["window"] == "all" and r["dimension"] in
              ("species_id", "region_id", "month", "season", "enso_regime", "label_status")]
    boot = {}
    if not pairs.empty:
        for hg, g in pairs.groupby("horizon_group"):
            boot[hg] = block_bootstrap_skill(g, "v1")
    lab_ok = labels[labels.observed_class.notna()] if not labels.empty else labels
    summary = {
        "schema_version": 1, "generated_utc": utc_now_iso(), "as_of": as_of.isoformat(),
        "label_version": "L1", "method": "rolling-origin walk-forward over as-issued ledger records",
        "ledger": {"issuances": int(ledger.issuance_id.nunique()) if not ledger.empty else 0,
                   "forecast_rows": info["forecast_rows"],
                   "first_issue": ledger.issue_local_date.min() if not ledger.empty else None,
                   "last_issue": ledger.issue_local_date.max() if not ledger.empty else None},
        "labels": {"rows": int(len(labels)), "labelled": int(len(lab_ok)),
                   "final": int((lab_ok.label_status == "final").sum()) if len(lab_ok) else 0,
                   "provisional": int((lab_ok.label_status == "provisional").sum()) if len(lab_ok) else 0,
                   "insufficient_effort": int((labels.label_status == "insufficient_effort").sum()) if len(labels) else 0,
                   "effort_adjusted_zero": int(labels.dq_flags.astype(str).str.contains("effort_adjusted_zero").sum()) if len(labels) else 0,
                   "first_fishing_date": lab_ok.fishing_date.min() if len(lab_ok) else None,
                   "last_fishing_date": lab_ok.fishing_date.max() if len(lab_ok) else None,
                   "median_publication_lag_days": float(lab_ok.publication_lag_days.median()) if len(lab_ok) else None,
                   "median_publication_lag_days_live": _live_lag(lab_ok),
                   "median_reporting_lag_days": float(lab_ok.reporting_lag_days.median()) if len(lab_ok) else None,
                   "backfilled": int(lab_ok.dq_flags.astype(str).str.contains("backfilled").sum()) if len(lab_ok) else 0},
        "pairs": {"total": int(len(pairs)), "excluded_late_lead0_rows": info["excluded_late_lead0"],
                  "target_dates": int(pairs.target_date.nunique()) if len(pairs) else 0,
                  "issuances": int(pairs.issuance_id.nunique()) if len(pairs) else 0,
                  "final_label_share": round(float((pairs.label_status == "final").mean()), 3) if len(pairs) else None},
        "models": models,
        "headline": headline, "by_lead": by_lead, "strata": strata,
        "reliability": reliability(pairs) if not pairs.empty else [],
        "timeline": timeline(pairs, as_of),
        "bootstrap_v1_brier_skill_vs_clim": boot,
        "challengers": [assess_challenger(pairs, c, criteria) for c in challengers],
        "sample_rules": {"insufficient": "< 30 pairs, < 10 target dates or < 3 issuances",
                         "preliminary": "< 100 pairs, < 30 target dates or < 10 issuances",
                         "reportable": ">= 100 pairs, >= 30 target dates and >= 10 issuances",
                         "precision_recall": ">= 10 forecast / observed Good-or-better events"},
    }
    write_json(data_root / "model_evaluation.json", summary)
    return summary
