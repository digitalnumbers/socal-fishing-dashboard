# Source Registry — SoCal Fishing Intelligence

Every feed behind the dashboard and the exported dataset. All sources are public; none require an API key.
Retrieval code lives in `fetch_raw.py`, `fetch_raw2.py`, `fetch_raw3.py`, `fetch_coops.py`, `fetch_mur.py` and `fetch_forecast.py`.

## NDBC realtime standard meteorological data

- **Provider**: NOAA National Data Buoy Center
- **Landing page**: https://www.ndbc.noaa.gov/data/realtime2/46232.txt
- **Endpoint**: `https://www.ndbc.noaa.gov/data/realtime2/{station}.txt`
- **Stations / points**: 46232 Point Loma South, 46258 Mission Bay West, 46086 San Clemente Basin, 46225 Torrey Pines Outer, SDBC1 Shelter Island, LJAC1 La Jolla
- **Used for**: Current wave height, dominant/average period, wind speed and direction, air and sea temperature, barometric pressure; cross-check for the modeled zone timelines
- **Coverage retrieved**: Rolling 45 days
- **Update cadence**: Every 10-60 minutes
- **Access**: Public, no key
- **Units**: m, s, m/s, degC, hPa (converted to ft, kt, degF in this project)
- **Licence**: US Government work, public domain
- **Limitations**: Station outages are common; 46225 and 46258 have multi-month gaps; wave period fields are sometimes MM (missing) even when height reports

## NDBC historical standard meteorological archive

- **Provider**: NOAA National Data Buoy Center
- **Landing page**: https://www.ndbc.noaa.gov/histsearch.php
- **Endpoint**: `https://www.ndbc.noaa.gov/view_text_file.php?filename={station}h{year}.txt.gz&dir=data/historical/stdmet/`
- **Stations / points**: 46232, 46258, 46086, 46225
- **Used for**: Day-of-year climatology and percentiles for wave height, wind speed and buoy sea temperature; the long-baseline 'normal' that current conditions are compared against
- **Coverage retrieved**: 2003/2004 through 2025 depending on station (75 station-years retrieved)
- **Update cadence**: Annual files, 10-60 minute samples
- **Access**: Public, no key
- **Units**: m, s, m/s, degC, hPa
- **Licence**: US Government work, public domain
- **Limitations**: Buoy relocations and sensor changes are not homogenized; 46225 was deployed later than the others; gaps reduce the effective sample in some day-of-year windows

## CO-OPS water temperature (shore thermistor)

- **Provider**: NOAA Tides & Currents
- **Landing page**: https://tidesandcurrents.noaa.gov/stationhome.html?id=9410170
- **Endpoint**: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=water_temperature&station={station}&begin_date=&end_date=&units=english&time_zone=lst_ldt&format=json`
- **Stations / points**: 9410170 San Diego Bay (Broadway Pier), 9410230 La Jolla (Scripps Pier)
- **Used for**: Authoritative inshore water temperature for San Diego Bay, Mission Bay and the surf zone, plus their 11-year day-of-year climatology and anomalies
- **Coverage retrieved**: 2015-01 through present (retrieved in 31-day chunks, 304 requests)
- **Update cadence**: 6 minutes
- **Access**: Public, no key
- **Units**: degF (requested directly)
- **Licence**: US Government work, public domain
- **Limitations**: Hard 31-day limit per request for this product; no usable record before 2015 for these stations, so the inshore climatology is 11 years rather than the 23-year buoy baseline; pier thermistors sit at fixed depth and read warmer than the open coast in summer

## CO-OPS harmonic tide predictions

- **Provider**: NOAA Tides & Currents
- **Landing page**: https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id=9410170
- **Endpoint**: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&station={station}&interval=hilo|30&datum=MLLW&units=english&time_zone=lst_ldt&format=json`
- **Stations / points**: 9410170 San Diego Bay, 9410230 La Jolla
- **Used for**: Tide range, maximum exchange rate, high/low timing and the best-water hour used by the tide timing term
- **Coverage retrieved**: Retrieved window 2026-07 through 2026-09-06
- **Update cadence**: High/low events plus 30-minute series
- **Access**: Public, no key
- **Units**: ft above MLLW, local time
- **Licence**: US Government work, public domain
- **Limitations**: Harmonic predictions exclude weather-driven setup and surge; the 30-minute series was pulled only for the dashboard window, so scoring outside that window falls back to high/low interpolation

