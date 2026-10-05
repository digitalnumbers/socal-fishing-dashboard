import gzip
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import evaluate_forecasts  # noqa: E402
from forecast_ledger import append_issuance, build_issuance, read_index, verify_ledger  # noqa: E402
from learning_common import (  # noqa: E402
    ENGINE_MODEL_VERSION, TERMS, lead_bucket, rescore_from_terms, sample_flag,
)
from model_registry import initial_registry, validate_registry  # noqa: E402
from outcome_ledger import build_labels, classify_trip, ingest  # noqa: E402

CSV = ROOT / "dataset/csv"
SPECIES = pd.read_csv(CSV / "dim_species.csv")


def trip(report_date, boat, ttype, raw, anglers, kept, days=0.5):
    return {"report_date": report_date, "landing": "Test", "boat": boat, "trip_type": ttype,
            "trip_type_raw": raw, "trip_days": days, "anglers": anglers, "kept_json": json.dumps(kept),
            "released_json": "{}", "tracked_fish": sum(kept.values()), "raw_text": f"{boat} {raw}",
            "source": "test", "source_url": "test://", "parser_version": "t"}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _issue(self, when=None, ref="run:test"):
        if when is None:
            status = json.loads((CSV / "source_status.json").read_text())
            when = (
                pd.Timestamp(status["build_started_at_utc"])
                + pd.Timedelta(minutes=15)
            ).isoformat().replace("+00:00", "Z")
        rows, meta = build_issuance(CSV, issue_utc=when, data_cutoff_utc=when, origin="live_daily_refresh",
                                    source_ref=ref)
        return append_issuance(self.tmp, rows, meta), rows

    def test_rescore_from_terms_reproduces_v1(self):
        _, rows = self._issue()
        w = {r.species_id: json.loads(r.weights_json) for r in SPECIES.itertuples()}
        checked = 0
        for r in rows:
            if int(r["lead_days"]) > 6 or r["bite_score"] is None:
                continue
            terms = {k: r.get(f"term_{k}") for k in TERMS}
            s = rescore_from_terms(terms, w[r["species_id"]], r["anomaly_term"], r["fishability"])
            self.assertAlmostEqual(s, float(r["bite_score"]), delta=0.25, msg=r["record_id"])
            checked += 1
        self.assertGreater(checked, 300)

    def test_ledger_is_write_once_and_tamper_evident(self):
        first, _ = self._issue()
        self.assertIsNotNone(first)
        dup, _ = self._issue()
        self.assertIsNone(dup, "same issuance must not be appended twice")
        baseline = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(self.tmp, baseline, dirs_exist_ok=True)
            self.assertEqual(verify_ledger(self.tmp, baseline), [])
            f = self.tmp / "forecast_ledger" / read_index(self.tmp).iloc[0].file
            with gzip.open(f, "rt") as fh:
                text = fh.read()
            with gzip.open(f, "wt") as fh:
                fh.write(text.replace(",v1,", ",v9,", 1))
            self.assertTrue(verify_ledger(self.tmp, baseline), "edited issuance must fail verification")
        finally:
            shutil.rmtree(baseline, ignore_errors=True)

    def test_no_deterministic_sea_state_beyond_day_14(self):
        _, rows = self._issue()
        far = [r for r in rows if int(r["lead_days"]) >= 15]
        self.assertTrue(far)
        for r in far:
            self.assertIsNone(r["wave_ft"])
            self.assertIsNone(r["wind_kt"])
            inp = json.loads(r["inputs_json"] or "{}")
            self.assertFalse({"wave_ft", "wind_kt", "swell_ft"} & set(inp))


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # reference window trips (calico caught at kelp) so a species-region reference exists
        fetches = []
        for d in pd.date_range("2026-08-15", "2026-08-27").date:
            fetches.append({"report_date": d.isoformat(), "fetched_at_utc": "2026-08-28T00:00:00Z", "ok": True,
                            "error": None, "method": "test",
                            "trips": [trip(d.isoformat(), f"B{i}", "half_day", "1/2 Day AM", 30,
                                           {"calico_bass": 30}) for i in range(3)]})
        # 2026-09-10: plenty of effort, zero calico -> effort-adjusted Poor
        fetches.append({"report_date": "2026-09-10", "fetched_at_utc": "2026-09-11T00:00:00Z", "ok": True,
                        "error": None, "method": "test",
                        "trips": [trip("2026-09-10", f"B{i}", "half_day", "1/2 Day AM", 40, {"sand_bass": 5})
                                  for i in range(3)]})
        # 2026-09-11: page fetched but no boats reported -> no label at all
        fetches.append({"report_date": "2026-09-11", "fetched_at_utc": "2026-09-12T00:00:00Z", "ok": True,
                        "error": None, "method": "test", "trips": []})
        ingest(self.tmp, fetches)
        self.labels = build_labels(self.tmp, SPECIES, date(2026, 9, 20))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _get(self, d, sp="calico_bass", region="kelp_nearshore"):
        return self.labels[(self.labels.fishing_date == d) & (self.labels.species_id == sp)
                           & (self.labels.region_id == region)]

    def test_no_reports_is_not_a_poor_bite(self):
        self.assertTrue(self._get("2026-09-11").observed_class.isna().all())

    def test_effort_adjusted_zero_is_poor(self):
        r = self._get("2026-09-10")
        self.assertEqual(len(r), 1)
        self.assertEqual(r.iloc[0].observed_class, "Poor")
        self.assertIn("effort_adjusted_zero", r.iloc[0].dq_flags)

    def test_species_without_reference_is_not_labelled(self):
        r = self.labels[(self.labels.fishing_date == "2026-09-10") & (self.labels.species_id == "white_seabass")]
        self.assertTrue(r.observed_class.isna().all())

    def test_trip_classification(self):
        self.assertEqual(classify_trip("Full Day Coronado Islands", "full_day")[0], "coronados")
        self.assertEqual(classify_trip("3 Day", "3_day")[0], None)
        self.assertEqual(classify_trip("Full Day", "full_day")[0], None)
        self.assertEqual(classify_trip("1/2 Day AM", "half_day")[0], "kelp_nearshore")
        self.assertEqual(classify_trip("2 Day", "2_day")[0], "extended_offshore")


