import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from refresh_support import due, status_record  # noqa: E402
from run_daily_refresh import Refresh, committed_snapshot_date  # noqa: E402
import build_dataset as build_dataset_module  # noqa: E402


class ScheduleGateTests(unittest.TestCase):
    def gate(self, when):
        out = subprocess.check_output(
            [sys.executable, str(ROOT / "pipeline/schedule_gate.py"),
             "--event", "schedule", "--now-utc", when],
            text=True,
        )
        return json.loads(out)

    def test_pdt_candidate_is_allowed(self):
        self.assertTrue(self.gate("2026-08-28T13:30:00+00:00")["allowed"])

    def test_pst_candidate_is_allowed(self):
        self.assertTrue(self.gate("2026-01-15T14:30:00+00:00")["allowed"])

    def test_wrong_dst_candidate_is_rejected(self):
        self.assertFalse(self.gate("2026-08-28T14:30:00+00:00")["allowed"])


class FreshnessStateTests(unittest.TestCase):
    def test_model_validation_reuses_prior_when_windows_do_not_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            prior = Path(tmp) / "model_validation.csv"
            prior.write_text(
                "species_id,species,n_days,spearman_rho,pearson_r,"
                "spearman_rho_raw_cpue,mean_cpue_per_angler_day,"
                "mean_score,total_fish,total_anglers\n"
                "yellowtail,California Yellowtail,8,0.4,0.3,0.2,1.1,70,40,20\n"
            )
            original = build_dataset_module.BASELINE
            build_dataset_module.BASELINE = tmp
            try:
                result = build_dataset_module.validate(
                    build_dataset_module.pd.DataFrame(),
                    build_dataset_module.pd.DataFrame(),
                )
            finally:
                build_dataset_module.BASELINE = original
            self.assertEqual(len(result), 1)
            self.assertEqual(result.iloc[0]["species_id"], "yellowtail")

    def test_committed_snapshot_date_uses_last_day_of_seven_day_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_dir = Path(tmp) / "dataset" / "csv"
            csv_dir.mkdir(parents=True)
            rows = ["date"] + [
                (date(2026, 8, 14) + timedelta(days=offset)).isoformat()
                for offset in range(21)
            ]
            (csv_dir / "conditions_daily.csv").write_text("\n".join(rows) + "\n")
            self.assertEqual(committed_snapshot_date(Path(tmp)), date(2026, 8, 28))

    def test_failed_attempt_preserves_previous_success(self):
        previous = {
            "last_successful_fetch_utc": "2026-08-27T13:30:00+00:00",
            "last_successful_fetch_local": "2026-08-27T06:30:00-07:00",
            "newest_valid_source_timestamp": "2026-08-27",
        }
        row = status_record(
            "mur", "MUR", "daily", "daily",
            "2026-08-28T13:30:00+00:00", "2026-08-28T06:30:00-07:00",
            previous, success=False, freshness="delayed", cached=True,
            error="temporary timeout",
        )
        self.assertEqual(row["last_successful_fetch_utc"], previous["last_successful_fetch_utc"])
        self.assertEqual(row["newest_valid_source_timestamp"], "2026-08-27")
        self.assertTrue(row["used_cached_data"])

    def test_periodic_cadence(self):
        prev = {"last_successful_fetch_local": "2026-08-25T06:30:00-07:00"}
        self.assertFalse(due(prev, "weekly", date(2026, 8, 28), False))
        self.assertTrue(due(prev, "weekly", date(2026, 9, 1), False))
        self.assertTrue(due(prev, "weekly", date(2026, 8, 28), True))

    def test_cpc_cache_compaction_keeps_only_samples_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "cpc" / "814temp"
            product.mkdir(parents=True)
            (product / "_sample_cpc_814_temp.json").write_text("{}")
            (product / "_source.txt").write_text("https://example.invalid/archive.zip\n")
            (product / "large.shp").write_bytes(b"x" * 1024)
            (product / "large.dbf").write_bytes(b"x" * 1024)
            refresh = object.__new__(Refresh)
            refresh.ext = Path(tmp)
            refresh.compact_cpc_cache()
            self.assertEqual(
                sorted(p.name for p in product.iterdir()),
                ["_sample_cpc_814_temp.json", "_source.txt"],
            )


if __name__ == "__main__":
    unittest.main()
