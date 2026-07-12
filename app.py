"""
PJM Frequency Regulation Dashboard — private Streamlit app.

Three views (v1 scope, deliberately lean — see CLAUDE.md):
  1. Historical : ACE + signal overlay, battery delivered MW + SOC, tracking metrics
  2. Deviation  : intervals where the constrained battery couldn't follow, and why
  3. Live ACE   : near-real-time ACE ticker (Option A — the signal is not public live)
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from freqreg import store
from freqreg.aggregate import pick_resolution
from freqreg.battery import (
    FLAG_NONE, BatteryParams, deviation_intervals, simulate, tracking_metrics,
)
from freqreg.regime import regime_of

st.set_page_config(page_title="PJM Frequency Regulation", layout="wide")

FLAG_LABELS = {
    "soc_low": "SOC floor (battery empty)",
    "soc_high": "SOC ceiling (battery full)",
    "ramp": "Ramp-limited",
}


# --- access gate (private instance; not meant for public cloud) --------------

def _gate() -> bool:
    passcode = os.environ.get("FREQREG_PASSCODE", "")
    if not passcode:
        return True  # no passcode configured -> local use
    if st.session_state.get("authed"):
        return True
    with st.form("gate"):
        given = st.text_input("Passcode", type="password")
        if st.form_submit_button("Enter") and given == passcode:
            st.session_state["authed"] = True
            st.rerun()
    return False


if not _gate():
    st.stop()


# --- cached data access -------------------------------------------------------

@st.cache_data(show_spinner=False)
def signal_days() -> list[str]:
    return store.available_signal_days()


@st.cache_data(show_spinner="Loading signal…")
def load_signal_raw(start: str, end: str) -> pd.DataFrame:
    return store.load_signal(start, end, raw=True)


@st.cache_data(show_spinner=False)
def load_ace_window(start: str, end: str) -> pd.DataFrame:
    return store.load_ace(start, end)


@st.cache_data(show_spinner="Simulating battery…")
def run_sim(start: str, end: str, params_tuple: tuple) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = BatteryParams(*params_tuple)
    sig = load_signal_raw(start, end)
    if sig.empty:
        return pd.DataFrame(), pd.DataFrame()
    sim = simulate(sig["signal"], params)
    sim["regime"] = sig["regime"].to_numpy()
    return sim, deviation_intervals(sim)


def downsample(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
               cols: list[str]) -> pd.DataFrame:
    """Plot-friendly resolution: raw for short windows, means beyond that."""
    rule = pick_resolution(start, end)
    if rule is None or df.empty:
        return df
    return df[cols].resample(rule).mean()


# --- sidebar ------------------------------------------------------------------

days = signal_days()
if not days:
    st.error("No signal data ingested yet. Run scripts/backfill_signal.py first.")
    st.stop()

st.sidebar.title("PJM Freq Regulation")
st.sidebar.caption(f"{len(days)} day(s) of signal data available")

d_start, d_end = st.sidebar.select_slider(
    "Date range", options=days, value=(days[0], days[-1]))

st.sidebar.subheader("Battery")
cap = st.sidebar.number_input("Capacity (MW)", 1.0, 500.0, 10.0, step=1.0)
mwh = st.sidebar.number_input("Energy (MWh)", 1.0, 2000.0, 20.0, step=1.0)
soc_lo, soc_hi = st.sidebar.slider("SOC window", 0.0, 1.0, (0.10, 0.90), step=0.05)
ramp = st.sidebar.number_input("Ramp (MW/min)", 1.0, 10000.0, 100.0, step=10.0)
rte = st.sidebar.slider("Round-trip efficiency", 0.5, 1.0, 0.86, step=0.01)
soc0 = st.sidebar.slider("Initial SOC", 0.0, 1.0, 0.5, step=0.05)

params = BatteryParams(capacity_mw=cap, energy_mwh=mwh, soc_min=soc_lo,
                       soc_max=soc_hi, soc_init=soc0, ramp_mw_per_min=ramp,
                       round_trip_eff=rte)
params_tuple = (cap, mwh, soc_lo, soc_hi, soc0, ramp, rte)

start = pd.Timestamp(d_start)
end = pd.Timestamp(d_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=2)

# regime banner — never mix regimes silently (CLAUDE.md hard rule)
reg_a, reg_b = regime_of(start), regime_of(end)
if reg_a != reg_b:
    st.warning(f"⚠️ Selected range spans a market-regime break: **{reg_a}** → "
               f"**{reg_b}** (Oct 1, 2025 redesign). Metrics across the break "
               "mix two different signal definitions — interpret separately.")
else:
    st.caption(f"Signal regime: **{reg_a}**"
               + (" (single consolidated signal)" if reg_a == "consolidated"
                  else " (legacy RegD dynamic signal)" if reg_a == "rega_regd" else ""))

sim, dev = run_sim(str(start), str(end), params_tuple)
if sim.empty:
    st.error("No signal data in the selected window.")
    st.stop()

tab_hist, tab_dev, tab_live = st.tabs(["📈 Historical", "🚧 Deviations", "⚡ Live ACE"])


# --- Historical view -----------------------------------------------------------

with tab_hist:
    m = tracking_metrics(sim, params)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tracking error", f"{m['tracking_error_mw']:.2f} MW",
              help="Mean |instructed − delivered|. The headline metric — an "
                   "unconstrained battery scores 0 by construction.")
    c2.metric("Precision score (proxy)", f"{m['precision_score']:.3f}",
              help="1 − Σ|error| / Σ|instruction| — proxy for PJM's precision "
                   "component, not the official score.")
    c3.metric("Constrained intervals", f"{m['pct_constrained']:.1f} %")
    c4.metric("Deviation events", f"{len(dev)}")

    plot = downsample(sim, start, end, ["target_mw", "delivered_mw", "soc"])

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.34, 0.33, 0.33],
                        vertical_spacing=0.04,
                        subplot_titles=("ACE (MW) — PJM's imbalance → their controller "
                                        "makes the signal (context only)",
                                        "Signal → battery: instructed vs delivered MW",
                                        "State of charge"))

    ace = load_ace_window(str(start), str(end))
    if not ace.empty:
        ace_col = "ace_mw_mean" if "ace_mw_mean" in ace.columns else "ace_mw"
        fig.add_scatter(x=ace.index, y=ace[ace_col], name="ACE", row=1, col=1,
                        line=dict(color="#888", width=1))
    else:
        fig.add_annotation(text="No archived ACE for this window (DataMiner2 keeps "
                                "30 days; scripts/archive_ace.py builds history)",
                           xref="x domain", yref="y domain", x=0.5, y=0.5,
                           showarrow=False, row=1, col=1)

    fig.add_scatter(x=plot.index, y=plot["target_mw"], name="Instructed MW",
                    row=2, col=1, line=dict(color="#1f77b4", width=1))
    fig.add_scatter(x=plot.index, y=plot["delivered_mw"], name="Delivered MW",
                    row=2, col=1, line=dict(color="#ff7f0e", width=1))
    fig.add_scatter(x=plot.index, y=plot["soc"] * 100, name="SOC %",
                    row=3, col=1, line=dict(color="#2ca02c", width=1))
    fig.add_hline(y=params.soc_min * 100, row=3, col=1, line_dash="dot", line_color="red")
    fig.add_hline(y=params.soc_max * 100, row=3, col=1, line_dash="dot", line_color="red")

    fig.update_layout(height=720, margin=dict(t=60, b=20),
                      legend=dict(orientation="h", y=1.06))
    st.plotly_chart(fig, use_container_width=True)

    st.caption("The ACE→signal link is PJM's controller — shown for context only; "
               "metrics are built exclusively on the signal→battery link "
               "(see CLAUDE.md, 'circularity trap').")


# --- Deviation view -------------------------------------------------------------

with tab_dev:
    st.subheader("Where the battery could not follow the signal — and why")
    if dev.empty:
        st.success("No binding constraints in this window: the battery tracked "
                   "the signal perfectly. Try a smaller battery, tighter SOC "
                   "window, or slower ramp to surface deviations.")
    else:
        by_flag = dev.groupby("flag").agg(
            events=("flag", "size"),
            total_minutes=("duration_s", lambda s: s.sum() / 60),
            worst_shortfall_mw=("mean_shortfall_mw", "max"),
        ).rename(index=FLAG_LABELS)
        st.dataframe(by_flag.style.format({"total_minutes": "{:.1f}",
                                           "worst_shortfall_mw": "{:.2f}"}),
                     use_container_width=True)

        plot = downsample(sim, start, end, ["target_mw", "delivered_mw"])
        fig = go.Figure()
        fig.add_scatter(x=plot.index, y=plot["target_mw"], name="Instructed MW",
                        line=dict(color="#1f77b4", width=1))
        fig.add_scatter(x=plot.index, y=plot["delivered_mw"], name="Delivered MW",
                        line=dict(color="#ff7f0e", width=1))
        colors = {"soc_low": "rgba(214,39,40,.25)", "soc_high": "rgba(148,103,189,.25)",
                  "ramp": "rgba(255,127,14,.25)"}
        for _, r in dev.iterrows():
            fig.add_vrect(x0=r["start"], x1=max(r["end"], r["start"] + pd.Timedelta(seconds=30)),
                          fillcolor=colors.get(r["flag"], "rgba(120,120,120,.2)"),
                          line_width=0)
        fig.update_layout(height=420, margin=dict(t=30, b=20),
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Shaded: red = SOC floor, purple = SOC ceiling, orange = ramp-limited.")

        show = dev.copy()
        show["flag"] = show["flag"].map(FLAG_LABELS)
        show["duration_s"] = show["duration_s"].round(0)
        show["mean_shortfall_mw"] = show["mean_shortfall_mw"].round(2)
        st.dataframe(show.sort_values("mean_shortfall_mw", ascending=False),
                     use_container_width=True, height=300)


# --- Live ACE ticker -------------------------------------------------------------

with tab_live:
    st.subheader("Live Area Control Error (DataMiner2, ~15 s cadence)")
    st.caption("Option A: the regulation signal itself is a dispatch instruction "
               "and is not publicly broadcast live — only ACE is. "
               "Negative ACE = system short (discharge territory); positive = long.")
    minutes = st.slider("Window (minutes)", 15, 180, 60, step=15)
    if st.button("🔄 Refresh") or "live_ace" not in st.session_state:
        from freqreg.pjm_api import fetch_ace
        now = pd.Timestamp.now()
        try:
            st.session_state["live_ace"] = fetch_ace(
                (now - pd.Timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M"),
                now.strftime("%Y-%m-%d %H:%M"))
        except Exception as exc:  # pragma: no cover - network path
            st.session_state["live_ace"] = pd.DataFrame()
            st.error(f"ACE fetch failed: {exc}")
    live = st.session_state.get("live_ace", pd.DataFrame())
    if not live.empty:
        latest = live.iloc[-1]
        c1, c2 = st.columns([1, 3])
        c1.metric("Latest ACE", f"{latest['ace_mw']:.0f} MW",
                  help=f"as of {latest['ts']}")
        fig = go.Figure()
        fig.add_scatter(x=live["ts"], y=live["ace_mw"], mode="lines",
                        line=dict(color="#d62728", width=1.2), name="ACE")
        fig.add_hline(y=0, line_dash="dot", line_color="#666")
        fig.update_layout(height=380, margin=dict(t=20, b=20))
        c2.plotly_chart(fig, use_container_width=True)
