# Data Dictionary — SoCal Fishing Intelligence

Field-level definitions for every table in `socal_fishing_dataset.xlsx` and the CSV exports.
Units are US customary (°F, ft, kt, nm) because that is how the underlying fishing decisions are made.

## `conditions_daily`

Zone-day environmental fact table: every driver the model reads, plus its historical norm and anomaly.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `wave_ft` | float | ft | Significant wave height (daily maximum of the marine model). |
| `swell_ft` | float | ft | Swell-partition wave height. |
| `wave_period_s` | float | s | Peak wave period. |
| `swell_period_s` | float | s | Swell-partition period. |
| `wave_dir_deg` | float | ° true | Dominant wave direction (coming from). |
| `sst_f_model` | float | °F | Open-Meteo marine model SST (mean of daily max and min). |
| `wind_kt` | float | kt | Daily maximum 10 m wind speed. |
| `gust_kt` | float | kt | Daily maximum wind gust. |
| `wind_dir_deg` | float | ° true | Dominant wind direction (coming from). |
| `pressure_hpa` | float | hPa | Mean sea-level pressure. |
| `cloud_pct` | float | % | Mean cloud cover. |
| `air_temp_f` | float | °F | Daily maximum 2 m air temperature. |
| `precip_mm` | float | mm | Daily precipitation total. |
| `pressure_trend_24h` | float | hPa/24 h | Change in mean sea-level pressure from the previous day. |
| `doy` | int | 1–366 | Day of year used to join day-of-year climatologies. |
| `month` | int | 1–12 | Calendar month. |
| `sst_f_sat` | float | °F | MUR 1 km satellite SST at the zone's open-water sampling point. |
| `sst_f` | float | °F | Zone sea surface temperature actually used in scoring: shore thermistor inshore, MUR satellite offshore, marine model as last resort. |
| `sst_source` | string | — | Which feed supplied sst_f for that zone-day. |
| `sst_norm` | float | °F | Day-of-year climatological mean SST from the matching baseline (±7-day window). |
| `sst_sd` | float | °F | Standard deviation of the day-of-year SST sample. |
| `sst_p10` | float | °F | 10th percentile of the day-of-year SST sample. |
| `sst_p50` | float | °F | Median of the day-of-year SST sample. |
| `sst_p90` | float | °F | 90th percentile of the day-of-year SST sample. |
| `sst_n` | int | days | Number of observations in the day-of-year window. |
| `sst_years` | int | years | Distinct years contributing to that climatology. |
| `tide_station` | string | — | NOAA CO-OPS station identifier. |
| `tide_high_ft` | — | — | Derived column; see build_dataset.py. |
| `tide_low_ft` | — | — | Derived column; see build_dataset.py. |
| `tide_range_ft` | float | ft | Highest high minus lowest low predicted tide for the day. |
| `tide_exchanges` | — | — | Derived column; see build_dataset.py. |
| `max_exchange_rate_ft_h` | float | ft/h | Largest mean vertical exchange rate between consecutive tide extremes. |
| `best_water_hour` | float | local h | Midpoint of the strongest tidal exchange of the day. |
| `first_high_hour` | float | local h | Decimal hour of the first predicted high tide. |
| `shore_sst_f` | float | °F | CO-OPS shore thermistor daily mean water temperature. |
| `shoresst_norm` | — | — | Derived column; see build_dataset.py. |
| `shoresst_sd` | — | — | Derived column; see build_dataset.py. |
| `shoresst_p10` | — | — | Derived column; see build_dataset.py. |
| `shoresst_p50` | — | — | Derived column; see build_dataset.py. |
| `shoresst_p90` | — | — | Derived column; see build_dataset.py. |
| `shoresst_years` | — | — | Derived column; see build_dataset.py. |
| `shore_sst_anom_f` | float | °F | Shore thermistor departure from its own day-of-year normal. |
| `sst_anom_f` | float | °F | sst_f minus sst_norm: the headline 'warmer or colder than normal' figure. |
| `sst_pctile` | float | 0–100 | Percentile of today's SST within the day-of-year distribution, from a normal fit to its mean and SD. |
| `sst_trend_7d` | float | °F | Change in sst_f over the previous 7 days. |
| `primary_buoy` | string | — | NDBC buoy used as the zone's wave/wind climatology reference. |
| `wave_norm` | float | ft | Day-of-year climatological wave height at the reference buoy. |
| `wave_p90` | — | — | Derived column; see build_dataset.py. |
| `wind_norm` | float | kt | Day-of-year climatological wind speed at the reference buoy. |
| `wind_p90` | — | — | Derived column; see build_dataset.py. |
| `buoysst_norm` | — | — | Derived column; see build_dataset.py. |
| `buoysst_years` | — | — | Derived column; see build_dataset.py. |
| `wave_years` | — | — | Derived column; see build_dataset.py. |
| `wave_anom_ft` | float | ft | wave_ft minus wave_norm. |
| `wind_anom_kt` | float | kt | wind_kt minus wind_norm. |
| `front_f_per_nm` | float | °F/nm | Horizontal SST gradient magnitude near the zone from the MUR grid: temperature-break strength. |
| `sst_spread_f` | float | °F | Range of SST within the sampled neighbourhood around the zone. |
| `chl_mg_m3` | float | mg/m³ | Satellite chlorophyll-a concentration (not resolved in this build). |
| `cuti` | float | m²/s | Coastal Upwelling Transport Index (not resolved in this build). |
| `offshore_minus_nearshore_f` | float | °F | SST at San Clemente Basin (46086) minus Point Loma South (46232): a cross-shore gradient proxy. |
| `moon_illum` | float | 0–1 | Fraction of the lunar disc illuminated. |
| `moon_phase` | string | — | Named lunar phase. |
| `moon_age_days` | float | days | Days since the previous new moon. |
| `sunrise_hour` | float | local h | Decimal hour of sunrise. |
| `sunset_hour` | float | local h | Decimal hour of sunset. |
| `daylight_hours` | float | h | Sunset minus sunrise. |
| `oni_latest` | float | °C | Most recent ONI value at build time, attached to every row for convenience. |
| `enso_regime` | string | — | Detailed regime in force at build time. |
| `enso_simple` | string | — | Collapsed regime in force at build time. |
| `enso_season` | — | — | Derived column; see build_dataset.py. |
| `is_forecast` | bool | — | True when the row is in the forward forecast window. |