## CO-OPS barometric pressure

- **Provider**: NOAA Tides & Currents
- **Landing page**: https://tidesandcurrents.noaa.gov/stationhome.html?id=9410170
- **Endpoint**: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=air_pressure&station=9410170&...`
- **Stations / points**: 9410170
- **Used for**: Independent check on the modeled barometric trend used by the pressure term
- **Coverage retrieved**: 2026 monthly chunks
- **Update cadence**: 6 minutes
- **Access**: Public, no key
- **Units**: mbar
- **Licence**: US Government work, public domain
- **Limitations**: Single station, bay location; sensor gaps in some months

## MUR L4 global 1 km sea surface temperature (jplMURSST41)

- **Provider**: NASA JPL via NOAA CoastWatch ERDDAP
- **Landing page**: https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.html
- **Endpoint**: `https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.csv?analysed_sst[(start):1:(end)][(lat)][(lon)]`
- **Used for**: Satellite sea surface temperature for the kelp, canyon, bank and Mexican-water zones; the day-of-year SST climatology, anomalies and percentiles; ENSO regime composites and per-zone ENSO sensitivity; horizontal temperature-break (front) strength
- **Coverage retrieved**: 2015-01-01 through present per zone point, plus a recent regional grid for gradients
- **Update cadence**: Daily, 1 km analysed field
- **Access**: Public ERDDAP, no key
- **Units**: degC (converted to degF)
- **Licence**: NASA/JPL MUR, open with attribution
- **Limitations**: ERDDAP accepts only one concurrent request per client, so acquisition is strictly sequential and slow; the analysed field is a gap-free blend, not a raw observation; the 1 km grid masks the interior of San Diego and Mission Bay, so those zones sample the nearest open-water pixel and defer to the shore thermistor

## Open-Meteo Marine forecast API

- **Provider**: Open-Meteo (ECMWF/MFWAM-based wave models)
- **Landing page**: https://open-meteo.com/en/docs/marine-weather-api
- **Endpoint**: `https://marine-api.open-meteo.com/v1/marine?latitude=&longitude=&daily=wave_height_max,wave_period_max,swell_wave_height_max,sea_surface_temperature_max&past_days=14&forecast_days=7`
- **Used for**: Per-zone wave height, wave and swell period, dominant direction and model SST for the 14-day hindcast plus 7-day forecast timeline that the scoring engine runs on
- **Coverage retrieved**: 14 days past, 7 days forward, all 9 zones
- **Update cadence**: Hourly and daily, refreshed multiple times per day
- **Access**: Public, no key for non-commercial use
- **Units**: m, s, degC (converted to ft, degF)
- **Licence**: Open-Meteo, CC-BY 4.0; underlying ECMWF data under its own open terms
- **Limitations**: Model output rather than measurement; nearshore refraction and island shadowing at the Coronados are not resolved at model scale; SST is a model field and is only used where satellite or thermistor data are unavailable

## Open-Meteo forecast API (atmosphere)

- **Provider**: Open-Meteo
- **Landing page**: https://open-meteo.com/en/docs
- **Endpoint**: `https://api.open-meteo.com/v1/forecast?latitude=&longitude=&daily=wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,pressure_msl_mean,cloud_cover_mean&past_days=14&forecast_days=7&wind_speed_unit=kn`
- **Used for**: Per-zone wind speed, gusts, direction, mean sea-level pressure (and its 24-hour trend), cloud cover and air temperature
- **Coverage retrieved**: 14 days past, 7 days forward, all 9 zones
- **Update cadence**: Hourly and daily
- **Access**: Public, no key for non-commercial use
- **Units**: kt, hPa, %, degC
- **Licence**: CC-BY 4.0
- **Limitations**: Coarse grid smooths Santa Ana channeling and the afternoon sea-breeze ramp inside the bays

## National Weather Service API (San Diego, SGX)

