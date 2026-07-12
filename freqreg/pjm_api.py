"""
Thin client for the PJM DataMiner2 public API.

Auth + retry pattern adapted from the PJM-5CP pipeline (same API account).
Feeds used here:
  - area_control_error  : ACE MW, ~15s cadence, ONLY 30 days of retention
  - reg_market_results  : regulation market data, 30-min intervals post-redesign
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import requests

_BASE = "https://api.pjm.com/api/v1"


def _api_key() -> str:
    key = os.environ.get("PJM_API_KEY", "").strip()
    if not key:
        # fall back to .env next to the project root
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.split("=")[0].strip() == "PJM_API_KEY":
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise EnvironmentError("PJM_API_KEY not set (env var or .env file)")
    return key


def _pjm_get(feed: str, params: dict, max_retries: int = 6) -> list[dict]:
    """Paginated GET with backoff on 429/5xx. Returns all rows.

    PJM's per-minute rate limit trips easily during multi-day backfills, and
    the 429 often arrives without a Retry-After header — so the fallback
    backoff starts high enough (15s) to actually clear the window.
    """
    headers = {"Ocp-Apim-Subscription-Key": _api_key()}
    rows: list[dict] = []
    start_row = 1
    while True:
        page = {**params, "startRow": start_row, "rowCount": 50000}
        backoff = 15.0
        for attempt in range(max_retries + 1):
            resp = requests.get(f"{_BASE}/{feed}", params=page,
                                headers=headers, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == max_retries:
                    resp.raise_for_status()
                wait = max(float(resp.headers.get("Retry-After", backoff)), 5.0)
                time.sleep(wait)
                backoff *= 1.5
                continue
            resp.raise_for_status()
            break
        body = resp.json()
        items = body.get("items", [])
        rows.extend(items)
        total = body.get("totalRows", len(rows))
        if len(rows) >= total or not items:
            return rows
        start_row += len(items)


def fetch_ace(start_ept: str, end_ept: str) -> pd.DataFrame:
    """
    ACE for [start, end] EPT ("YYYY-MM-DD HH:MM"). ~15s cadence.
    Empty result for windows older than the feed's 30-day retention.
    """
    items = _pjm_get("area_control_error", {
        "datetime_beginning_ept": f"{start_ept}to{end_ept}",
        "fields": "datetime_beginning_ept,ace_mw",
    })
    df = pd.DataFrame(items)
    if df.empty:
        return pd.DataFrame(columns=["ts", "ace_mw"])
    df["ts"] = pd.to_datetime(df["datetime_beginning_ept"])
    return df[["ts", "ace_mw"]].sort_values("ts").reset_index(drop=True)


def fetch_reg_market_results(start_ept: str, end_ept: str) -> pd.DataFrame:
    """Regulation market data (30-min post-redesign; hourly before)."""
    items = _pjm_get("reg_market_results", {
        "datetime_beginning_ept": f"{start_ept}to{end_ept}",
        "fields": ("datetime_beginning_ept,requirement,total_mw,deficiency,"
                   "rto_perfscore,rega_mileage,rega_ssmw,rega_procure"),
    })
    df = pd.DataFrame(items)
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["datetime_beginning_ept"])
    return df.drop(columns=["datetime_beginning_ept"]).sort_values("ts").reset_index(drop=True)
