"""
Pre-aggregation layer (required, not optional — see CLAUDE.md Step 1).

2-second data is ~43k points/day/series; Streamlit chokes on raw multi-day
ranges. Raw is stored as-is; queries are served at a resolution chosen from
the window length. Aggregates carry mean/min/max so extremes aren't smoothed
away by downsampling.
"""
from __future__ import annotations

import pandas as pd

# window length ceiling (hours) -> resample rule; None = raw
RESOLUTION_LADDER: list[tuple[float | None, str | None]] = [
    (6, None),        # <= 6h: raw
    (48, "1min"),     # <= 2 days: 1-minute
    (None, "5min"),   # anything longer: 5-minute
]


def pick_resolution(start: pd.Timestamp, end: pd.Timestamp) -> str | None:
    hours = (end - start).total_seconds() / 3600.0
    for ceiling, rule in RESOLUTION_LADDER:
        if ceiling is None or hours <= ceiling:
            return rule
    return "5min"


def aggregate(df: pd.DataFrame, rule: str | None, cols: list[str] | None = None) -> pd.DataFrame:
    """
    Downsample a timestamp-indexed frame to `rule`, keeping mean/min/max per
    numeric column. rule=None returns the frame unchanged.
    """
    if rule is None or df.empty:
        return df
    cols = cols or [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    agg = df[cols].resample(rule).agg(["mean", "min", "max"])
    agg.columns = [f"{c}_{stat}" for c, stat in agg.columns]
    return agg.dropna(how="all")
