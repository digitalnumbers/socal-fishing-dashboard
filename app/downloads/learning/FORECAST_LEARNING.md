# Forecast learning, backtesting and model governance

This document is the methodology and data dictionary for the forecast-learning system
added on 2026-10-04. It adds an audit trail around the existing Bite Score. It does
**not** change how the live score is calculated: the production model is still the
original engine, registered as **Model v1**.

## What the system does each day

`pipeline/run_daily_refresh.py` runs these steps after the normal build and source
checks, all inside the staging tree. Nothing is promoted unless the whole build passes
validation.

1. **Collect raw outcomes.** Dock-total pages for report dates `today-3 … today` are
   read again, because boats update their counts late. Every new trip version is
   appended to `data/outcome_raw/`. Existing rows are never rewritten.
2. **Freeze today's forecast.** The forecasts just built (days 0–6 from
   `scores_daily.csv`, days 7–30 from `extended_scores.csv`) are written as one
   write-once issuance file in `data/forecast_ledger/`. The file records every input
   feature, driver term, source timestamp and data-quality flag as they were at issue
   time.
3. **Rebuild normalized labels.** `data/outcome_ledger.csv` is derived from the raw
   trips using the frozen label definition (L1). The file is deterministic, so
   rebuilding it from the same raw data gives the same result.
4. **Walk-forward evaluation.** Each stored forecast is paired with the label for its
   target day. Model v1, the two baselines and any shadow challengers are scored from
   the same as-issued records (`data/model_evaluation.csv/.json`).
5. **"Why this forecast changed".** The latest issuance is compared with the one
   before it (`data/forecast_changes_latest.csv`).
6. **Log.** A run summary is appended to `data/update_log.json`.

The system never retrains, recalibrates or replaces the live Bite Score on its own.
`learning_common.ENGINE_MODEL_VERSION` is `"v1"`. Validation fails if the registry
names any other production model.

## Leakage rules (no future information)

- Forecast records are written once, at issue time, and are never overwritten. Each
  index row stores the SHA-256 of its file and of the previous index row (a hash
  chain). `validate_build.py` fails the build if any earlier issuance file or index
  row changes or disappears.
- Each issuance stores `data_cutoff_utc`, which is the time the run started.
  Validation fails if any of its source timestamps (`src_*`) is later than the
  issuance's `issue_utc`.
- Evaluation uses only stored, as-issued scores and terms. Forecasts are never
  recomputed from data that was revised later.
- A lead-0 forecast issued after 12:00 local time is excluded, because the day was
  already being fished when it was issued.
- Calibration challengers are refitted at every issuance. Each fit uses only labels
  whose `available_at_utc` is before that issuance's `issue_utc` (expanding window).
- Outcome labels keep `available_at_utc`, the first time the supporting raw report
  was collected, so a backtest can tell which labels existed when.
- At leads of 15 days or more, the ledger stores no deterministic daily swell, wind or
  wave values (`inputs_json` excludes them). Validation enforces this.

## Forecast-horizon rules

| Lead | Treatment | Confidence tier |
|---|---|---|
| 0–7 days | Observed and forecast marine conditions, weighted heavily | High (0–6), Moderate (7) |
| 8–14 days | Blended model and climatology; weather precision reduced; wider band | Moderate |
| 15–30 days | Probabilistic seasonal/regime outlook: calendar seasonality, ENSO analog anomaly, tide and moon timing. No daily swell or wind is issued or evaluated | Outlook |

Each forecast's probability of a Good-or-better day is
`p_good_or_better = logistic((score − 60) / spread)`. The spread depends on the lead
bucket (8 / 10 / 14 / 20 points for 0–3 / 4–7 / 8–14 / 15–30 days), so far-off
forecasts can never claim the sharp probabilities of short-range ones. This
probability is what the Brier score and the reliability diagram evaluate.

## Outcome labels (label version L1)

Dock totals measure where boats chose to go and how many anglers fished as much as
they measure how the fish bit. The label rules are deliberately conservative:

- **Raw reports and labels are kept separate.** The raw reports in
  `data/outcome_raw/` are append-only and versioned by `raw_id`, a hash of the trip's
  content. A late update to a trip adds a new version, and the latest version per
  boat-trip is used.
