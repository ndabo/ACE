"""
PJM ACE Dashboard — live system-imbalance monitor.

Shows PJM's Area Control Error (ACE): the near-real-time MW imbalance between
scheduled and actual supply/demand. Negative = system short (discharge
territory); positive = system long. PJM retains roughly the last 30 days.

Scope note: the regulation-signal analysis (Historical / Deviations views) is
intentionally omitted from this deploy — there is no public live regulation
signal, so this build focuses on the imbalance you CAN watch. That work lives on
the `development` branch pending review.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from freqreg.aggregate import aggregate, pick_resolution
from freqreg.pjm_api import fetch_ace, now_ept

st.set_page_config(page_title="PJM ACE Dashboard", layout="wide")


# --- access gate (internal tool; passcode required when deployed) -------------

def _configured_passcode() -> str:
    """Passcode from Streamlit secrets (cloud) or env var (local); '' if unset."""
    try:
        if "FREQREG_PASSCODE" in st.secrets:
            return str(st.secrets["FREQREG_PASSCODE"])
    except Exception:
        pass  # no secrets.toml present -> fall through to env var
    return os.environ.get("FREQREG_PASSCODE", "")


def _gate() -> bool:
    passcode = _configured_passcode()
    if not passcode:
        return True  # no passcode configured -> unguarded (local dev only)
    if st.session_state.get("authed"):
        return True
    st.title("PJM ACE Dashboard")
    with st.form("gate"):
        given = st.text_input("Passcode", type="password")
        submitted = st.form_submit_button("Enter")
    if submitted:
        if given == passcode:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect passcode.")
    return False


if not _gate():
    st.stop()


# --- data access --------------------------------------------------------------

@st.cache_data(show_spinner="Fetching ACE from PJM…", ttl=120)
def fetch_ace_window(start: str, end: str) -> pd.DataFrame:
    """ACE straight from DataMiner2 (kept ~30 days by PJM — no local archive)."""
    df = fetch_ace(start, end)
    if df.empty:
        return df
    df = df.set_index("ts")
    rule = pick_resolution(pd.Timestamp(start), pd.Timestamp(end))
    return aggregate(df, rule, cols=["ace_mw"]) if rule else df


# --- Live ACE -----------------------------------------------------------------

st.title("⚡ PJM System Imbalance — ACE")
st.caption("Area Control Error from DataMiner2 (~15 s cadence). The regulation "
           "signal is a dispatch instruction and is not publicly broadcast live "
           "— ACE is the imbalance you CAN watch. Negative = system short "
           "(discharge territory); positive = long. PJM keeps 30 days; older ACE "
           "is gone. All times are Eastern (EPT).")

col_a, col_b = st.columns([3, 1])
window = col_a.select_slider(
    "Window", options=["1 h", "3 h", "6 h", "24 h", "3 d", "7 d", "14 d", "30 d"],
    value="1 h")
n, unit = window.split()
delta = pd.Timedelta(hours=int(n)) if unit == "h" else pd.Timedelta(days=int(n))
# auto-refresh only makes sense for short "watching the grid" windows
can_auto = delta <= pd.Timedelta(hours=6)
auto = col_b.toggle("Live (30 s)", value=can_auto, disabled=not can_auto,
                    help="Re-fetches from PJM every 30 s. Available for windows "
                         "up to 6 h; PJM posts new ACE every ~15 s.")


def _render_ace():
    now = now_ept()
    start_s = (now - delta).strftime("%Y-%m-%d %H:%M")
    end_s = now.strftime("%Y-%m-%d %H:%M")
    try:
        if auto:  # bypass the 2-min cache: small window, cheap fetch
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
