# PJM Frequency Regulation Dashboard

Private Streamlit dashboard showing when PJM system imbalance (ACE) occurs and
how a constrained simulated battery tracks the regulation signal PJM derives
from it. Companion to the PJM-5CP capacity tool. Full design + data-availability
findings: [CLAUDE.md](CLAUDE.md).

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# one-time: ingest signal data (post-redesign sample days + pre-redesign archive)
.venv/bin/python scripts/backfill_signal.py sample data/raw/new-signal-sample.xlsx
.venv/bin/python scripts/backfill_signal.py zip data/raw/rto-regulation-signal-data.zip "02 2025"

# launch (optionally set FREQREG_PASSCODE for the access gate)
FREQREG_PASSCODE=yourcode .venv/bin/streamlit run app.py
```

Requires `PJM_API_KEY` in `.env` (same account as PJM-5CP).

ACE is fetched on demand from DataMiner2 (PJM's rolling 30-day window) — no
local archive by design. `scripts/archive_ace.py` remains available if that
decision is ever reversed (required before attempting Option B).

## Layout

| Path | Purpose |
|---|---|
| `freqreg/battery.py` | Constrained battery sim (SOC/ramp/efficiency) + tracking metrics + deviation intervals |
| `freqreg/ingest.py` | Parsers for PJM's signal files (sample xlsx, monthly matrix zip) |
| `freqreg/store.py` | Daily parquet store + windowed reads at auto-picked resolution |
| `freqreg/aggregate.py` | Pre-aggregation ladder (raw ≤6h, 1-min ≤2d, 5-min beyond) |
| `freqreg/pjm_api.py` | DataMiner2 client (ACE, reg_market_results), rate-limit aware |
| `freqreg/regime.py` | Pre/post Oct-2025 regime tagging (Oct-2026 Phase 2 parameterized) |
| `app.py` | Streamlit app: Historical / Deviations / Live ACE |
| `scripts/` | `archive_ace.py` (daily), `backfill_signal.py` (one-time) |

## Data reality (verified 2026-07-12)

- **ACE**: near-live on DataMiner2 (~15 s), but only 30 days retained → we archive.
- **Regulation signal**: no live feed, no DataMiner2 feed. Public data =
  pre-redesign monthly archive (2024-03..2025-02, RegD "Dynamic" sheets) plus
  two posted post-redesign sample days (2025-10-03, 2025-11-03). Both preserved
  in `data/raw/` — the archive's link was removed from pjm.com.
- **Signal regimes must never be mixed silently** — the app warns when a
  selected range spans the Oct 1, 2025 break.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```
