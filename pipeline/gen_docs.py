"""Generate SOURCE_REGISTRY.md, DATA_DICTIONARY.md and the dictionary JSON for the dashboard."""
import json, os, glob
import pandas as pd

BASE = "/home/user/workspace/socal"
OUT = f"{BASE}/data/out"
DOC = f"{BASE}/docs"
os.makedirs(DOC, exist_ok=True)
SRC = json.load(open(f"{BASE}/config/sources.json"))

DEF = {
 # keys
 "zone_id": ("string", "—", "Fishing zone identifier; joins to dim_zones."),
 "species_id": ("string", "—", "Species identifier; joins to dim_species."),
 "date": ("date", "local date", "Calendar date in America/Los_Angeles."),
 "doy": ("int", "1–366", "Day of year used to join day-of-year climatologies."),
 "month": ("int", "1–12", "Calendar month."),
 "station": ("string", "—", "NDBC buoy station identifier."),
 "tide_station": ("string", "—", "NOAA CO-OPS station identifier."),
 "primary_buoy": ("string", "—", "NDBC buoy used as the zone's wave/wind climatology reference."),
 # temperature
 "sst_f": ("float", "°F", "Zone sea surface temperature actually used in scoring: shore thermistor inshore, MUR satellite offshore, marine model as last resort."),
 "sst_source": ("string", "—", "Which feed supplied sst_f for that zone-day."),
 "sst_f_sat": ("float", "°F", "MUR 1 km satellite SST at the zone's open-water sampling point."),
 "sst_f_model": ("float", "°F", "Open-Meteo marine model SST (mean of daily max and min)."),
 "shore_sst_f": ("float", "°F", "CO-OPS shore thermistor daily mean water temperature."),
 "shore_sst_anom_f": ("float", "°F", "Shore thermistor departure from its own day-of-year normal."),
 "sst_norm": ("float", "°F", "Day-of-year climatological mean SST from the matching baseline (±7-day window)."),
 "sst_sd": ("float", "°F", "Standard deviation of the day-of-year SST sample."),
 "sst_p10": ("float", "°F", "10th percentile of the day-of-year SST sample."),
 "sst_p50": ("float", "°F", "Median of the day-of-year SST sample."),
 "sst_p90": ("float", "°F", "90th percentile of the day-of-year SST sample."),
 "sst_n": ("int", "days", "Number of observations in the day-of-year window."),
 "sst_years": ("int", "years", "Distinct years contributing to that climatology."),
 "sst_anom_f": ("float", "°F", "sst_f minus sst_norm: the headline 'warmer or colder than normal' figure."),
 "sst_pctile": ("float", "0–100", "Percentile of today's SST within the day-of-year distribution, from a normal fit to its mean and SD."),
 "sst_trend_7d": ("float", "°F", "Change in sst_f over the previous 7 days."),
 "buoy_sst_f": ("float", "°F", "NDBC buoy sea temperature daily mean."),
 "offshore_minus_nearshore_f": ("float", "°F", "SST at San Clemente Basin (46086) minus Point Loma South (46232): a cross-shore gradient proxy."),
 "sst_norm_f": ("float", "°F", "Climatological SST used to compute the climatological score curve."),
 # waves / wind / pressure
 "wave_ft": ("float", "ft", "Significant wave height (daily maximum of the marine model)."),
 "swell_ft": ("float", "ft", "Swell-partition wave height."),
 "wave_period_s": ("float", "s", "Peak wave period."),
 "swell_period_s": ("float", "s", "Swell-partition period."),
 "wave_dir_deg": ("float", "° true", "Dominant wave direction (coming from)."),
 "wave_norm": ("float", "ft", "Day-of-year climatological wave height at the reference buoy."),
 "wave_anom_ft": ("float", "ft", "wave_ft minus wave_norm."),
 "wind_kt": ("float", "kt", "Daily maximum 10 m wind speed."),
 "gust_kt": ("float", "kt", "Daily maximum wind gust."),
 "wind_dir_deg": ("float", "° true", "Dominant wind direction (coming from)."),
 "wind_norm": ("float", "kt", "Day-of-year climatological wind speed at the reference buoy."),
 "wind_anom_kt": ("float", "kt", "wind_kt minus wind_norm."),
 "pressure_hpa": ("float", "hPa", "Mean sea-level pressure."),
 "pressure_trend_24h": ("float", "hPa/24 h", "Change in mean sea-level pressure from the previous day."),
 "cloud_pct": ("float", "%", "Mean cloud cover."),
 "air_temp_f": ("float", "°F", "Daily maximum 2 m air temperature."),
 "precip_mm": ("float", "mm", "Daily precipitation total."),
 # tide / astronomy
 "tide_range_ft": ("float", "ft", "Highest high minus lowest low predicted tide for the day."),
 "max_exchange_rate_ft_h": ("float", "ft/h", "Largest mean vertical exchange rate between consecutive tide extremes."),
 "first_high_hour": ("float", "local h", "Decimal hour of the first predicted high tide."),
 "first_low_hour": ("float", "local h", "Decimal hour of the first predicted low tide."),
 "best_water_hour": ("float", "local h", "Midpoint of the strongest tidal exchange of the day."),
 "n_extremes": ("int", "count", "Number of predicted tide extremes for the day."),
 "moon_illum": ("float", "0–1", "Fraction of the lunar disc illuminated."),
 "moon_phase": ("string", "—", "Named lunar phase."),
 "moon_age_days": ("float", "days", "Days since the previous new moon."),
 "sunrise_hour": ("float", "local h", "Decimal hour of sunrise."),
 "sunset_hour": ("float", "local h", "Decimal hour of sunset."),
 "daylight_hours": ("float", "h", "Sunset minus sunrise."),
 # ocean structure
 "front_f_per_nm": ("float", "°F/nm", "Horizontal SST gradient magnitude near the zone from the MUR grid: temperature-break strength."),
 "sst_spread_f": ("float", "°F", "Range of SST within the sampled neighbourhood around the zone."),
 "chl_mg_m3": ("float", "mg/m³", "Satellite chlorophyll-a concentration (not resolved in this build)."),
 "cuti": ("float", "m²/s", "Coastal Upwelling Transport Index (not resolved in this build)."),
 # ENSO
 "oni": ("float", "°C", "CPC Oceanic Niño Index, 3-month running mean SST anomaly in Niño 3.4."),
 "oni_latest": ("float", "°C", "Most recent ONI value at build time, attached to every row for convenience."),
 "season": ("string", "—", "CPC three-letter overlapping season label (e.g. JJA)."),
 "regime": ("string", "—", "Detailed ENSO classification: strong/moderate/weak El Niño, neutral, or La Niña equivalents."),
 "simple_regime": ("string", "—", "Collapsed ENSO classification: el_nino, neutral or la_nina."),
 "enso_regime": ("string", "—", "Detailed regime in force at build time."),
 "enso_simple": ("string", "—", "Collapsed regime in force at build time."),
 "trailing_12mo_mean_oni": ("float", "°C", "Mean ONI over the last 12 months: regime persistence."),
 "mean_anom_f": ("float", "°F", "Mean zone SST anomaly observed in that ENSO regime and month."),
 "sd_anom_f": ("float", "°F", "Standard deviation of that composite sample."),
 "n_days": ("int", "days", "Sample size."),
 "anom_f_per_oni": ("float", "°F per °C", "Regression slope of zone SST anomaly on ONI for that month: local ENSO sensitivity."),
 "r": ("float", "−1–1", "Pearson correlation for that regression."),
 # scores
 "score": ("float", "0–100", "Opportunity score: weighted geometric mean of driver suitabilities, adjusted for SST anomaly preference and gated by access."),
 "seasonal_norm_score": ("float", "0–100", "The same model run on climatological drivers with neutral tide and moon: what is typical for this date and place."),
 "vs_norm": ("float", "points", "score minus seasonal_norm_score: today's departure from typical."),
 "annual_percentile": ("float", "0–100", "Rank of today's score within that zone-species climatological annual distribution."),
 "core_score": ("float", "0–100", "Score before the access gate is applied."),
 "fishability": ("float", "0–100", "Small-boat access gate from wind, wave height, wave period and run distance."),
 "access_note": ("string", "—", "Plain-language reason the access gate was reduced."),
 "is_forecast": ("bool", "—", "True when the row is in the forward forecast window."),
 "climatological_score": ("float", "0–100", "Score computed entirely from climatological drivers for that day of year."),
 "climatology_source": ("string", "—", "Which climatology baseline supplied the SST for the curve."),
 "term_sst": ("float", "0–1", "Temperature suitability from the species trapezoidal response curve."),
 "term_season": ("float", "0–1", "Monthly seasonal presence index for the species."),
 "term_tide": ("float", "0–1", "Tide strength and timing suitability for the species tide preference."),
 "term_moon": ("float", "0–1", "Lunar suitability for the species moon preference."),
 "term_pressure": ("float", "0–1", "Barometric-trend suitability (slow falls score highest)."),
 "term_swell": ("float", "0–1", "Swell suitability relative to the species tolerance."),
 "term_front": ("float", "0–1", "Temperature-break strength suitability (pelagics only in practice)."),
 "term_chl": ("float", "0–1", "Chlorophyll suitability (null in this build)."),
 "anomaly_term": ("float", "0.35–1.35", "ENSO/anomaly multiplier from the species anomaly preference and the SST anomaly."),
 "contrib_json": ("json", "log units", "Per-driver weighted log-odds contribution relative to a neutral 0.5 suitability; drives the 'why' bars."),
 "missing_drivers": ("string", "—", "Weighted drivers that had no data for that row."),
 # species / zone dims
 "species": ("string", "—", "Common name."), "group": ("string", "—", "Functional group used for chlorophyll and front handling."),
 "temp_reference": ("string", "—", "Whether the temperature window applies to surface or near-bottom temperature."),
 "sst_min_f": ("float", "°F", "Lower bound of the trapezoidal temperature response."),
 "sst_opt_lo_f": ("float", "°F", "Start of the optimum temperature plateau."),
 "sst_opt_hi_f": ("float", "°F", "End of the optimum temperature plateau."),
 "sst_max_f": ("float", "°F", "Upper bound of the trapezoidal temperature response."),
 "anomaly_pref": ("float", "−1–1", "Preference for warm (positive) or cold (negative) SST anomalies."),
 "tide_pref": ("string", "—", "Tide regime the species is scored against."),
 "moon_pref": ("string", "—", "Lunar regime the species is scored against."),
 "swell_tolerance_ft": ("float", "ft", "Wave height at which the swell term begins to fall away sharply."),
 "depth_min_ft": ("float", "ft", "Shallow end of the species' usual depth range."),
 "depth_max_ft": ("float", "ft", "Deep end of the species' usual depth range."),
 "season_index_monthly": ("string", "0–1 × 12", "Monthly seasonal presence index, January first."),
 "weights_json": ("json", "—", "Driver weights used in the weighted geometric mean."),
 "notes": ("string", "—", "Behavioural notes behind the parameters."),
 "sources": ("string", "—", "Reference URLs for the species parameters."),
 "zones": ("string", "—", "Zones in which the species is modeled."),
 "id": ("string", "—", "Zone identifier."), "name": ("string", "—", "Human-readable zone name."),
 "band": ("string", "—", "inshore, nearshore or offshore."),
 "lat": ("float", "°N", "Zone centroid latitude."), "lon": ("float", "°E", "Zone centroid longitude."),
 "sst_lat": ("float", "°N", "Latitude of the open-water pixel used for satellite SST."),
 "sst_lon": ("float", "°E", "Longitude of the open-water pixel used for satellite SST."),
 "depth_ft_min": ("float", "ft", "Shallow end of the zone's fishable depth."),
 "depth_ft_max": ("float", "ft", "Deep end of the zone's fishable depth."),
 "depth_mid_ft": ("float", "ft", "Representative depth used for the near-bottom temperature approximation."),
 "distance_nm": ("float", "nm", "Approximate run from Point Loma."),
 "waters": ("string", "—", "us or mexico."), "permit": ("string", "—", "Permit requirement note."),
 "buoys": ("string", "—", "Reference NDBC buoys, comma separated."),
 "structure": ("string", "—", "Dominant habitat or structure."),
 # catch
 "landing": ("string", "—", "Sportfishing landing reporting the trip."),
 "boat": ("string", "—", "Vessel name."), "trip_type": ("string", "—", "Reported trip length category."),
 "trip_days": ("float", "days", "Trip length in days used to normalize effort."),
 "anglers": ("int", "count", "Anglers aboard."), "angler_days": ("float", "angler-days", "Anglers × trip length."),
 "kept": ("int", "fish", "Fish reported kept."), "released": ("int", "fish", "Fish reported released."),
 "trips": ("int", "count", "Boat trips contributing to the row."),
 "cpue": ("float", "fish/angler", "Fish kept per angler."),
 "cpue_per_angler_day": ("float", "fish/angler-day", "Fish kept per angler per day of trip length: the validation target."),
 "zone_hint": ("string", "—", "Zone inferred from the trip description, where stated."),
 "source_url": ("string", "—", "Page the report was read from."),
 # validation
 "spearman_rho": ("float", "−1–1", "Rank correlation between daily best score and normalized CPUE."),
 "pearson_r": ("float", "−1–1", "Linear correlation of the same pair."),
 "spearman_rho_raw_cpue": ("float", "−1–1", "Rank correlation against un-normalized fish per angler."),
 "mean_cpue_per_angler_day": ("float", "fish/angler-day", "Mean observed normalized CPUE over the compared days."),
 "mean_score": ("float", "0–100", "Mean modeled score over the compared days."),
 "total_fish": ("int", "fish", "Fish in the compared sample."),
 "total_anglers": ("int", "anglers", "Anglers in the compared sample."),
 "n_obs": ("int", "count", "Underlying observations in the aggregate."),
}
TABLES = {
 "conditions_daily": "Zone-day environmental fact table: every driver the model reads, plus its historical norm and anomaly.",
 "scores_daily": "Zone-species-day scores with driver decomposition, seasonal norm and forecast flag.",
 "climatological_curve": "Climatological score for every day of the year, per zone and species: the seasonal opportunity curve.",
 "sst_climatology": "Day-of-year satellite SST climatology per zone (mean, SD, percentiles, sample size).",
 "shore_sst_climatology": "Day-of-year shore-thermistor SST climatology per CO-OPS station.",
 "shore_sst_daily": "Daily shore-thermistor water temperature aggregates.",
 "buoy_climatology": "Day-of-year wave, wind and sea-temperature climatology per NDBC buoy.",
 "buoy_daily": "Daily NDBC buoy aggregates from the realtime and historical archives.",
 "mur_history": "Daily MUR satellite SST per zone sampling point.",
 "tide_daily": "Daily tide features derived from harmonic predictions.",
 "enso_oni": "Monthly CPC ONI with regime classification.",
 "enso_current": "The ENSO state in force at build time.",
 "enso_zone_composite": "Mean zone SST anomaly by month and ENSO regime, from this project's own satellite record.",
 "enso_sensitivity": "Regression of zone SST anomaly on ONI by month.",
 "catch_reports": "Boat-trip level dock totals normalized to species identifiers.",
 "catch_daily_cpue": "Daily catch per angler and per angler-day by species.",
 "model_validation": "Preliminary skill check of scores against observed CPUE.",
 "dim_zones": "Zone dimension: geography, depth, run distance, waters and reference stations.",
 "dim_species": "Species dimension: full response-curve parameters, weights, notes and sources.",
 "fronts": "Temperature-break strength derived from the MUR regional grid.",
 "upwelling_cuti": "Coastal Upwelling Transport Index (empty in this build).",
 "chlorophyll": "Satellite chlorophyll-a per zone (empty in this build).",
}

