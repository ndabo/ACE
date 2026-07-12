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


@st.cache_data(show_spinner="Fetching ACE from PJM…", ttl=120)
def fetch_ace_window(start: str, end: str) -> pd.DataFrame:
    """ACE straight from DataMiner2 (kept 30 days by PJM — no local archive)."""
    from freqreg.aggregate import aggregate
    from freqreg.pjm_api import fetch_ace

    df = fetch_ace(start, end)
    if df.empty:
        return df
    df = df.set_index("ts")
    rule = pick_resolution(pd.Timestamp(start), pd.Timestamp(end))
    return aggregate(df, rule, cols=["ace_mw"]) if rule else df


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

default_start = days[max(0, len(days) - 7)]
d_start, d_end = st.sidebar.select_slider(
    "Date range", options=days, value=(default_start, days[-1]))

st.sidebar.subheader("Battery")
cap = st.sidebar.number_input(
    "Capacity (MW)", 1.0, 500.0, 10.0, step=1.0,
    help="Cleared regulation capability. Target MW = signal × this. A ±1 "
         "signal swings the battery between full charge and full discharge.")
mwh = st.sidebar.number_input(
    "Energy (MWh)", 1.0, 2000.0, 20.0, step=1.0,
    help="Storage size. MWh ÷ MW = duration: 20 MWh / 10 MW = 2 h at full "
         "power. Smaller = hits SOC bounds sooner when the signal is one-sided.")
soc_lo, soc_hi = st.sidebar.slider(
    "SOC window", 0.0, 1.0, (0.10, 0.90), step=0.05,
    help="Usable state-of-charge band. Outside it the battery can't keep "
         "discharging (floor) or charging (ceiling). Tighter window = less "
         "usable energy = more deviations.")
ramp = st.sidebar.number_input(
    "Ramp (MW/min)", 1.0, 10000.0, 100.0, step=10.0,
    help="Max change in output per minute. Batteries are fast (default is "
         "effectively unlimited for 10 MW); lower it to see how slower assets "
         "lag the signal's sharp reversals.")
rte = st.sidebar.slider(
    "Round-trip efficiency", 0.5, 1.0, 0.86, step=0.01,
    help="Fraction of charged energy you get back out (losses split evenly "
         "between charge and discharge). Below 1.0 the SOC drifts down over a "
         "balanced signal — energy leaks every cycle.")
soc0 = st.sidebar.slider(
    "Initial SOC", 0.0, 1.0, 0.5, step=0.05,
    help="Starting state of charge. 0.5 gives equal headroom both ways; "
         "start high and a charge-heavy signal hits the ceiling almost "
         "immediately.")

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

tab_hist, tab_dev, tab_live = st.tabs(["📈 Historical", "🚧 Deviations", "⚡ ACE (last 30 days)"])


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

    from freqreg.pjm_api import ACE_RETENTION_DAYS, now_ept
    ace_cutoff = now_ept() - pd.Timedelta(days=ACE_RETENTION_DAYS)
    if end >= ace_cutoff:
        ace = fetch_ace_window(str(max(start, ace_cutoff)), str(min(end, now_ept())))
    else:
        ace = pd.DataFrame()
    if not ace.empty:
        ace_col = "ace_mw_mean" if "ace_mw_mean" in ace.columns else "ace_mw"
        fig.add_scatter(x=ace.index, y=ace[ace_col], name="ACE", row=1, col=1,
                        line=dict(color="#888", width=1))
    else:
        fig.add_annotation(text=f"ACE unavailable: PJM only keeps the last "
                                f"{ACE_RETENTION_DAYS} days, and this window is older. "
                                "See the ACE tab for the current period.",
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
    from freqreg.pjm_api import now_ept

    st.subheader("System imbalance — ACE (DataMiner2, ~15 s cadence)")
    st.caption("The regulation signal is a dispatch instruction and is not "
               "publicly broadcast live — ACE is the imbalance you CAN watch. "
               "Negative = system short (discharge territory); positive = long. "
               "PJM keeps 30 days; older ACE is gone. All times are Eastern (EPT).")
    col_a, col_b = st.columns([3, 1])
    window = col_a.select_slider(
        "Window", options=["1 h", "3 h", "6 h", "24 h", "3 d", "7 d", "14 d", "30 d"],
        value="1 h")
    n, unit = window.split()
    delta = pd.Timedelta(hours=int(n)) if unit == "h" else pd.Timedelta(days=int(n))
    # auto-refresh only makes sense for short "watching the grid" windows
    can_auto = delta <= pd.Timedelta(hours=6)
    auto = col_b.toggle("Live (30 s)", value=can_auto, disabled=not can_auto,
                        help="Re-fetches from PJM every 30 s. Available for "
                             "windows up to 6 h; PJM posts new ACE every ~15 s.")

    def _render_ace():
        now = now_ept()
        start_s = (now - delta).strftime("%Y-%m-%d %H:%M")
        end_s = now.strftime("%Y-%m-%d %H:%M")
        try:
            if auto:  # bypass the 2-min cache: small window, cheap fetch
                from freqreg.pjm_api import fetch_ace
                df = fetch_ace(start_s, end_s)
                live = df.set_index("ts") if not df.empty else df
            else:
                live = fetch_ace_window(start_s, end_s)
        except Exception as exc:  # pragma: no cover - network path
            live = pd.DataFrame()
            st.error(f"ACE fetch failed: {exc}")

        if live.empty:
            st.warning("No ACE rows returned for this window.")
            return
        ace_col = "ace_mw_mean" if "ace_mw_mean" in live.columns else "ace_mw"
        latest_ts, latest_val = live.index[-1], live[ace_col].iloc[-1]
        c1, c2 = st.columns([1, 3])
        c1.metric("Latest ACE", f"{latest_val:.0f} MW",
                  delta=("system long" if latest_val > 0 else "system short"),
                  delta_color=("normal" if latest_val > 0 else "inverse"),
                  help=f"as of {latest_ts} EPT")
        c1.metric("Window mean |ACE|", f"{live[ace_col].abs().mean():.0f} MW")
        c1.caption(f"fetched {now_ept():%H:%M:%S} EPT"
                   + (" · auto-refreshing" if auto else ""))
        fig = go.Figure()
        fig.add_scatter(x=live.index, y=live[ace_col], mode="lines",
                        line=dict(color="#d62728", width=1.2), name="ACE")
        if f"{ace_col.rsplit('_', 1)[0]}_min" in live.columns:
            fig.add_scatter(x=live.index, y=live["ace_mw_min"], mode="lines",
                            line=dict(width=0), showlegend=False)
            fig.add_scatter(x=live.index, y=live["ace_mw_max"], mode="lines",
                            line=dict(width=0), fill="tonexty",
                            fillcolor="rgba(214,39,40,.15)", name="min–max")
        fig.add_hline(y=0, line_dash="dot", line_color="#666")
        fig.update_layout(height=380, margin=dict(t=20, b=20))
        c2.plotly_chart(fig, use_container_width=True)

    if auto:
        st.fragment(run_every="30s")(_render_ace)()
    else:
        if st.button("🔄 Refresh"):
            fetch_ace_window.clear()
        _render_ace()
