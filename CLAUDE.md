# PJM Frequency Regulation Dashboard

## What this project is

A companion project to the PJM-5CP tool, but for a different PJM market: **frequency regulation** (ancillary services), not capacity. The goal is a **streamlined, private** Streamlit dashboard that shows when system imbalance (ACE) occurs and how a battery's charge/discharge behavior relates to the regulation signal PJM derives from that imbalance — primarily over **historical** data, with a limited live component (see "Live data reality" below).

## Background context

- PJM keeps grid frequency at 60 Hz by measuring **Area Control Error (ACE)** — the imbalance between scheduled and actual supply/demand, calculated in MW roughly every 15 seconds. Negative ACE ≈ system short → resources discharge; positive ACE ≈ system long → resources charge.
- ACE is filtered (PI compensator) into a **regulation signal**, sent every ~2 seconds to all enrolled regulation resources. The signal is normalized to roughly ±1: positive = discharge instruction, negative = charge instruction. Each resource's target MW = signal × its cleared capacity.
- Historically split into RegA (slow) and RegD (fast, battery-oriented). **PJM merged these into a single bidirectional signal effective October 1, 2025.** A split into separate RegUp/RegDown products is planned for October 2026.
- **Hard schema break:** pre- and post-Oct 1, 2025 data are two different regimes. Never mix them in one analysis without an explicit flag.

## Data availability — constraints that shape the whole design

1. **No per-asset battery telemetry is public.** PJM treats individual generator output as confidential (at any timescale). Real SOC/response data for a specific battery is not obtainable. → We simulate.
2. **The regulation signal is likely NOT available live.** ACE is near-real-time on DataMiner2, but the 2-second regulation signal has historically been posted as **historical files with a lag (roughly monthly)** on PJM's ancillary services data pages — it is a dispatch instruction, not a public live broadcast. **This is the load-bearing assumption of the project and must be verified before any dashboard code is written** (see Step 0).
3. What IS reliably available: `area_control_error` (DataMiner2, near-live, ~15s), regulation signal historical files (lagged), `reg_market_results` (clearing prices, cleared MW, mileage).

### Live data reality — two options (decide at Step 0)

- **Option A (default): historical-first dashboard.** Full analysis runs on lagged signal files; the only live element is an ACE ticker. Simpler, honest, fully answers the core question.
- **Option B (stretch): approximate the live signal ourselves** by filtering live ACE the way PJM does (PI compensator). More interesting, but it's reverse-engineering PJM's controller — the approximation won't exactly match the real signal and can't be validated until lagged files post. Only attempt after Option A works.

## Core analytical framing — avoid the circularity trap

The relationship has two links, and only one is *our* analysis:

- **ACE → signal**: this is PJM's controller. The signal is a filtered, lagged transform of ACE. Correlating anything against raw ACE mostly measures PJM's filter, not battery behavior. **Show this link visually (overlay plot); do not build metrics on it.**
- **Signal → constrained battery response**: this is the project's actual analysis. The headline metric is **tracking error / performance-score proxy** (instructed vs. delivered MW), NOT correlation. An unconstrained simulated battery tracks the signal perfectly by construction — the finding lives in *where and why the constrained battery deviates* (SOC bounds hit, ramp-limited periods).

## Decision: simulate the battery, with constraints from day one

- Why it's reasonable: only option (telemetry is confidential); fully public inputs; clean causal chain.
- Its honest limit: without constraints the simulation is a replot of the input. The SOC/ramp/efficiency constraints are what make the deviation analysis real. Build them first, not as a later add-on.

## Build steps

### Step 0 — Verify data availability (BEFORE any dashboard code)
- [x] Confirm where the post-Oct-2025 regulation signal files are posted, their format, resolution, and actual posting lag
- [x] Confirm ACE feed access and cadence on DataMiner2 (reuse PJM-5CP API account)
- [x] Based on findings, lock Option A vs. B above and record the decision here

#### Step 0 findings (verified 2026-07-12)

**ACE feed (`area_control_error`, DataMiner2):** confirmed working with the PJM-5CP
API key. 15-second cadence, near-live (rows appear within ~2 min). **Retention is
only 30 days** (verified empirically: 22 days back has data, 37 days back is empty).
→ Historical ACE beyond 30 days is unobtainable; the data layer must archive ACE
continuously from day one. Fields: `datetime_beginning_ept`, `area` ("PJM"), `ace_mw`.

