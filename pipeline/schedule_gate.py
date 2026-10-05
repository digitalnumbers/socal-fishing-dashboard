#!/usr/bin/env python3
"""DST-safe GitHub Actions gate for the daily Pacific refresh.

Scheduled jobs may start well after their cron time.  A scheduled run is
therefore accepted at or after the local target threshold instead of only
inside a narrow window.  Persistent state rejects a second successful run for
the same Pacific calendar date before dependencies are installed.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PT = ZoneInfo("America/Los_Angeles")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "manual"))
    ap.add_argument("--now-utc", help="ISO timestamp override for tests")
    ap.add_argument("--not-before", default="08:15")
    ap.add_argument("--state-file", default=".refresh-cache/daily_state.json")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now_utc.replace("Z", "+00:00")) if args.now_utc else datetime.now(timezone.utc)
    local = now.astimezone(PT)
    start_h, start_m = map(int, args.not_before.split(":"))
    minute = local.hour * 60 + local.minute
    scheduled = args.event == "schedule"
    already_completed = False
    state_path = Path(args.state_file)
    if scheduled and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            already_completed = state.get("last_successful_local_date") == local.date().isoformat()
        except (OSError, json.JSONDecodeError):
            # The orchestrator validates state again. A damaged gate-state file
            # must not prevent a recovery attempt from running.
            already_completed = False

    allowed = not scheduled or (
        minute >= start_h * 60 + start_m and not already_completed
    )
    if not scheduled:
        reason = "manual dispatch bypasses the schedule gate"
    elif already_completed:
        reason = "a successful refresh already completed for this Pacific date"
    elif minute < start_h * 60 + start_m:
        reason = f"scheduled candidate arrived before {args.not_before} Pacific"
    else:
        reason = "scheduled candidate is at or after the Pacific target threshold"
    result = {
        "allowed": allowed,
        "event": args.event,
        "utc": now.isoformat(),
        "local": local.isoformat(),
        "local_date": local.date().isoformat(),
        "not_before": f"{args.not_before} America/Los_Angeles",
        "already_completed": already_completed,
        "reason": reason,
    }
    print(json.dumps(result))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"allowed={'true' if allowed else 'false'}\n")
            f.write(f"local_date={local.date().isoformat()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
