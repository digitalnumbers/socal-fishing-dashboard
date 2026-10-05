# SoCal Fishing Intelligence

A San Diego–focused predictive fishing dashboard for inshore, nearshore and offshore Southern California
waters, including Mexican-side banks. It scores species-specific fishing opportunity per zone per day,
explains the environmental drivers behind each score, compares current conditions against the historical
day-of-year norm, and adjusts for the present ENSO regime.

Data build: **2026-08-28 14:00 UTC** · 9 zones · 15 species · 21-day window (14 days hindcast + 7 forecast)

## Contents

| Path | What it is |
| --- | --- |
| `app/` | The interactive dashboard (static — `index.html` + `app.js` + `data.js`, Chart.js from CDN) |
| `dataset/socal_fishing_dataset.xlsx` | 31-sheet workbook: every cleaned source table and calculation |
| `dataset/csv/` | 31 CSV tables plus machine-readable build/source metadata |
| `docs/SOURCE_REGISTRY.md` | All 13 data sources with endpoint, coverage, cadence, licence and limitations |
| `docs/DATA_DICTIONARY.md` | 287 field definitions plus the scoring model and reproduction steps |
| `pipeline/` | The fetch, build, export and site-generation scripts, and the zone/species/source config |
| `data/` | Forecast-learning ledgers: immutable forecast issuances, raw and normalized outcomes, model registry, evaluation and run log |
| `docs/FORECAST_LEARNING.md` | Methodology and data dictionary for forecast verification, backtesting and model governance |

## Daily automation

The production-safe entry point is:

```bash
python pipeline/run_daily_refresh.py
```

Validate the current clean-clone snapshot without network access or repository changes:

```bash
python pipeline/run_daily_refresh.py --dry-run
```

GitHub Actions evaluates 08:30 Pacific UTC candidates for both PST and PDT, gates them with `America/Los_Angeles`, and runs once per successful Pacific calendar date. Delayed GitHub jobs may catch up later the same day instead of being rejected by a narrow clock window. The refresh fetches only bounded recent or due source windows, retains last-known-good data for honest degraded operation, validates a complete staged generation, commits meaningful state, and deploys `app/` only after every gate passes. See [Daily Refresh Operations](docs/DAILY_REFRESH.md) and [Pipeline Safety Audit](docs/PIPELINE_AUDIT.md).

## Forecast learning and accuracy

Each daily refresh freezes the day's forecasts in an append-only, hash-chained ledger
(`data/forecast_ledger/`). Dock totals for the last few report dates are added to an
append-only raw outcome store, and labels are derived from it under strict effort rules:
no reports never counts as a poor bite. The run then scores Model v1 against a seasonal
climatology baseline, an ENSO/SST-regime baseline and shadow challengers, using
walk-forward evaluation over the as-issued records only. Results appear on the
dashboard's **Forecast Accuracy** tab, with sample-size warnings and a "why this
forecast changed" view.

The live Bite Score is never retrained automatically. Model v1 stays the production
champion until a challenger passes the registry gate, receives a recorded human
approval, and is implemented in a reviewed code change
(`python pipeline/model_registry.py --help`). See
[Forecast Learning](docs/FORECAST_LEARNING.md).

## Zones

| Zone | Band | Distance | Notes |
| --- | --- | --- | --- |
| San Diego Bay | inshore | 0 nm | shore thermistor 9410170 |
| Mission Bay | inshore | 0 nm | shore thermistor 9410170 |
| Del Mar to Imperial Beach Surf | inshore | 0 nm | shore thermistor 9410230 |
| La Jolla Kelp & Canyon | nearshore | 1 nm | |
| Point Loma Kelp | nearshore | 2 nm | |
| 9-Mile Bank & 43-Fathom | offshore | 9 nm | |
| Coronado Islands | offshore | 18 nm | **Mexican waters** — FMM permit + Mexican fishing licence required |
| 302 / 371 / Hidden Bank | offshore | 32 nm | |
| Cortez & Tanner Bank | offshore | 95 nm | **Mexican waters** for the Cortez side |

## Baselines

Anomalies and percentiles are measured against a day-of-year climatology built with a ±7-day window:

- **Satellite SST (MUR 1 km):** 2015–2026 per zone point
- **Shore thermistor (NOAA CO-OPS):** 2015–2026
- **Buoy wave, wind and sea temperature (NDBC):** 2003–2026

The satellite record is shorter than the full MUR era (2002–present) because each zone-year is a separate
sequential ERDDAP request; the buoy archive carries the long baseline.

## Known gaps and limitations

- **Validation is preliminary.** Skill is measured against 923 species-rows from 13 days of public dock
  totals — a single late-summer window. 5 of 12 species show positive rank agreement. This is a sanity
  check, not a validated forecast skill score.
- **CDFW CPFV logbook data is confidential**, so observed catch comes from public dock totals, which
  reflect where boats chose to go and how long they fished as much as how the fish bit.
- **Upwelling (CUTI) is missing** — the ERDDAP `erdCUTI` endpoint returned 404.
- **Chlorophyll is missing** — the dataset ID could not be resolved, so the chlorophyll driver is dropped
  from the geometric mean rather than guessed at.
- **Forecast days use modelled SST** (Open-Meteo), not observation; satellite lags ~2 days.
- San Diego Bay and Mission Bay share tide station 9410170, so their shore water temperature is identical.
- Every score is decision support, not a guarantee. Verify CDFW seasons and limits, and Mexican permit
  requirements, before fishing.

<!-- BEGIN extended-range (generated by pipeline/gen_extended_docs.py) -->
## 30-Day Outlook

The dashboard now carries a fourth view, **30-Day Outlook**, extending the horizon from 7 to 30 days in three explicitly labelled confidence tiers:

| Tier | Lead | What it is | What it shows |
| --- | --- | --- | --- |
| High confidence | Days 1-7 | Observed conditions plus deterministic marine and atmospheric forecast. **Unchanged** by this addition. | Specific daily SST, swell, wind, tide and score |
| Moderate confidence | Days 8-14 | Deterministic model decayed toward day-of-year climatology, plus the CPC 8-14 day outlook, plus exact tide and moon | Daily score with an uncertainty band; wind blended; swell only where the marine model still reaches |
| Outlook / trend only | Days 15-30 | Climatology weighted with the ENSO analog composite, plus CPC week 3-4 and monthly outlooks, plus exact tide and moon | Trend versus the seasonal norm and a tide/moon quality signal. **No daily wind or swell numbers.** |

Days 15-30 deliberately publish no daily wind or swell figures, because no public extended marine forecast has useful skill at that range. Field-by-field provenance is in `dataset/csv/extended_field_provenance.csv` and `docs/SOURCE_REGISTRY.md`.

The daily orchestrator refreshes the extended inputs and rebuilds these tables automatically. Run
`python pipeline/run_daily_refresh.py --force --full-rebuild` for an on-demand recomputation; use the
legacy individual scripts only for deliberate historical repair or schema maintenance.
<!-- END extended-range -->