def dictionary():
    rows = []
    for name, desc in TABLES.items():
        p = f"{OUT}/{name}.csv"
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p, nrows=200)
        for c in d.columns:
            t, u, defi = DEF.get(c, ("—", "—", "Derived column; see build_dataset.py."))
            rows.append({"table": name, "field": c, "type": t, "units": u, "definition": defi})
    return rows

def md_registry():
    L = ["# Source Registry — SoCal Fishing Intelligence", "",
         "Every feed behind the dashboard and the exported dataset. All sources are public; none require an API key.",
         "Retrieval code lives in `fetch_raw.py`, `fetch_raw2.py`, `fetch_raw3.py`, `fetch_coops.py`, `fetch_mur.py` and `fetch_forecast.py`.", ""]
    for r in SRC["registry"]:
        L += [f"## {r['name']}", "",
              f"- **Provider**: {r['provider']}",
              f"- **Landing page**: {r['url']}",
              f"- **Endpoint**: `{r['endpoint']}`"]
        if r.get("stations"):
            L.append(f"- **Stations / points**: {r['stations']}")
        L += [f"- **Used for**: {r['used_for']}",
              f"- **Coverage retrieved**: {r['coverage']}",
              f"- **Update cadence**: {r['cadence']}",
              f"- **Access**: {r['access']}",
              f"- **Units**: {r['units']}",
              f"- **Licence**: {r['license']}",
              f"- **Limitations**: {r['limitations']}", ""]
    L += ["## Known gaps and honest caveats", ""] + [f"- {g}" for g in SRC["gaps"]] + [""]
    return "\n".join(L)