- **Trip → region.** Coronado trips go to `coronados`. 1.5-day and "offshore" trips go
  to `offshore_banks`. Overnight trips also go to `offshore_banks`, flagged
  `overnight_destination_uncertain`. 2- and 2.5-day trips go to `extended_offshore`.
  ½-day, ¾-day, twilight and "local" trips go to `kelp_nearshore`. Trips of 3 days or
  more are excluded as out of domain. A plain "Full Day" trip is excluded because its
  destination is ambiguous. Bays and the surf zone have no reliable public effort
  signal, so they are not labelled.
- **Fishing date.** Multi-day trips are split across their fishing days (report date
  minus 1, 2, …), with catch and effort shared out by fishing day
  (`multi_day_split`).
- **Effort.** Angler-days = anglers × trip length in days (½ day = 0.5).
  `catch_index = (kept + released) / angler_days` for each species, region and
  fishing day.
- **No reports means no label.** A region-day needs at least 2 eligible trips and 30
  angler-days of effort (`effort_sufficient`). Below that, no class is assigned
  (`insufficient_effort`).
- **Effort-adjusted negative signal.** Zero catch can only become a Poor label when
  effort is sufficient **and** the species has its own frozen species-region reference
  (it was demonstrably caught in that region during the reference window). Without
  that reference, the row stays unlabelled (`no_reference`).
- **Normalization.** `index_ratio = catch_index / reference_index`. The
  `reference_index` is the median daily index for 2026-08-15 … 2026-08-27, frozen in
  `data/label_reference_L1.csv`. That window comes before every forecast target that
  can be verified. The ratio is mapped piecewise-linearly to a 0–100
  `observed_score` through the knots (0, 0), (0.25, 40), (0.6, 60), (1.2, 75), (2.0, 90)
  and (4.0, 100). The score is then classed with the same cuts as the Bite Score:
  Poor < 40 ≤ Fair < 60 ≤ Good < 75 ≤ Very Good < 90 ≤ Excellent.
- **Reporting delay.** A label stays `provisional` until 3 days after the fishing date,
  then becomes `final`. Labels change only when new raw versions arrive.
  `reporting_lag_days` is report date minus fishing date. `publication_lag_days` is
  the time from the fishing date until the report was first collected. Backfilled
  labels show a large publication lag and are flagged `backfilled`.
- Changing any of these rules means creating label version L2. L1 labels and their
  reference stay as they are.

## Baselines and models compared

| Id | Role | Definition |
|---|---|---|
| `baseline_climatology` | A | The v1 engine run on day-of-year climatological SST, wave and wind, with neutral tide and moon terms (`climatological_curve.csv`). |
| `baseline_enso` | B | A, shifted by the zone × month × ENSO-regime composite SST anomaly through each species' anomaly preference. Uses the regime known at issue time. |
| `v1` | C | Production champion: the as-issued Bite Score. |
| challengers | D | Shadow models. They are evaluated offline and never published as the live score. |

The registry ships with one challenger, `v1.1-cal`. It is a walk-forward linear
recalibration (`observed ≈ a + b·v1`) for each horizon group (0–7 and 8–30 days). It
stays identical to v1 until 60 training labels exist before an issuance. Weight
challengers (`kind: weights`) re-score the stored as-issued driver terms with new
weights, using the same engine mathematics (`learning_common.rescore_from_terms`).

## Metrics

- `exact_hit`: share of forecasts whose bite class equals the observed class.
- `within_one`: share of forecasts within one class of the observed class.
- `mae`: mean absolute error between the 0–100 score and `observed_score`.
- `brier`: mean of (p_good_or_better − observed_good_or_better)².
- `precision_good` / `recall_good`: precision and recall for Good-or-better calls.
  Shown only when there are at least 10 forecast and 10 observed Good-or-better events.
- `brier_skill_vs_clim` = 1 − Brier / Brier(A). `mae_skill_vs_clim` is defined the
  same way for MAE.
- The reliability diagram uses 5 probability bins for each horizon group.
- The bootstrap is a block bootstrap by target date (400 replicates). It gives a 90%
  interval and P(skill > 0) for v1's Brier skill.

Results are reported by window (rolling 30 days, rolling 90 days, season to date
from 1 March, and all) and by horizon group (0–7 / 8–30 days). They are also broken
down by lead bucket (0–3, 4–7, 8–14, 15–30), species, region, month, meteorological
season, ENSO regime at issue, and label status.

