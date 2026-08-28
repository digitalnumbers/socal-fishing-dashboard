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
| `dataset/socal_fishing_dataset.xlsx` | 24-sheet workbook: every cleaned source table and every calculation |
| `dataset/csv/` | The same 24 tables as individual CSVs |
| `docs/SOURCE_REGISTRY.md` | All 13 data sources with endpoint, coverage, cadence, licence and limitations |
| `docs/DATA_DICTIONARY.md` | 287 field definitions plus the scoring model and reproduction steps |
| `pipeline/` | The fetch, build, export and site-generation scripts, and the zone/species/source config |

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
