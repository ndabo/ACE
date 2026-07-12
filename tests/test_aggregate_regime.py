import pandas as pd

from freqreg.aggregate import aggregate, pick_resolution
from freqreg.regime import regime_of, tag_regime


def test_resolution_ladder():
    t0 = pd.Timestamp("2025-10-03 00:00")
    assert pick_resolution(t0, t0 + pd.Timedelta(hours=2)) is None       # raw
    assert pick_resolution(t0, t0 + pd.Timedelta(hours=24)) == "1min"
    assert pick_resolution(t0, t0 + pd.Timedelta(days=10)) == "5min"


def test_aggregate_keeps_extremes():
    idx = pd.date_range("2025-10-03", periods=300, freq="2s")
    df = pd.DataFrame({"signal": [0.0] * 300}, index=idx)
    df.iloc[150] = 1.0  # single spike must survive in the max column
    out = aggregate(df, "1min")
    assert out["signal_max"].max() == 1.0
    assert out["signal_mean"].max() < 1.0
    assert len(out) <= 10


def test_aggregate_raw_passthrough():
    idx = pd.date_range("2025-10-03", periods=10, freq="2s")
    df = pd.DataFrame({"signal": range(10)}, index=idx)
    assert aggregate(df, None) is df


def test_regime_tagging_across_break():
    ts = pd.Series(pd.to_datetime([
        "2025-09-30 23:59", "2025-10-01 00:00", "2026-06-01 00:00", "2026-10-01 00:00",
    ]))
    tags = tag_regime(ts)
    assert list(tags) == ["rega_regd", "consolidated", "consolidated", "regup_regdown"]
    assert regime_of("2024-01-01") == "rega_regd"
    assert regime_of("2025-12-25") == "consolidated"
