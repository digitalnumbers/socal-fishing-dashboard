"""Per-zone 7-day marine + atmospheric forecast (Open-Meteo) and NWS marine text zones."""
import sys, json
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/user/workspace/socal")
from fetch_raw import get, save, ZONES, task

MARINE = ("https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
          "&daily=wave_height_max,wave_period_max,wave_direction_dominant,swell_wave_height_max,"
          "swell_wave_period_max,sea_surface_temperature_max,sea_surface_temperature_min"
          "&hourly=sea_surface_temperature,wave_height&timezone=America%2FLos_Angeles"
          "&forecast_days=7&past_days=14")
WX = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
      "&daily=wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,pressure_msl_mean,"
      "cloud_cover_mean,temperature_2m_max,precipitation_sum"
      "&hourly=wind_speed_10m,pressure_msl&timezone=America%2FLos_Angeles&forecast_days=7&past_days=14"
      "&wind_speed_unit=kn")

def one(z):
    a = save(f"om_marine_{z['id']}.json", get(MARINE.format(lat=z["lat"], lon=z["lon"]), timeout=90))
    b = save(f"om_wx_{z['id']}.json", get(WX.format(lat=z["lat"], lon=z["lon"]), timeout=90))
    return a + " | " + b

def marine_zones():
    out = []
    for zid in ["PZZ750", "PZZ775", "PZZ725", "PZZ700"]:
        try:
            out.append(save(f"nws_marine_{zid}.json",
                            get(f"https://api.weather.gov/zones/forecast/{zid}/forecast", timeout=60)))
        except Exception as e:
            out.append(f"{zid} FAIL {e}")
    return " | ".join(out)

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=5) as ex:
        jobs = [ex.submit(task, one, z) for z in ZONES] + [ex.submit(task, marine_zones)]
        for f in as_completed(jobs):
            print(f.result(), flush=True)