**Sample-size rules.** A stratum is *insufficient* when it has fewer than 30 pairs,
fewer than 10 target days or fewer than 3 issuances. It is *preliminary* when it has
fewer than 100 pairs, fewer than 30 target days or fewer than 10 issuances. Otherwise
it is *reportable*. Pairs from the same issuance or the same day are strongly
correlated, so issuances and days count as well as raw pairs. The dashboard fades
insufficient strata and withholds their skill.

## Governance and promotion

`data/model_registry.json` records each model's id, status, role, feature set,
weights, training window, evaluation window, changes, latest performance, promotion
decision and human-approval record. It also holds the append-only `decisions` list
and the governance criteria. A challenger is only recommended for promotion when all
of these hold:

- At least 300 pairs, 45 target days and 30 issuances.
- Aggregate Brier improves by at least 2%, with bootstrap P(improvement) ≥ 0.9.
- MAE is no worse than v1 + 0.5 points.
- No priority species (yellowtail, yellowfin tuna, bluefin tuna, calico bass,
  rockfish) or priority region (kelp & nearshore, offshore banks) with at least 30
  pairs has a Brier degradation above 5%.
- Short range (leads 0–3 and 4–7) loses no more than 2% Brier and no more than
  2 points of exact-hit rate.

Workflow (`pipeline/model_registry.py`):

```
propose  --id v1.2-wt --kind weights --weights-file w.json --description "..."   # shadow
review   --id v1.2-wt                         # records the gate result
approve  --id v1.2-wt --by "Name" --note ""   # human approval flag (needs passing review)
promote  --id v1.2-wt                         # refuses unless approved AND the engine implements it
```

Promotion takes a reviewed code change that implements the model in the engine and
sets `ENGINE_MODEL_VERSION`. The daily job cannot do it.

## Data dictionary

### `data/forecast_ledger/index.csv`
`issuance_id` (e.g. `2026-10-03T1619PT_v1`), `issue_utc`, `data_cutoff_utc`,
`issue_local_date`, `origin` (`live_daily_refresh` or `recovered_git_snapshot`),
`source_ref` (run start or git commit), `model_version`, `schema_version`, `n_rows`,
`lead_min`, `lead_max`, `file`, `file_sha256`, `content_sha256`,
`prev_index_row_sha256` (hash chain) and `created_utc`.

### `data/forecast_ledger/YYYY-MM/<issuance_id>.csv.gz` (one row per target day × zone × species)
| Column | Meaning |
|---|---|
| `record_id` | `issuance_id|target_date|zone_id|species_id` |
| `issue_utc`, `issue_local`, `issue_local_date`, `data_cutoff_utc` | When the forecast was issued and its data cutoff |
| `target_date`, `lead_days`, `lead_bucket`, `horizon_tier`, `confidence_key`, `confidence_label` | Target day and horizon |
| `zone_id`, `region_id`, `species_id` | Zone, outcome region (`ZONE_PRIMARY_REGION`) and species |
| `model_version`, `bite_score` (0–100), `bite_class`, `p_good_or_better`, `score_lo`, `score_hi` | Forecast output (band only for leads of 7 days or more) |
| `baseline_clim_score`, `baseline_enso_score`, `baseline_enso_anom_f`, `enso_regime`, `oni` | Baselines A/B and the ENSO state at issue |
| `core_score`, `fishability`, `anomaly_term`, `term_sst … term_chl`, `missing_drivers` | Engine internals, enough to re-score exactly |
| `basis`, `sst_f`, `sst_anom_f`, `sst_source`, `wave_ft`, `wind_kt`, `tide_range_ft`, `moon_illum`, `pressure_trend_24h`, `front_f_per_nm` | Key inputs (sea state is blank at leads of 15 days or more) |
| `inputs_json` | Every input feature for that zone-day, as issued |
| `src_*` | Newest source timestamps or fetch times, plus the list of failed sources |
| `dq_flags` | `;`-separated flags, e.g. `mur_failed`, `sources_failed=N`, `missing_drivers`, `sst_modelled_not_observed`, `lead0_issued_after_local_noon`, `shared_bay_thermistor`, `no_outcome_source`, `no_daily_sea_state`, `fishability_default`, `model_decayed_toward_climatology`, `no_source_timestamps`, `recovered_git_snapshot` |