- **Provider**: NOAA National Weather Service
- **Landing page**: https://api.weather.gov/points/32.72,-117.22
- **Endpoint**: `https://api.weather.gov/gridpoints/SGX/{x},{y} and /forecast`
- **Used for**: Narrative and gridded coastal forecast retained as a cross-check on wind timing
- **Coverage retrieved**: 7 days forward
- **Update cadence**: Hourly updates
- **Access**: Public, User-Agent required
- **Units**: km/h, degC
- **Licence**: US Government work, public domain
- **Limitations**: The coastal land gridpoint can return degenerate wave fields (a single zero value), so wave data are taken from the marine model instead. The SGX Coastal Waters Forecast narrative is a cross-check, not a numerical wave input.

## CPC Oceanic Nino Index (ONI)

- **Provider**: NOAA Climate Prediction Center
- **Landing page**: https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
- **Endpoint**: `https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt`
- **Used for**: ENSO regime classification (El Nino / La Nina / neutral, with strength tiers), the regime badge, the ONI history chart, monthly regime composites of zone SST anomaly, and the per-zone ENSO sensitivity regression
- **Coverage retrieved**: 1950-01 through present (918 months)
- **Update cadence**: Monthly
- **Access**: Public, no key
- **Units**: degC anomaly, 3-month running mean
- **Licence**: US Government work, public domain
- **Limitations**: Monthly resolution and a 3-month running mean lag current conditions; ERSSTv5 base periods are updated in 5-year steps, so historical values shift slightly between releases

## CPC weekly Nino 3.4 SST anomaly

- **Provider**: NOAA Climate Prediction Center
- **Landing page**: https://www.cpc.ncep.noaa.gov/data/indices/wksst8110.for
- **Endpoint**: `https://www.cpc.ncep.noaa.gov/data/indices/wksst8110.for`
- **Used for**: Weekly cross-check on the direction of the current ENSO state ahead of the monthly ONI update
- **Coverage retrieved**: 1990 through present
- **Update cadence**: Weekly
- **Access**: Public, no key
- **Units**: degC anomaly
- **Licence**: US Government work, public domain
- **Limitations**: Fixed-width legacy text format; not used in scoring

## San Diego dock totals (CPFV fish counts)

- **Provider**: sandiegofishreports.com, cross-checked against Fisherman's Landing and Seaforth Landing
- **Landing page**: https://www.sandiegofishreports.com/dock_totals/boats.php
- **Endpoint**: `https://www.sandiegofishreports.com/dock_totals/boats.php`
- **Used for**: Observed catch-per-angler-day used as the validation target for model skill, and the species mix currently being landed
- **Coverage retrieved**: 2026-08-15 through 2026-08-27 (13 days, 336 boat trips, 7,998 angler-days, 4 landings)
- **Update cadence**: Daily
- **Access**: Public web page
- **Units**: fish kept and released, anglers per trip
- **Licence**: Third-party editorial content; used here for non-commercial analysis with attribution
- **Limitations**: Effort is self-reported and only normalizable by anglers and trip length; fishing location is inferred from trip type rather than logged; totals reflect where the fleet chose to fish, and species are reported inconsistently across landings; only 13 days were available, which is far too short for a real skill estimate

## CDFW marine fisheries data and CPFV logbook program

- **Provider**: California Department of Fish and Wildlife
- **Landing page**: https://wildlife.ca.gov/Conservation/Marine/Data-Management-Research/MFDE
- **Endpoint**: `https://wildlife.ca.gov/Fishing/Commercial/MFSU`
- **Used for**: Species-behavior context, regulatory framing and published aggregate landings used when setting seasonal indices
- **Coverage retrieved**: Multi-decade published aggregates
- **Update cadence**: Annual to monthly publications
- **Access**: Public reports; trip-level CPFV logbook records are confidential and not obtainable via API
- **Units**: fish, pounds
- **Licence**: State of California public records
- **Limitations**: Trip-level logbook data are confidential, so the project substitutes public dock totals; published aggregates lag by roughly a year and are not spatially resolved to these zones

## CPC 8-14 day temperature and precipitation outlook (GIS shapefiles)

