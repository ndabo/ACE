"""
Constrained battery simulation following the PJM regulation signal.

Sign convention (matches PJM's normalized signal): positive = discharge
instruction, negative = charge instruction. Target MW = signal x cleared MW.

The constraints (SOC bounds, ramp limit, round-trip efficiency) are the point
of the project: an unconstrained battery tracks the signal perfectly by
construction, so all findings live in where and why the constrained battery
deviates. Each interval is flagged with the binding constraint.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Constraint flags, in the order they are applied
FLAG_NONE = "none"
FLAG_RAMP = "ramp"
FLAG_SOC_LOW = "soc_low"    # empty: cannot discharge as instructed
FLAG_SOC_HIGH = "soc_high"  # full: cannot charge as instructed


@dataclass(frozen=True)
class BatteryParams:
    capacity_mw: float = 10.0     # cleared regulation capability (per CLAUDE.md default)
    energy_mwh: float = 20.0      # 2h duration placeholder default
    soc_min: float = 0.10         # usable SOC window
    soc_max: float = 0.90
    soc_init: float = 0.50
    ramp_mw_per_min: float = 100.0  # batteries are fast; default effectively unbinding
    round_trip_eff: float = 0.86    # split sqrt/sqrt between charge and discharge

    @property
    def eta_one_way(self) -> float:
        return float(np.sqrt(self.round_trip_eff))


def simulate(signal: pd.Series, params: BatteryParams = BatteryParams()) -> pd.DataFrame:
    """
    Run the constrained simulation over a normalized signal series.

    signal : pd.Series indexed by timestamp, values roughly in [-1, 1].
    Returns a DataFrame indexed like the signal with columns:
      target_mw, delivered_mw, soc, flag
    """
    if signal.empty:
        return pd.DataFrame(columns=["target_mw", "delivered_mw", "soc", "flag"])

    idx = signal.index
    # step length in hours; assume uniform cadence, fall back to 2s
    if len(idx) > 1:
        dt_h = (idx[1] - idx[0]).total_seconds() / 3600.0
    else:
        dt_h = 2.0 / 3600.0

    target = np.clip(signal.to_numpy(dtype=float), -1.0, 1.0) * params.capacity_mw
    n = len(target)

    ramp_step = params.ramp_mw_per_min * dt_h * 60.0  # max MW change per step
    eta = params.eta_one_way
    e = params.energy_mwh

    delivered = np.empty(n)
    soc = np.empty(n)
    flags = np.empty(n, dtype=object)

    prev_p = 0.0
    s = params.soc_init
    for i in range(n):
        t = target[i]

        # 1. ramp limit relative to previous delivered MW
        p = np.clip(t, prev_p - ramp_step, prev_p + ramp_step)
        ramp_bound = p != t

        # 2. SOC headroom: discharging drains soc/eta faster; charging stores p*eta
        p_dis_max = min(params.capacity_mw, max(0.0, (s - params.soc_min) * e * eta / dt_h))
        p_chg_max = min(params.capacity_mw, max(0.0, (params.soc_max - s) * e / (eta * dt_h)))
        p2 = np.clip(p, -p_chg_max, p_dis_max)

        if p2 != p or (abs(p2) < abs(t) and not ramp_bound and p2 != t):
            flags[i] = FLAG_SOC_LOW if t > 0 else FLAG_SOC_HIGH
        elif ramp_bound:
            flags[i] = FLAG_RAMP
        else:
            flags[i] = FLAG_NONE
        p = p2

        # 3. SOC update
        if p >= 0:
            s -= p * dt_h / (e * eta)      # discharge: draw more than delivered
        else:
            s -= p * eta * dt_h / e        # charge: store less than absorbed
        s = min(max(s, 0.0), 1.0)

        delivered[i] = p
        soc[i] = s
        prev_p = p

    return pd.DataFrame(
        {"target_mw": target, "delivered_mw": delivered, "soc": soc, "flag": flags},
        index=idx,
    )


def tracking_metrics(sim: pd.DataFrame, params: BatteryParams = BatteryParams()) -> dict:
    """
    Headline metrics of instructed vs. delivered — NOT correlation.

    tracking_error_mw   : mean |target - delivered|
    tracking_error_pct  : same, normalized by capacity
    precision_score     : 1 - sum|error| / sum|target|  (PJM precision-score proxy)
    pct_constrained     : share of intervals with any binding constraint
    """
    if sim.empty:
        return {}
    err = (sim["target_mw"] - sim["delivered_mw"]).abs()
    denom = sim["target_mw"].abs().sum()
    return {
        "tracking_error_mw": float(err.mean()),
        "tracking_error_pct": float(err.mean() / params.capacity_mw * 100),
        "precision_score": float(1 - err.sum() / denom) if denom > 0 else 1.0,
        "pct_constrained": float((sim["flag"] != FLAG_NONE).mean() * 100),
    }


def deviation_intervals(sim: pd.DataFrame) -> pd.DataFrame:
    """
    Contiguous runs where a constraint bound the battery (the "so what" data).
    Returns start, end, flag, duration_s, mean_shortfall_mw per run.
    """
    if sim.empty:
        return pd.DataFrame(columns=["start", "end", "flag", "duration_s", "mean_shortfall_mw"])
    constrained = sim["flag"] != FLAG_NONE
    if not constrained.any():
        return pd.DataFrame(columns=["start", "end", "flag", "duration_s", "mean_shortfall_mw"])

    runs = (constrained != constrained.shift()).cumsum()
    out = []
    for _, grp in sim[constrained].groupby(runs[constrained]):
        shortfall = (grp["target_mw"] - grp["delivered_mw"]).abs().mean()
        out.append({
            "start": grp.index[0],
            "end": grp.index[-1],
            "flag": grp["flag"].mode().iloc[0],
            "duration_s": (grp.index[-1] - grp.index[0]).total_seconds(),
            "mean_shortfall_mw": float(shortfall),
        })
    return pd.DataFrame(out)
