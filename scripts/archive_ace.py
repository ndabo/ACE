#!/usr/bin/env python3
"""
Archive PJM ACE data locally before it falls out of DataMiner2's 30-day window.

Usage:
  python scripts/archive_ace.py                 # catch up: archive everything
                                                # missing in the last 30 days
  python scripts/archive_ace.py 2026-07-01      # archive one specific day

Run daily (cron/launchd) to build a permanent ACE history. Days already fully
archived are skipped; today is always re-fetched to pick up new intervals.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from freqreg import store  # noqa: E402
from freqreg.pjm_api import fetch_ace, now_ept  # noqa: E402


def archive_day(day: pd.Timestamp) -> int:
    df = fetch_ace(f"{day.date()} 00:00", f"{day.date()} 23:59")
    if df.empty:
        print(f"[ace] {day.date()}: no data (outside retention or future)")
        return 0
    store.write_ace_day(day, df.set_index("ts"))
    print(f"[ace] {day.date()}: {len(df)} rows archived")
    return len(df)


def main() -> None:
    if len(sys.argv) > 1:
        archive_day(pd.Timestamp(sys.argv[1]))
        return

    today = now_ept().normalize()
    have = set(store.available_ace_days())
    total = 0
    failed: list[str] = []
    for offset in range(29, -1, -1):
        day = today - pd.Timedelta(days=offset)
        # re-fetch today (still filling) and any missing past day
        if str(day.date()) in have and day != today:
            continue
        try:
            total += archive_day(day)
        except Exception as exc:  # keep going; a failed day retries next run
            failed.append(str(day.date()))
            print(f"[ace] {day.date()}: FAILED ({exc})")
        time.sleep(10)  # stay under PJM's per-minute rate limit
    print(f"[ace] catch-up complete, {total} new rows"
          + (f", failed: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