- **Provider**: NOAA Climate Prediction Center
- **Landing page**: https://www.cpc.ncep.noaa.gov/products/predictions/814day/
- **Endpoint**: `https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/814temp_latest.zip`
- **Stations / points**: Sampled at the San Diego onshore proxy point -117.15, 32.70
- **Used for**: Probabilistic warm/cool lean applied as an additive nudge to the day 7-14 SST projection (cpc_nudge_f), and the CPC columns shown on the 30-Day Outlook tab
- **Coverage retrieved**: Days 8-14 from the issuance date
- **Update cadence**: Daily
- **Access**: Public, no key
- **Units**: Tercile probability in percent, converted to a signed degF nudge
- **Licence**: US Government work, public domain
- **Limitations**: BASIS = forecast, but a LAND forecast: this is a CONUS *air* temperature tercile product, not a marine SST forecast. No polygon covers the offshore zones, so a single onshore San Diego proxy point is sampled for all nine zones including Cortez Bank at 95 nm. The true coastal point -117.22, 32.72 falls outside every CPC CONUS polygon. Equal-Chances areas contribute exactly zero signal.

## CPC week 3-4 temperature and precipitation outlook (GIS shapefiles)

- **Provider**: NOAA Climate Prediction Center
- **Landing page**: https://www.cpc.ncep.noaa.gov/products/predictions/WK34/
- **Endpoint**: `https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/wk34temp_latest.zip`
- **Stations / points**: Sampled at the San Diego onshore proxy point -117.15, 32.70
- **Used for**: Probabilistic warm/cool lean for the day 15-28 outlook tier
- **Coverage retrieved**: Weeks 3 and 4 from the issuance date
- **Update cadence**: Weekly (Friday)
- **Access**: Public, no key
- **Units**: Tercile probability in percent
- **Licence**: US Government work, public domain
- **Limitations**: BASIS = forecast. Same land/CONUS air-temperature caveat as the 8-14 day product, and it is a two-week average, so it carries no day-to-day information at all. Down-weighted to 0.32 in the extended blend.

## CPC monthly and seasonal temperature outlooks (GIS shapefiles)

- **Provider**: NOAA Climate Prediction Center
- **Landing page**: https://www.cpc.ncep.noaa.gov/products/predictions/long_range/
- **Endpoint**: `https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/seastemp_{YYYYMM}.zip`
- **Stations / points**: Sampled at the San Diego onshore proxy point -117.15, 32.70
- **Used for**: Weakest-weighted warm/cool lean at the far end of the 30-day window
- **Coverage retrieved**: Monthly lead 1 and seasonal leads
- **Update cadence**: Monthly (mid-month), with a monthly update near the end of the month
- **Access**: Public, no key
- **Units**: Tercile probability in percent
- **Licence**: US Government work, public domain
- **Limitations**: BASIS = forecast, weakly weighted (0.20 monthly, 0.15 seasonal). At this build the monthly *update* product was stale, so the September outlook was read from the seasonal lead-14 file instead, and at 33 percent probability it is Equal Chances and contributes exactly zero signal. Same land/CONUS air-temperature caveat.

## CO-OPS tide predictions, extended horizon

- **Provider**: NOAA Tides & Currents
- **Landing page**: https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id=9410170
- **Endpoint**: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&station={station}&begin_date={YYYYMMDD}&end_date={YYYYMMDD}&datum=MLLW&interval=hilo&units=english&time_zone=lst_ldt&format=json`
- **Stations / points**: 9410170 San Diego Bay (Broadway Pier), 9410230 La Jolla (Scripps Pier)
- **Used for**: Tide range, strongest exchange rate and best-water hour for all 30 days
- **Coverage retrieved**: Verified working at least 40 days ahead
- **Update cadence**: Static harmonic predictions
- **Access**: Public, no key
- **Units**: ft MLLW, ft/h, local hour
- **Licence**: US Government work, public domain
- **Limitations**: BASIS = astronomical, so these are exactly as reliable at day 30 as at day 1 and are the strongest signal in the outlook tier. Harmonic predictions exclude weather-driven surge and river/runoff effects. On strongly diurnal days the daily extremum window is narrowed to the day's single extremum plus the closer of its two neighbours, flagged by tide_window_widened, to avoid inflating the range across two exchanges.

## Open-Meteo 16-day marine and atmospheric forecast

- **Provider**: Open-Meteo
- **Landing page**: https://open-meteo.com/en/docs/marine-weather-api
- **Endpoint**: `https://marine-api.open-meteo.com/v1/marine?forecast_days=16 and https://api.open-meteo.com/v1/forecast?forecast_days=16`
- **Stations / points**: One sampling point per zone (nine zones)
- **Used for**: The deterministic tail of the day 8-14 tier: wind, swell and pressure blended toward climatology with weight exp(-(lead-6)/7)
- **Coverage retrieved**: 16 requested days, but the two models do NOT reach equally far
- **Update cadence**: Multiple runs daily
- **Access**: Public, no key
- **Units**: m, s, m/s, hPa (converted to ft, kt, degF)
- **Licence**: CC-BY 4.0, non-commercial free tier
- **Limitations**: BASIS = forecast, but with a hard and unequal horizon. At this build the MARINE (swell) fields null out after 2026-09-05, roughly lead 8, while the ATMOSPHERIC (wind) fields run to 2026-09-12, lead 15. The two are therefore handled independently and tagged per row by wave_basis and wind_basis. Beyond the atmospheric horizon no daily wind or swell number is published at all.