## `scores_daily`

Zone-species-day scores with driver decomposition, seasonal norm and forecast flag.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `zone` | — | — | Derived column; see build_dataset.py. |
| `band` | string | — | inshore, nearshore or offshore. |
| `species_id` | string | — | Species identifier; joins to dim_species. |
| `species` | string | — | Common name. |
| `group` | string | — | Functional group used for chlorophyll and front handling. |
| `score` | float | 0–100 | Opportunity score: weighted geometric mean of driver suitabilities, adjusted for SST anomaly preference and gated by access. |
| `seasonal_norm_score` | float | 0–100 | The same model run on climatological drivers with neutral tide and moon: what is typical for this date and place. |
| `vs_norm` | float | points | score minus seasonal_norm_score: today's departure from typical. |
| `core_score` | float | 0–100 | Score before the access gate is applied. |
| `fishability` | float | 0–100 | Small-boat access gate from wind, wave height, wave period and run distance. |
| `access_note` | string | — | Plain-language reason the access gate was reduced. |
| `is_forecast` | bool | — | True when the row is in the forward forecast window. |
| `sst_f` | float | °F | Zone sea surface temperature actually used in scoring: shore thermistor inshore, MUR satellite offshore, marine model as last resort. |
| `sst_anom_f` | float | °F | sst_f minus sst_norm: the headline 'warmer or colder than normal' figure. |
| `wave_ft` | float | ft | Significant wave height (daily maximum of the marine model). |
| `wind_kt` | float | kt | Daily maximum 10 m wind speed. |
| `tide_range_ft` | float | ft | Highest high minus lowest low predicted tide for the day. |
| `moon_illum` | float | 0–1 | Fraction of the lunar disc illuminated. |
| `front_f_per_nm` | float | °F/nm | Horizontal SST gradient magnitude near the zone from the MUR grid: temperature-break strength. |
| `term_sst` | float | 0–1 | Temperature suitability from the species trapezoidal response curve. |
| `term_season` | float | 0–1 | Monthly seasonal presence index for the species. |
| `term_tide` | float | 0–1 | Tide strength and timing suitability for the species tide preference. |
| `term_moon` | float | 0–1 | Lunar suitability for the species moon preference. |
| `term_pressure` | float | 0–1 | Barometric-trend suitability (slow falls score highest). |
| `term_swell` | float | 0–1 | Swell suitability relative to the species tolerance. |
| `term_front` | float | 0–1 | Temperature-break strength suitability (pelagics only in practice). |
| `term_chl` | float | 0–1 | Chlorophyll suitability (null in this build). |
| `anomaly_term` | float | 0.35–1.35 | ENSO/anomaly multiplier from the species anomaly preference and the SST anomaly. |
| `contrib_json` | json | log units | Per-driver weighted log-odds contribution relative to a neutral 0.5 suitability; drives the 'why' bars. |
| `missing_drivers` | string | — | Weighted drivers that had no data for that row. |
| `enso_regime` | string | — | Detailed regime in force at build time. |
| `oni` | float | °C | CPC Oceanic Niño Index, 3-month running mean SST anomaly in Niño 3.4. |
| `annual_percentile` | float | 0–100 | Rank of today's score within that zone-species climatological annual distribution. |