**Regulation signal (2-second):** there is NO DataMiner2 feed for the signal, and the
redesign's DataMiner2 change list adds none. The only public source is a zip on
pjm.com (`/-/media/DotCom/markets-ops/ancillary/rto-regulation-signal-data.zip`,
~484 MB, RegA/RegD pre-redesign archive). It was last modified 2025-03-19 and its
link was removed from the live ancillary-services page — i.e. **no ongoing monthly
signal postings exist post-redesign.** The only public post-Oct-2025 consolidated
signal data is the "Sample Normalized New Regulation Signal" workbook
(`sample-normalize-new-regulation-signal-for-one-day.xlsx`): two full real days
(2025-10-03, 2025-11-03) at 2-second resolution, normalized ±1, columns
`Time`, `RegA` (the consolidated signal rides the RegA path).

**reg_market_results (DataMiner2):** confirmed. 30-minute intervals post-redesign,
posted monthly (1st Friday), history to 2012. RegD columns null after 06/2025;
consolidated market data flows through `rega_*` columns. Includes hourly
`rto_perfscore` — useful reference against our simulated tracking score.

**Decision: Option A, adapted.** Historical-first dashboard built on (1) the
pre-redesign RegA/RegD zip archive for depth, (2) the two posted post-redesign
sample days as the current-regime exemplars, (3) a self-maintained rolling ACE
archive (the 30-day retention makes archiving mandatory, not optional), and
(4) reg_market_results for market context. Live element = ACE ticker only.
Option B (PI-compensator approximation of the live signal from ACE) stays
deferred; it is now the *only* possible route to post-redesign signal coverage
beyond the sample days, which raises its future value — revisit after v1.

### Step 1 — Data layer ✅ (2026-07-12)
- [x] Pull scripts: ACE feed (`freqreg/pjm_api.py`), regulation signal files (`freqreg/ingest.py`), reg_market_results
- [x] Historical backfill for arbitrary date ranges (`scripts/backfill_signal.py`, `scripts/archive_ace.py`)
- [x] **Pre-aggregation layer:** raw ≤6h, 1-min ≤2 days, 5-min beyond (`freqreg/aggregate.py`); aggregates keep mean/min/max so spikes survive
- [x] Tag every record with pre/post Oct 1, 2025 regime (`freqreg/regime.py`; Oct 2026 Phase 2 break already parameterized)
- Ingested so far: 2025-02 (28 days RegD) + both post-redesign sample days; ACE archive seeded with the full available 30 days (2026-06-13 →)

### Step 2 — Simulated battery module ✅ (2026-07-12)
- [x] Params: MW capacity, MWh, SOC min/max, ramp limit, round-trip efficiency (defaults 10 MW / 20 MWh)
- [x] Signal → target MW → constraints → delivered MW → SOC trace (`freqreg/battery.py`)
- [x] Tracking-error + precision-score proxy (instructed vs. delivered)
- [x] SOC-bound and ramp-limited interval flags + contiguous deviation events
- 8 unit tests cover each constraint in isolation (`tests/test_battery.py`)

### Step 3 — Streamlit app ✅ (2026-07-12)
- [x] **Historical view**: date-range picker, ACE + signal overlay, delivered MW + SOC trace, tracking-error metric, regime flag
- [x] **Deviation view**: highlighted intervals with constraint attribution (SOC floor / SOC ceiling / ramp)
- [x] **Live element**: ACE ticker (Option A, per Step 0 decision)
- Verified headless via streamlit AppTest: no exceptions, regime-break warning fires

### Step 4 — Deployment
- [x] Passcode gate via `FREQREG_PASSCODE` env var; runs locally (`streamlit run app.py`)
- [ ] Set up daily cron for `scripts/archive_ace.py` (line in README) — user action

## Explicitly deferred to v2 (cut from v1 to stay streamlined)
- Revenue estimation (RMCCP/RMPCP, mileage ratios, lost opportunity cost) — easy to get subtly wrong, doesn't serve the core imbalance↔battery question
- Pre/post-redesign regime comparison view
- RegUp/RegDown handling for the Oct 2026 Phase 2 change (parameterize the signal-regime logic now so this doesn't force a rewrite)

## Open questions
- Actual posting cadence/format of post-redesign signal data (Step 0 resolves)
- Battery spec defaults (10 MW / 20 MWh placeholder)
- Whether Option B (live signal approximation) is worth the accuracy tradeoff — revisit only after v1 ships
