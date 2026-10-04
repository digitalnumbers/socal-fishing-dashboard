#!/usr/bin/env python3
"""One-time recovery of forecasts that were genuinely issued before the ledger existed.

Before the ledger, every build overwrote its forecast tables, but the committed git
snapshots preserve exactly what was published. This script extracts those committed
tables and records them as ``recovered_git_snapshot`` issuances. Hindcast rows (dates
before the build date) are never recorded: they were recomputed from later
observations and are not forecasts.

Idempotent: an issuance whose source commit is already in the index is skipped.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forecast_ledger import append_issuance, build_issuance  # noqa: E402
from learning_common import ROOT  # noqa: E402

FILES = ["scores_daily.csv", "conditions_daily.csv", "extended_scores.csv", "extended_outlook.csv",
         "climatological_curve.csv", "enso_zone_composite.csv", "enso_current.csv", "dim_species.csv",
         "extended_meta.json", "source_status.json"]

# (commit, how the issue/cutoff instants are determined)
SNAPSHOTS = [
    # 2026-08-28 build: published by commit d8556c6; inputs cut at the extended build time.
    "d8556c69a83993d472d5459f40ca003e9542df43",
    # 2026-10-03 first transactional daily refresh.
    "c625edf3fc330d3e1de6666be8b0adca771f4a63",
]


def git(*args) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--commit", action="append", help="override the snapshot list")
    a = ap.parse_args()
    for commit in a.commit or SNAPSHOTS:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for f in FILES:
                try:
                    (d / f).write_bytes(git("show", f"{commit}:dataset/csv/{f}"))
                except subprocess.CalledProcessError:
                    pass
            commit_utc = git("log", "-1", "--format=%cI", commit).decode().strip()
            status = json.loads((d / "source_status.json").read_text()) if (d / "source_status.json").exists() else None
            meta = json.loads((d / "extended_meta.json").read_text())
            if status:
                issue = status.get("build_completed_at_utc") or commit_utc
                cutoff = status.get("build_started_at_utc")
            else:
                issue, cutoff = commit_utc, meta.get("built_utc")
            from learning_common import parse_ts
            issue = parse_ts(issue).isoformat().replace("+00:00", "Z")
            cutoff = parse_ts(cutoff).isoformat().replace("+00:00", "Z") if cutoff else None
            rows, m = build_issuance(d, issue_utc=issue, data_cutoff_utc=cutoff,
                                     origin="recovered_git_snapshot", source_ref=commit)
            entry = append_issuance(a.data, rows, m)
            print(json.dumps(entry or {"skipped": commit}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