def md_dictionary(rows):
    L = ["# Data Dictionary — SoCal Fishing Intelligence", "",
         "Field-level definitions for every table in `socal_fishing_dataset.xlsx` and the CSV exports.",
         "Units are US customary (°F, ft, kt, nm) because that is how the underlying fishing decisions are made.", ""]
    for name, desc in TABLES.items():
        sub = [r for r in rows if r["table"] == name]
        if not sub:
            continue
        L += [f"## `{name}`", "", desc, "", "| Field | Type | Units | Definition |", "| --- | --- | --- | --- |"]
        L += [f"| `{r['field']}` | {r['type']} | {r['units']} | {r['definition']} |" for r in sub]
        L.append("")
    L += ["## Scoring model", "",
          "The opportunity score is a weighted geometric mean of driver suitabilities, each on a 0–1 scale:", "",
          "```", "core  = exp( Σ wᵢ · ln(termᵢ) / Σ wᵢ ) × anomaly_term",
          "raw   = core × (0.35 + 0.65 × fishability)",
          "score = soft_ceiling(raw) × 100     # soft_ceiling(v) = v for v ≤ 0.90,",
          "                                   #   else 0.90 + 0.10 · (1 − e^(−(v−0.90)/0.10))", "```", "",
          "- A geometric mean is used so that a single disqualifying driver (water far outside the species' window, or a",
          "  species out of season) suppresses the score rather than being averaged away.",
          "- `anomaly_term = clamp(1 + anomaly_pref × (sst_anom_f / 2.5) × 0.45, 0.35, 1.35)` applies the current climate",
          "  regime: warm-water species gain in a warm anomaly, cold-water species lose.",
          "- `fishability` is a separate access gate from wind, wave height, wave period and run distance, so an unfishable",
          "  day never shows a high score even when the biology is perfect. It never zeroes a score completely (floor 0.35)",
          "  because conditions can improve within a day.",
          "- `seasonal_norm_score` re-runs the same model on climatological SST, wave and wind with neutral tide and moon",
          "  terms, which is what makes 'better or worse than typical for this date' a like-for-like comparison.",
          "- `annual_percentile` ranks the day against that zone-species climatological curve across all 366 days.",
          "- `soft_ceiling` compresses the top of the range instead of clipping it. Without it, a strong warm",
          "  anomaly pushes several species past 1.0 and they all tie at exactly 100, which destroys ranking",
          "  information at the top. The mapping is strictly monotonic, so ordering is preserved and 100 is",
          "  approached but never reached.", "",
          "## Reproducing the build", "",
          "```bash", "python fetch_raw.py        # buoys, tides, ONI, NWS",
          "python fetch_coops.py 2015 2026 water_temperature", "python fetch_forecast.py   # per-zone marine + atmospheric forecast",
          "python fetch_mur.py zones 0 9   # satellite SST, strictly sequential",
          "python build_dataset.py    # features, climatologies, scores, validation",
          "python export_xlsx.py      # workbook + CSV bundle", "python build_site.py       # dashboard payload", "```", ""]
    return "\n".join(L)

if __name__ == "__main__":
    rows = dictionary()
    open(f"{DOC}/SOURCE_REGISTRY.md", "w").write(md_registry())
    open(f"{DOC}/DATA_DICTIONARY.md", "w").write(md_dictionary(rows))
    json.dump({"registry": SRC["registry"], "dictionary": rows, "gaps": SRC["gaps"]},
              open(f"{OUT}/docs.json", "w"), indent=1)
    print(f"registry {len(SRC['registry'])} sources | dictionary {len(rows)} fields | gaps {len(SRC['gaps'])}")