class EvaluationTests(unittest.TestCase):
    def test_lead_buckets_and_sample_flags(self):
        self.assertEqual([lead_bucket(x) for x in (0, 3, 4, 7, 8, 14, 15, 30)],
                         ["0-3", "0-3", "4-7", "4-7", "8-14", "8-14", "15-30", "15-30"])
        self.assertEqual(sample_flag(500, 40, 1), "insufficient")
        self.assertEqual(sample_flag(500, 40, 5), "preliminary")
        self.assertEqual(sample_flag(500, 40, 12), "reportable")

    def test_walk_forward_calibration_uses_only_prior_labels(self):
        rows = []
        for i in range(120):
            d = (pd.Timestamp("2026-06-01") + pd.Timedelta(days=i)).date().isoformat()
            rows.append({"target_date": d, "issue_utc": f"{d}T13:00:00Z", "lead_days": 0, "region_id": "r",
                         "species_id": "s", "bite_score": 70.0 + i % 10, "fishing_date": d, "issuance_id": d,
                         "observed_score": (30.0 + i % 10) if i < 100 else 95.0,
                         "available_at_utc": (pd.Timestamp(d) + pd.Timedelta(days=1)).isoformat() + "Z"})
        p = pd.DataFrame(rows)
        p["horizon_group"] = "short_0_7"
        lab = p[["fishing_date", "region_id", "species_id", "observed_score", "available_at_utc"]].copy()
        lab["observed_class"] = "Fair"
        ch = {"model_id": "cal", "kind": "calibration", "min_training_pairs": 60}
        s = evaluate_forecasts.walk_forward_calibration(p, lab, pd.DataFrame(), ch)
        # day 100 is the first 90-observation day; its calibration must not have seen it
        self.assertLess(float(s.iloc[100]), 60.0)
        # before 60 prior labels exist, the challenger equals v1
        self.assertAlmostEqual(float(s.iloc[10]), 70.0)


class RegistryTests(unittest.TestCase):
    def test_registry_gate(self):
        reg = initial_registry(SPECIES)
        self.assertEqual(reg["production_model"], ENGINE_MODEL_VERSION)
        self.assertEqual(validate_registry(reg, reg), [])
        self.assertFalse(reg["governance"]["auto_retrain"])
        bad = json.loads(json.dumps(reg))
        bad["production_model"] = "v1.1-cal"
        self.assertTrue(validate_registry(bad, reg))
        bad = json.loads(json.dumps(reg))
        bad["models"] = [m for m in bad["models"] if m["model_id"] != "v1.1-cal"]
        self.assertTrue(validate_registry(bad, reg))
        bad = json.loads(json.dumps(reg))
        bad["decisions"] = bad["decisions"][1:]
        self.assertTrue(validate_registry(bad, reg))


if __name__ == "__main__":
    unittest.main()