### `data/outcome_raw/dock_trips_YYYY-MM.csv` (append-only)
`raw_id`, `report_date`, `landing`, `boat`, `trip_type`, `trip_type_raw`,
`trip_days`, `anglers`, `kept_json`, `released_json`, `tracked_fish`, `raw_text`,
`source`, `source_url`, `fetched_at_utc`, `ingest_method` (`backfill` or
`daily_refresh`) and `parser_version`. Trips with zero catch are kept, because they
are effort. `fetch_log.csv` records every page fetch (`ok`, `n_trips`, `raw_ids`,
`error`).

### `data/outcome_ledger.csv` (normalized labels)
| Column | Meaning |
|---|---|
| `label_id`, `label_version`, `fishing_date`, `region_id`, `species_id` | Key |
| `n_trips`, `anglers`, `angler_days`, `fish_kept`, `fish_released`, `fish_total` | Effort and catch on eligible trips |
| `catch_index`, `reference_index`, `index_ratio` | Normalized catch index |
| `observed_score`, `observed_class`, `observed_good_or_better` | Training label (blank unless labelled) |
| `effort_sufficient`, `label_status` | `final`, `provisional`, `insufficient_effort` or `no_reference` |
| `reporting_lag_days`, `available_at_utc`, `publication_lag_days`, `report_dates` | Delay and availability |
| `source`, `n_raw_records`, `dq_flags` | Provenance. Flags include `backfilled`, `multi_day_split`, `low_effort`, `effort_adjusted_zero`, `overnight_destination_uncertain`, `reference_default`, `reference_species_fallback`, `sparse_species`, `in_reference_window` |

`data/label_reference_L1.csv` holds the frozen reference medians with their `basis`
and `n_days`.

### `data/model_evaluation.csv` / `.json`
One row per `window × dimension × stratum × model_id`: `n_pairs`, `n_target_dates`,
`n_issuances`, `sample_flag`, `exact_hit`, `within_one`, `mae`, `brier`,
`brier_skill_vs_clim`, `mae_skill_vs_clim`, `precision_good`, `recall_good`,
`n_obs_good`, `n_fcst_good`, `obs_good_rate`, `mean_forecast_score` and
`mean_observed_score`. The JSON adds the ledger and label summaries, pair counts, the
reliability bins, the bootstrap, a 60-day observed-vs-forecast timeline, the
challenger gate checks and recommendations, and the sample rules.

### `data/model_registry.json`
`production_model`, `engine_model_version`, `governance` (auto_retrain = false,
human_approval_required = true, priority species and regions, promotion_criteria,
review cadence), `models[]` and the append-only `decisions[]`.

### `data/forecast_changes_latest.csv`
`current_issuance`, `prior_issuance`, `target_date`, `zone_id`, `species_id`,
`lead_now`, `lead_prior`, `tier_now`, `tier_prior`, `score_prior`, `score_now`,
`delta`, `class_prior`, `class_now`, `top_drivers`, `driver_points_json`,
`input_changes` and `notes`. Driver points break the score change down by driver in
the engine's log space: each driver contributes `w_k/Σw · Δln(term_k)`, plus
`Δln(anomaly_term)` and `Δln(0.35 + 0.65·fishability)`. The pieces are rescaled so
they add up to the actual change, including the soft-ceiling compression.

### `data/update_log.json`
An append-only list of runs: `run_utc`, `local_date`, `mode`, outcome ingest counts,
issuance id, label counts, evaluation summary, challenger recommendations, changes
count, `live_model_changed` (always false) and warnings.

## Initial state and known limitations (2026-10-04)

- The ledger did not exist before. Two genuinely issued forecast sets were recovered
  from committed git snapshots: 2026-08-28 (commit `d8556c6`) and 2026-10-03 (commit
  `c625edf`). Hindcast rows were never recorded as forecasts. Until about 30 daily
  issuances have accumulated, every result is a pipeline check, not a skill estimate.
- Outcomes for 2026-08-15 … 2026-10-03 were backfilled from archived dock pages on
  2026-10-04. They are flagged `backfilled`, and their `available_at_utc` is the
  collection time.
- The L1 reference comes from a single 13-day late-summer window. Seasonal shifts in
  targeting and effort are not modelled yet.
- Dock totals have no zone-level location and mix in effort and targeting decisions.
  Overnight-trip destinations are uncertain, and Coronado trips are rare.
- The 2026-10-03 issuance ran with MUR SST and NDBC 46235 failed. Its flags record
  that.