## `climatological_curve`

Climatological score for every day of the year, per zone and species: the seasonal opportunity curve.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `species_id` | string | — | Species identifier; joins to dim_species. |
| `doy` | int | 1–366 | Day of year used to join day-of-year climatologies. |
| `month` | int | 1–12 | Calendar month. |
| `climatological_score` | float | 0–100 | Score computed entirely from climatological drivers for that day of year. |
| `sst_norm_f` | float | °F | Climatological SST used to compute the climatological score curve. |
| `wave_norm_ft` | — | — | Derived column; see build_dataset.py. |
| `wind_norm_kt` | — | — | Derived column; see build_dataset.py. |
| `climatology_source` | string | — | Which climatology baseline supplied the SST for the curve. |

## `sst_climatology`

Day-of-year satellite SST climatology per zone (mean, SD, percentiles, sample size).

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `doy` | int | 1–366 | Day of year used to join day-of-year climatologies. |
| `sst_norm` | float | °F | Day-of-year climatological mean SST from the matching baseline (±7-day window). |
| `sst_sd` | float | °F | Standard deviation of the day-of-year SST sample. |
| `sst_p10` | float | °F | 10th percentile of the day-of-year SST sample. |
| `sst_p50` | float | °F | Median of the day-of-year SST sample. |
| `sst_p90` | float | °F | 90th percentile of the day-of-year SST sample. |
| `sst_n` | int | days | Number of observations in the day-of-year window. |
| `sst_years` | int | years | Distinct years contributing to that climatology. |

## `shore_sst_climatology`

Day-of-year shore-thermistor SST climatology per CO-OPS station.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `tide_station` | string | — | NOAA CO-OPS station identifier. |
| `doy` | int | 1–366 | Day of year used to join day-of-year climatologies. |
| `shoresst_norm` | — | — | Derived column; see build_dataset.py. |
| `shoresst_sd` | — | — | Derived column; see build_dataset.py. |
| `shoresst_p10` | — | — | Derived column; see build_dataset.py. |
| `shoresst_p50` | — | — | Derived column; see build_dataset.py. |
| `shoresst_p90` | — | — | Derived column; see build_dataset.py. |
| `shoresst_n` | — | — | Derived column; see build_dataset.py. |
| `shoresst_years` | — | — | Derived column; see build_dataset.py. |

## `shore_sst_daily`

Daily shore-thermistor water temperature aggregates.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `tide_station` | string | — | NOAA CO-OPS station identifier. |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `shore_sst_f` | float | °F | CO-OPS shore thermistor daily mean water temperature. |
| `shore_sst_min_f` | — | — | Derived column; see build_dataset.py. |
| `shore_sst_max_f` | — | — | Derived column; see build_dataset.py. |
| `n_obs` | int | count | Underlying observations in the aggregate. |

## `buoy_climatology`

