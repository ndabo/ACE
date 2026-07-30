# PJM ACE Dashboard

Live monitor of PJM's **Area Control Error (ACE)** — the near-real-time MW
imbalance between scheduled and actual supply/demand. Negative = system short
(discharge territory); positive = system long. PJM retains roughly the last
30 days of ACE; older data is gone.

## Scope of this deploy

This branch is the **Live ACE only** build. The regulation-signal analysis
(Historical performance + Deviation attribution for a simulated battery) is
deliberately left out for now — there is no public live regulation signal, so it
can only run on limited historical files. That work lives on the `development`
branch, pending review.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
FREQREG_PASSCODE=yourcode .venv/bin/streamlit run app.py
```

Requires a PJM DataMiner2 API key. Locally, put `PJM_API_KEY=...` in a `.env`
file at the project root (same account as PJM-5CP).

## Deploy (Streamlit Community Cloud)

This is an internal tool — always set a passcode when deploying.

1. Point the app at `app.py` on this branch.
2. In **App → Settings → Secrets**, paste (see `.streamlit/secrets.toml.example`):
   ```toml
   FREQREG_PASSCODE = "a-strong-passcode"
   PJM_API_KEY      = "your-pjm-api-key"
   ```
   The access gate reads `FREQREG_PASSCODE` from `st.secrets` first, then the
   environment. **If it is unset the app is unguarded** — never deploy without it.

The dashboard fetches ACE on demand from DataMiner2 (no local data needed), so
it works on the ephemeral cloud filesystem out of the box.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Streamlit app: passcode gate + live ACE view |
| `freqreg/pjm_api.py` | DataMiner2 client (ACE), rate-limit aware, EPT time helper |
| `freqreg/aggregate.py` | Resolution ladder so long windows stay light to plot |
