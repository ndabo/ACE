"""Battery simulation: the constraints ARE the analysis, so they get the tests."""
import numpy as np
import pandas as pd
import pytest

from freqreg.battery import (
    FLAG_NONE, FLAG_RAMP, FLAG_SOC_HIGH, FLAG_SOC_LOW,
    BatteryParams, deviation_intervals, simulate, tracking_metrics,
)


def make_signal(values, freq="2s"):
    idx = pd.date_range("2025-10-03 00:00", periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


def test_unconstrained_battery_tracks_perfectly():
    """Mid-SOC, tiny signal, huge ramp: delivered == target everywhere."""
    sig = make_signal(np.sin(np.linspace(0, 4 * np.pi, 600)) * 0.3)
    sim = simulate(sig, BatteryParams(ramp_mw_per_min=1e6))
    assert np.allclose(sim["target_mw"], sim["delivered_mw"])
    assert (sim["flag"] == FLAG_NONE).all()
    m = tracking_metrics(sim)
    assert m["tracking_error_mw"] == pytest.approx(0.0)
    assert m["precision_score"] == pytest.approx(1.0)


def test_sustained_discharge_hits_soc_floor():
    """Full discharge instruction drains a small battery to soc_min and flags it."""
    p = BatteryParams(capacity_mw=10, energy_mwh=1.0, soc_init=0.5,
                      ramp_mw_per_min=1e6)
    # 1 MWh battery, ~0.4 usable from 0.5 to 0.1 -> ~2.2 min at 10 MW (with eta)
    sig = make_signal([1.0] * 3600)  # 2 hours of full discharge
    sim = simulate(sig, p)
    assert sim["soc"].iloc[-1] == pytest.approx(p.soc_min, abs=1e-3)
    assert (sim["flag"] == FLAG_SOC_LOW).any()
    # once empty, delivered must be ~0 while target stays 10
    tail = sim.iloc[-100:]
    assert (tail["delivered_mw"] < 0.1).all()
    assert (tail["target_mw"] == 10.0).all()


def test_sustained_charge_hits_soc_ceiling():
    p = BatteryParams(capacity_mw=10, energy_mwh=1.0, soc_init=0.5,
                      ramp_mw_per_min=1e6)
    sig = make_signal([-1.0] * 3600)
    sim = simulate(sig, p)
    assert sim["soc"].iloc[-1] == pytest.approx(p.soc_max, abs=1e-3)
    assert (sim["flag"] == FLAG_SOC_HIGH).any()


def test_ramp_limit_binds_on_step_change():
    """A step from 0 to full power is ramp-limited when ramp is slow."""
    p = BatteryParams(capacity_mw=10, energy_mwh=1000, ramp_mw_per_min=30)
    sig = make_signal([0.0] * 5 + [1.0] * 100)
    sim = simulate(sig, p)
    # 30 MW/min = 1 MW per 2s step: reaching 10 MW takes 10 steps
    step = sim["delivered_mw"].diff().dropna()
    assert step.max() <= 1.0 + 1e-9
    assert (sim["flag"] == FLAG_RAMP).any()
    assert sim["delivered_mw"].iloc[-1] == pytest.approx(10.0)


def test_round_trip_efficiency_loses_energy():
    """Symmetric charge/discharge cycle ends with net SOC loss."""
    p = BatteryParams(capacity_mw=10, energy_mwh=20, soc_init=0.5,
                      ramp_mw_per_min=1e6, round_trip_eff=0.80)
    sig = make_signal([-0.5] * 900 + [0.5] * 900)  # 30 min charge, 30 min discharge
    sim = simulate(sig, p)
    assert sim["soc"].iloc[-1] < p.soc_init


def test_deviation_intervals_capture_soc_run():
    p = BatteryParams(capacity_mw=10, energy_mwh=0.5, soc_init=0.5,
                      ramp_mw_per_min=1e6)
    sig = make_signal([1.0] * 1800)
    sim = simulate(sig, p)
    dev = deviation_intervals(sim)
    assert not dev.empty
    assert dev.iloc[-1]["flag"] == FLAG_SOC_LOW
    assert dev["mean_shortfall_mw"].max() > 0


def test_empty_signal():
    sim = simulate(pd.Series(dtype=float))
    assert sim.empty
    assert tracking_metrics(sim) == {}
    assert deviation_intervals(sim).empty


def test_signal_clipped_to_unit_range():
    sig = make_signal([2.0, -2.0, 0.5])
    sim = simulate(sig, BatteryParams(capacity_mw=10, ramp_mw_per_min=1e6))
    assert sim["target_mw"].max() <= 10.0
    assert sim["target_mw"].min() >= -10.0