Day-of-year wave, wind and sea-temperature climatology per NDBC buoy.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `station` | string | — | NDBC buoy station identifier. |
| `doy` | int | 1–366 | Day of year used to join day-of-year climatologies. |
| `wave_norm` | float | ft | Day-of-year climatological wave height at the reference buoy. |
| `wave_sd` | — | — | Derived column; see build_dataset.py. |
| `wave_p10` | — | — | Derived column; see build_dataset.py. |
| `wave_p50` | — | — | Derived column; see build_dataset.py. |
| `wave_p90` | — | — | Derived column; see build_dataset.py. |
| `wave_n` | — | — | Derived column; see build_dataset.py. |
| `wave_years` | — | — | Derived column; see build_dataset.py. |
| `wind_norm` | float | kt | Day-of-year climatological wind speed at the reference buoy. |
| `wind_sd` | — | — | Derived column; see build_dataset.py. |
| `wind_p10` | — | — | Derived column; see build_dataset.py. |
| `wind_p50` | — | — | Derived column; see build_dataset.py. |
| `wind_p90` | — | — | Derived column; see build_dataset.py. |
| `wind_n` | — | — | Derived column; see build_dataset.py. |
| `wind_years` | — | — | Derived column; see build_dataset.py. |
| `buoysst_norm` | — | — | Derived column; see build_dataset.py. |
| `buoysst_sd` | — | — | Derived column; see build_dataset.py. |
| `buoysst_p10` | — | — | Derived column; see build_dataset.py. |
| `buoysst_p50` | — | — | Derived column; see build_dataset.py. |
| `buoysst_p90` | — | — | Derived column; see build_dataset.py. |
| `buoysst_n` | — | — | Derived column; see build_dataset.py. |
| `buoysst_years` | — | — | Derived column; see build_dataset.py. |

## `buoy_daily`

Daily NDBC buoy aggregates from the realtime and historical archives.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `station` | string | — | NDBC buoy station identifier. |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `wind_kt` | float | kt | Daily maximum 10 m wind speed. |
| `wind_kt_max` | — | — | Derived column; see build_dataset.py. |
| `gust_kt_max` | — | — | Derived column; see build_dataset.py. |
| `wave_ft` | float | ft | Significant wave height (daily maximum of the marine model). |
| `wave_ft_max` | — | — | Derived column; see build_dataset.py. |
| `swell_period_s` | float | s | Swell-partition period. |
| `swell_dir_deg` | — | — | Derived column; see build_dataset.py. |
| `pressure_hpa` | float | hPa | Mean sea-level pressure. |
| `pressure_hpa_min` | — | — | Derived column; see build_dataset.py. |
| `buoy_sst_f` | float | °F | NDBC buoy sea temperature daily mean. |
| `air_temp_f` | float | °F | Daily maximum 2 m air temperature. |
| `wind_dir_deg` | float | ° true | Dominant wind direction (coming from). |
| `pressure_trend_24h` | float | hPa/24 h | Change in mean sea-level pressure from the previous day. |

## `mur_history`

Daily MUR satellite SST per zone sampling point.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `sst_f` | float | °F | Zone sea surface temperature actually used in scoring: shore thermistor inshore, MUR satellite offshore, marine model as last resort. |

## `tide_daily`

Daily tide features derived from harmonic predictions.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `tide_station` | string | — | NOAA CO-OPS station identifier. |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `tide_high_ft` | — | — | Derived column; see build_dataset.py. |
| `tide_low_ft` | — | — | Derived column; see build_dataset.py. |
| `tide_range_ft` | float | ft | Highest high minus lowest low predicted tide for the day. |
| `tide_exchanges` | — | — | Derived column; see build_dataset.py. |
| `max_exchange_rate_ft_h` | float | ft/h | Largest mean vertical exchange rate between consecutive tide extremes. |
| `best_water_hour` | float | local h | Midpoint of the strongest tidal exchange of the day. |
| `first_high_hour` | float | local h | Decimal hour of the first predicted high tide. |

## `enso_oni`

Monthly CPC ONI with regime classification.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `season` | string | — | CPC three-letter overlapping season label (e.g. JJA). |
| `year` | — | — | Derived column; see build_dataset.py. |
| `sst_c` | — | — | Derived column; see build_dataset.py. |
| `oni` | float | °C | CPC Oceanic Niño Index, 3-month running mean SST anomaly in Niño 3.4. |
| `month` | int | 1–12 | Calendar month. |
| `regime` | string | — | Detailed ENSO classification: strong/moderate/weak El Niño, neutral, or La Niña equivalents. |
| `simple_regime` | string | — | Collapsed ENSO classification: el_nino, neutral or la_nina. |

## `enso_current`

The ENSO state in force at build time.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `season` | string | — | CPC three-letter overlapping season label (e.g. JJA). |
| `year` | — | — | Derived column; see build_dataset.py. |
| `oni` | float | °C | CPC Oceanic Niño Index, 3-month running mean SST anomaly in Niño 3.4. |
| `regime` | string | — | Detailed ENSO classification: strong/moderate/weak El Niño, neutral, or La Niña equivalents. |
| `simple_regime` | string | — | Collapsed ENSO classification: el_nino, neutral or la_nina. |
| `trailing_12mo_mean_oni` | float | °C | Mean ONI over the last 12 months: regime persistence. |