## Known gaps and honest caveats

- Chlorophyll-a: no ERDDAP dataset ID resolved during this build, so the chlorophyll driver has zero weight in every score and is reported as a missing driver rather than imputed.
- Upwelling index (CUTI): the ERDDAP table endpoint returned HTTP 404, so the upwelling column is present in the schema but empty.
- Front (temperature-break) strength is computed only where the recent MUR regional grid covers the zone; older dates fall back to a null front term rather than an estimate.
- Trip-level CPFV logbook catch is confidential, so validation relies on 13 days of public dock totals from a single late-summer window - enough for a sanity check, not for a skill score.
- The inshore SST climatology is 11 years (CO-OPS, 2015-present) while the wave and wind climatology is up to 23 years (NDBC buoys); anomalies from the two baselines are not directly comparable.
- Species response parameters are literature- and experience-derived priors, not fitted coefficients. They have not been calibrated against catch data because the available catch record is too short.
- Mexican-water zones (Coronado Islands, Cortez and Tanner banks) carry no permit or closure feed; a Mexican FMM permit and fishing licence are required and must be verified independently.
- This is a static snapshot build: the dashboard reflects the data as of the build timestamp and does not refresh itself.
- Extended range, days 15-30: no true extended MARINE forecast exists at public-API level, so wave_proj_ft, wind_proj_kt and fishability_outlook are deliberately NULL for the whole tier rather than filled with a seasonal average dressed up as a forecast.
- Extended range: the CPC 8-14 day, week 3-4 and monthly outlooks are CONUS land air-temperature tercile products. No polygon covers the offshore zones, so one onshore San Diego proxy point (-117.15, 32.70) is sampled for all nine zones. Treat it as a regional warm/cool lean only.
- Extended range: observed analog-year SST anomalies can only be computed for analog years inside the MUR satellite record, so of the eight ENSO analog years only 2023 has an observed local anomaly (and it ran counter to the composite). The primary ENSO signal is the zone composite.
- Extended range: barometric pressure exists only inside the deterministic model horizon. Feeding it into days 8-14 and dropping it at day 15 renormalized the driver weights and produced a false score cliff at the tier boundary, so pressure is excluded from the extended score at BOTH tiers and published only as the reference field pressure_trend_model_24h.
- Extended range: front strength and chlorophyll are unavailable beyond the near term, so those drivers are dropped and the remaining weights renormalize. Extended scores are directionally comparable to near-term scores but are not identically constructed.
- Extended range: only NDBC 46086 carries a wind climatology, so seven of nine zones fall back to it as a regional wind normal, flagged by wind_norm_is_regional_fallback. Wave climatology gets NO such fallback -- San Diego Bay and Mission Bay keep a null wave normal and honestly drop the swell driver, because substituting an offshore buoy put 8 ft swell inside a harbour.
- Extended range: the published score band is ratcheted so its half-width never narrows as lead time grows. The raw SST-sensitivity band can narrow at long lead where the score saturates near its soft ceiling, which would falsely imply rising certainty. Rows where the band edge is clipped by the bounded 0-100 scale are flagged by score_band_hits_ceiling / score_band_hits_floor.
