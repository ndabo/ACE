#!/usr/bin/env python3
"""
Backfill the local signal store from PJM's posted files.

Usage:
  python scripts/backfill_signal.py sample [path/to/sample.xlsx]
      Ingest the post-redesign sample workbook (downloads if no path given).

  python scripts/backfill_signal.py zip path/to/rto-regulation-signal-data.zip ["regex"]
      Ingest pre-redesign monthly files from the archive zip, optionally
      filtered by a regex on member names (e.g. "02 2025").
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from freqreg import ingest  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("sample", "zip"):
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "sample":
        if len(sys.argv) > 2:
            path = Path(sys.argv[2])
        else:
            path = Path(tempfile.gettempdir()) / "pjm-new-signal-sample.xlsx"
            print(f"downloading sample workbook -> {path}")
            path.write_bytes(requests.get(ingest.SAMPLE_XLSX_URL, timeout=120).content)
        days = ingest.ingest_sample_xlsx(path)
    else:
        if len(sys.argv) < 3:
            print("zip mode needs a path to rto-regulation-signal-data.zip")
            sys.exit(1)
        member_filter = sys.argv[3] if len(sys.argv) > 3 else None
        days = ingest.ingest_signal_zip(sys.argv[2], member_filter=member_filter)

    print(f"ingested {len(days)} day(s): {days[:5]}{' ...' if len(days) > 5 else ''}")


if __name__ == "__main__":
    main()
