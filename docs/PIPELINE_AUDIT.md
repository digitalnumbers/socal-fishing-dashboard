# Pipeline Safety Audit

This inventory records the pre-automation behavior found at revision `d8556c69a83993d472d5459f40ca003e9542df43`. It identifies each script's inputs, outputs, side effects, runtime needs, and daily suitability before behavior was changed.

## Script inventory

| Script | Inputs and network | Outputs and side effects | Dependencies/runtime | Daily safety decision |
| --- | --- | --- | --- | --- |
| `fetch_raw.py` | Config; NDBC history/realtime, CO-OPS, full MUR, CPC, CUTI, NWS, chlorophyll | Direct legacy raw-file overwrites; creates raw directory on import | Standard library; parallel, potentially very long | Unsafe: hard-coded paths, repeated history, parallel MUR, fixed UTC-7, swallowed errors |
| `fetch_raw2.py` | Same helpers; full MUR, current grids, forecasts, CO-OPS | Direct legacy raw overwrites; runs on import | Standard library; six workers | Unsafe and redundant; not scheduled |
| `fetch_raw3.py` | `ndbc` or `coops`; historical yearly/monthly calls | Legacy historical raw files | Standard library; six workers | Bootstrap/backfill only |
| `fetch_coops.py` | Start/end years and products; CO-OPS monthly requests | Legacy monthly JSON | Standard library; up to ten workers | Backfill only; not daily |
| `fetch_forecast.py` | Zones; Open-Meteo and NWS | Paired near-term raw JSON | Standard library; five workers | Functionality retained in orchestrator with atomic paired validation |
| `fetch_mur.py` | Mode/zone slice; CoastWatch ERDDAP | Year-named MUR chunks, grid, extras | Standard library; sequential, long timeouts | Unsafe: year-only size cache froze advancing data; replaced by dated incremental overlap |
| `fill_mur.py` | Existing MUR chunks; ERDDAP gaps | Additional historical chunks | Standard library; sequential | Manual gap repair only |
| `parse_dock_totals.py` | Pre-created third-party page JSON | Rewrites `catch_reports.json` | Standard library | Unsafe/unreproducible; replaced by bounded fetch, parser check, merge, and LKG |
| `fetch_extended.py` | Date/raw path; CPC ZIPs, tides, Open-Meteo | Extracted CPC directories and extended raw JSON | Standard library; serial, over ten minutes possible | Unsafe persistent use: old CPC files accumulated and failures exited zero |
| `lib_parse.py` | Raw files and config | In-memory DataFrames; import-time config reads | pandas, NumPy | Safe transformation after paths are injected; malformed raw must be staged/validated |
| `build_dataset.py` | Raw inputs, config, catch history | Base CSVs and run metadata | pandas, NumPy; CPU-heavy | Scoring preserved; now runs only in staging with retained baselines |
| `build_extended.py` | Base CSVs, extended raws, scoring source block | Seven extended CSVs and metadata | pandas, NumPy, pyshp | Methodology preserved; now uses clean current CPC staging and atomic CSV writes |
| `gen_docs.py` | Source config and generated schemas | Registry/dictionary docs and JSON | pandas | Deterministic packaging step; portable and atomic |
| `export_xlsx.py` | Generated CSVs/docs/meta | Workbook | pandas, openpyxl | Safe only in staging; unsafe self-copy behavior removed |
| `build_site.py` | Generated CSVs/docs/meta/status | `app/data.js` | pandas, NumPy | Safe only in staging; now includes source status |
| `gen_extended_docs.py` | Source config and provenance | Mutates registry/docs/README | Standard library | Schema/release maintenance, not daily |
| `append_extended_xlsx.py` | Workbook and extended CSVs | Replaces extended workbook sheets | pandas, openpyxl | Idempotent in staging; path contracts made portable |
| `inject_extended.py` | Base data.js and extended CSVs | Adds extended payload keys | pandas | Validated and now atomically replaces data.js |
| `stage_downloads.py` | Workbook, docs, all CSV/JSON artifacts | Populates `app/downloads` | Standard library | Safe in staging; final hashes are validated |
| `patch_config.py` | Legacy zone/species JSON | Rewrites config | Standard library | One-time migration only; never daily |
| `run_erddap.sh` | Legacy hard-coded directory | Runs MUR fetch modes and prints `ALLDONE` | Shell and Python | Unsafe: no fail-fast behavior; retired from daily operation |

## Material findings

- Base scripts and extended scripts used incompatible path contracts; no end-to-end command existed.
- MUR's year-named cache never advanced after the first successful current-year file.
- CPC extraction accumulated dated files while the builder selected the oldest match.
- Several fetchers reported success after child failures.
- Generated CSV, workbook, downloads, and site payload were written independently without a publication boundary.
- The actual distribution contains 31 CSVs and a 31-sheet workbook, not the older documented count of 24.
- The static site is `app/index.html`, `app/app.js`, and generated `app/data.js`; it performs no runtime data fetch.
- Historical acquisition, config patching, and extended documentation mutation are maintenance tasks, not daily tasks.

## Replacement decision

The daily workflow does not schedule any legacy fetch script. `run_daily_refresh.py` implements bounded acquisition, local-day state, retries, status reporting, staging, existing scoring/build calls, validation, and promotion. The legacy scripts remain available for deliberate historical repair, but production automation has a single supported entry point documented in `docs/DAILY_REFRESH.md`.
