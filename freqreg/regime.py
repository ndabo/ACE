"""
Signal-regime tagging.

PJM's regulation market has hard schema breaks. Pre- and post-break data must
never be mixed in one analysis without an explicit flag, so every stored record
carries a regime tag. Breaks are parameterized so the planned Oct 2026 Phase 2
split (RegUp/RegDown) is a data change, not a rewrite.
"""
from __future__ import annotations

import pandas as pd

# Ordered list of (effective timestamp EPT, regime name). A record belongs to
# the last regime whose start is <= its timestamp.
REGIME_BREAKS: list[tuple[str, str]] = [
    ("1900-01-01 00:00", "rega_regd"),      # legacy two-signal market
    ("2025-10-01 00:00", "consolidated"),   # Phase 1: single bidirectional signal
    ("2026-10-01 00:00", "regup_regdown"),  # Phase 2: separate Up/Down products
]


def tag_regime(ts: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """Return the regime name for each timestamp (EPT, tz-naive)."""
    idx = pd.DatetimeIndex(ts) if not isinstance(ts, pd.DatetimeIndex) else ts
    starts = pd.DatetimeIndex([b[0] for b in REGIME_BREAKS])
    names = [b[1] for b in REGIME_BREAKS]
    # searchsorted: index of first break > ts, minus 1 = active regime
    pos = starts.searchsorted(idx, side="right") - 1
    return pd.Series([names[p] for p in pos], index=getattr(ts, "index", None))


def regime_of(ts: str | pd.Timestamp) -> str:
    """Regime name for a single timestamp."""
    t = pd.Timestamp(ts)
    name = REGIME_BREAKS[0][1]
    for start, n in REGIME_BREAKS:
        if pd.Timestamp(start) <= t:
            name = n
    return name
