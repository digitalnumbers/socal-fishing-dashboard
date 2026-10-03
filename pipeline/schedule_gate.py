#!/usr/bin/env python3
"""DST-safe GitHub Actions schedule gate for the 06:30 Pacific daily run."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


PT = ZoneInfo("America/Los_Angeles")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "manual"))
    ap.add_argument("--now-utc", help="ISO timestamp override for tests")
    ap.add_argument("--window-start", default="06:20")
    ap.add_argument("--window-end", default="06:50")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now_utc.replace("Z", "+00:00")) if args.now_utc else datetime.now(timezone.utc)
    local = now.astimezone(PT)
    start_h, start_m = map(int, args.window_start.split(":"))
    end_h, end_m = map(int, args.window_end.split(":"))
    minute = local.hour * 60 + local.minute
    allowed = args.event != "schedule" or start_h * 60 + start_m <= minute <= end_h * 60 + end_m
    result = {
        "allowed": allowed,
        "event": args.event,
        "utc": now.isoformat(),
        "local": local.isoformat(),
        "local_date": local.date().isoformat(),
        "window": f"{args.window_start}-{args.window_end} America/Los_Angeles",
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