## `enso_zone_composite`

Mean zone SST anomaly by month and ENSO regime, from this project's own satellite record.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `month` | int | 1–12 | Calendar month. |
| `simple_regime` | string | — | Collapsed ENSO classification: el_nino, neutral or la_nina. |
| `mean_anom_f` | float | °F | Mean zone SST anomaly observed in that ENSO regime and month. |
| `sd_anom_f` | float | °F | Standard deviation of that composite sample. |
| `n_days` | int | days | Sample size. |

## `enso_sensitivity`

Regression of zone SST anomaly on ONI by month.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `month` | int | 1–12 | Calendar month. |
| `anom_f_per_oni` | float | °F per °C | Regression slope of zone SST anomaly on ONI for that month: local ENSO sensitivity. |
| `r` | float | −1–1 | Pearson correlation for that regression. |
| `n_days` | int | days | Sample size. |

## `catch_reports`

Boat-trip level dock totals normalized to species identifiers.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `landing` | string | — | Sportfishing landing reporting the trip. |
| `boat` | string | — | Vessel name. |
| `trip_type` | string | — | Reported trip length category. |
| `anglers` | int | count | Anglers aboard. |
| `species_id` | string | — | Species identifier; joins to dim_species. |
| `kept` | int | fish | Fish reported kept. |
| `released` | int | fish | Fish reported released. |
| `zone_hint` | string | — | Zone inferred from the trip description, where stated. |
| `source_url` | string | — | Page the report was read from. |
| `trip_days` | float | days | Trip length in days used to normalize effort. |
| `cpue` | float | fish/angler | Fish kept per angler. |
| `cpue_per_angler_day` | float | fish/angler-day | Fish kept per angler per day of trip length: the validation target. |

## `catch_daily_cpue`

Daily catch per angler and per angler-day by species.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `species_id` | string | — | Species identifier; joins to dim_species. |
| `kept` | int | fish | Fish reported kept. |
| `released` | int | fish | Fish reported released. |
| `anglers` | int | count | Anglers aboard. |
| `angler_days` | float | angler-days | Anglers × trip length. |
| `trips` | int | count | Boat trips contributing to the row. |
| `cpue` | float | fish/angler | Fish kept per angler. |
| `cpue_per_angler_day` | float | fish/angler-day | Fish kept per angler per day of trip length: the validation target. |

## `model_validation`

Preliminary skill check of scores against observed CPUE.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `species_id` | string | — | Species identifier; joins to dim_species. |
| `species` | string | — | Common name. |
| `n_days` | int | days | Sample size. |
| `spearman_rho` | float | −1–1 | Rank correlation between daily best score and normalized CPUE. |
| `pearson_r` | float | −1–1 | Linear correlation of the same pair. |
| `spearman_rho_raw_cpue` | float | −1–1 | Rank correlation against un-normalized fish per angler. |
| `mean_cpue_per_angler_day` | float | fish/angler-day | Mean observed normalized CPUE over the compared days. |
| `mean_score` | float | 0–100 | Mean modeled score over the compared days. |
| `total_fish` | int | fish | Fish in the compared sample. |
| `total_anglers` | int | anglers | Anglers in the compared sample. |

## `dim_zones`

Zone dimension: geography, depth, run distance, waters and reference stations.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `id` | string | — | Zone identifier. |
| `name` | string | — | Human-readable zone name. |
| `band` | string | — | inshore, nearshore or offshore. |
| `lat` | float | °N | Zone centroid latitude. |
| `lon` | float | °E | Zone centroid longitude. |
| `depth_ft` | — | — | Derived column; see build_dataset.py. |
| `distance_nm` | float | nm | Approximate run from Point Loma. |
| `waters` | string | — | us or mexico. |
| `buoys` | string | — | Reference NDBC buoys, comma separated. |
| `tide_station` | string | — | NOAA CO-OPS station identifier. |
| `structure` | string | — | Dominant habitat or structure. |
| `permit` | string | — | Permit requirement note. |
| `sst_lat` | float | °N | Latitude of the open-water pixel used for satellite SST. |
| `sst_lon` | float | °E | Longitude of the open-water pixel used for satellite SST. |
| `depth_mid_ft` | float | ft | Representative depth used for the near-bottom temperature approximation. |

