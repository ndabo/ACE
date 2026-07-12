"""
Local parquet store for signal and ACE data.

Layout (all timestamps EPT, tz-naive):
  data/raw/signal/YYYY-MM-DD.parquet   columns: signal (normalized ±1), regime
  data/ace_archive/YYYY-MM-DD.parquet  columns: ace_mw

The ACE archive is mandatory: DataMiner2 retains only 30 days, so anything we
don't archive is gone. scripts/archive_ace.py keeps it current.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .aggregate import aggregate, pick_resolution
from .regime import tag_regime

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SIGNAL_DIR = DATA_DIR / "raw" / "signal"
ACE_DIR = DATA_DIR / "ace_archive"


def _daily_path(base: Path, day: pd.Timestamp) -> Path:
    return base / f"{day.date().isoformat()}.parquet"


# --- writing ---------------------------------------------------------------

def write_signal_day(day: pd.Timestamp, df: pd.DataFrame) -> Path:
    """Persist one day of 2-second signal data. df: index ts, column 'signal'."""
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = df[["signal"]].copy()
    out["regime"] = tag_regime(out.index).to_numpy()
    path = _daily_path(SIGNAL_DIR, day)
    out.to_parquet(path)
    return path


def write_ace_day(day: pd.Timestamp, df: pd.DataFrame) -> Path:
    """Persist (or extend) one day of ACE data. df: index ts, column 'ace_mw'."""
    ACE_DIR.mkdir(parents=True, exist_ok=True)
    path = _daily_path(ACE_DIR, day)
    if path.exists():
        prev = pd.read_parquet(path)
        df = pd.concat([prev, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(path)
    return path


# --- reading ---------------------------------------------------------------

def _read_range(base: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    days = pd.date_range(start.normalize(), end.normalize(), freq="D")
    frames = [pd.read_parquet(_daily_path(base, d)) for d in days
              if _daily_path(base, d).exists()]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    return df.loc[start:end]


def available_signal_days() -> list[str]:
    if not SIGNAL_DIR.exists():
        return []
    return sorted(p.stem for p in SIGNAL_DIR.glob("*.parquet"))


def available_ace_days() -> list[str]:
    if not ACE_DIR.exists():
        return []
    return sorted(p.stem for p in ACE_DIR.glob("*.parquet"))


def load_signal(start, end, raw: bool = False) -> pd.DataFrame:
    """
    Signal for [start, end], served at a window-appropriate resolution
    (raw only for short windows unless raw=True is forced).
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    df = _read_range(SIGNAL_DIR, start, end)
    if df.empty or raw:
        return df
    rule = pick_resolution(start, end)
    if rule is None:
        return df
    regime = df["regime"].resample(rule).first()
    out = aggregate(df, rule, cols=["signal"])
    out["regime"] = regime
    return out


def load_ace(start, end, raw: bool = False) -> pd.DataFrame:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    df = _read_range(ACE_DIR, start, end)
    if df.empty or raw:
        return df
    rule = pick_resolution(start, end)
    return aggregate(df, rule, cols=["ace_mw"]) if rule else df