## `dim_species`

Species dimension: full response-curve parameters, weights, notes and sources.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `species_id` | string | — | Species identifier; joins to dim_species. |
| `species` | string | — | Common name. |
| `group` | string | — | Functional group used for chlorophyll and front handling. |
| `temp_reference` | string | — | Whether the temperature window applies to surface or near-bottom temperature. |
| `sst_min_f` | float | °F | Lower bound of the trapezoidal temperature response. |
| `sst_opt_lo_f` | float | °F | Start of the optimum temperature plateau. |
| `sst_opt_hi_f` | float | °F | End of the optimum temperature plateau. |
| `sst_max_f` | float | °F | Upper bound of the trapezoidal temperature response. |
| `anomaly_pref` | float | −1–1 | Preference for warm (positive) or cold (negative) SST anomalies. |
| `tide_pref` | string | — | Tide regime the species is scored against. |
| `moon_pref` | string | — | Lunar regime the species is scored against. |
| `swell_tolerance_ft` | float | ft | Wave height at which the swell term begins to fall away sharply. |
| `depth_min_ft` | float | ft | Shallow end of the species' usual depth range. |
| `depth_max_ft` | float | ft | Deep end of the species' usual depth range. |
| `zones` | string | — | Zones in which the species is modeled. |
| `season_index_monthly` | string | 0–1 × 12 | Monthly seasonal presence index, January first. |
| `weights_json` | json | — | Driver weights used in the weighted geometric mean. |
| `notes` | string | — | Behavioural notes behind the parameters. |
| `sources` | string | — | Reference URLs for the species parameters. |

## `fronts`

Temperature-break strength derived from the MUR regional grid.

| Field | Type | Units | Definition |
| --- | --- | --- | --- |
| `zone_id` | string | — | Fishing zone identifier; joins to dim_zones. |
| `date` | date | local date | Calendar date in America/Los_Angeles. |
| `front_f_per_nm` | float | °F/nm | Horizontal SST gradient magnitude near the zone from the MUR grid: temperature-break strength. |
| `front_mean_f_per_nm` | — | — | Derived column; see build_dataset.py. |
| `sst_spread_f` | float | °F | Range of SST within the sampled neighbourhood around the zone. |

## Scoring model

The opportunity score is a weighted geometric mean of driver suitabilities, each on a 0–1 scale:

```
core  = exp( Σ wᵢ · ln(termᵢ) / Σ wᵢ ) × anomaly_term
raw   = core × (0.35 + 0.65 × fishability)
score = soft_ceiling(raw) × 100     # soft_ceiling(v) = v for v ≤ 0.90,
                                   #   else 0.90 + 0.10 · (1 − e^(−(v−0.90)/0.10))
```

- A geometric mean is used so that a single disqualifying driver (water far outside the species' window, or a
  species out of season) suppresses the score rather than being averaged away.
- `anomaly_term = clamp(1 + anomaly_pref × (sst_anom_f / 2.5) × 0.45, 0.35, 1.35)` applies the current climate
  regime: warm-water species gain in a warm anomaly, cold-water species lose.
- `fishability` is a separate access gate from wind, wave height, wave period and run distance, so an unfishable
  day never shows a high score even when the biology is perfect. It never zeroes a score completely (floor 0.35)
  because conditions can improve within a day.
- `seasonal_norm_score` re-runs the same model on climatological SST, wave and wind with neutral tide and moon
  terms, which is what makes 'better or worse than typical for this date' a like-for-like comparison.
- `annual_percentile` ranks the day against that zone-species climatological curve across all 366 days.
- `soft_ceiling` compresses the top of the range instead of clipping it. Without it, a strong warm
  anomaly pushes several species past 1.0 and they all tie at exactly 100, which destroys ranking
  information at the top. The mapping is strictly monotonic, so ordering is preserved and 100 is
  approached but never reached.

## Reproducing the build

```bash
python fetch_raw.py        # buoys, tides, ONI, NWS
python fetch_coops.py 2015 2026 water_temperature
python fetch_forecast.py   # per-zone marine + atmospheric forecast
python fetch_mur.py zones 0 9   # satellite SST, strictly sequential
python build_dataset.py    # features, climatologies, scores, validation
python export_xlsx.py      # workbook + CSV bundle
python build_site.py       # dashboard payload
```
