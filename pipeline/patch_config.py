import json
z = json.load(open("/home/user/workspace/socal/config/zones.json"))
sst_pts = {"sd_bay": (32.665, -117.245), "mission_bay": (32.765, -117.268), "surf_zone": (32.955, -117.288),
           "pt_loma_kelp": (32.665, -117.278), "la_jolla": (32.855, -117.298)}
depth_mid = {"sd_bay": 25, "mission_bay": 20, "surf_zone": 10, "pt_loma_kelp": 80, "la_jolla": 150,
             "nine_mile": 350, "coronados": 150, "outer_banks": 400, "cortez_tanner": 300}
for x in z:
    la, lo = sst_pts.get(x["id"], (x["lat"], x["lon"]))
    x["sst_lat"], x["sst_lon"] = la, lo
    x["depth_mid_ft"] = depth_mid[x["id"]]
json.dump(z, open("/home/user/workspace/socal/config/zones.json", "w"), indent=1)
s = json.load(open("/home/user/workspace/socal/config/species.json"))
for x in s:
    x["temp_ref"] = "bottom" if x["id"] in {"rockfish", "sheephead"} else "surface"
json.dump(s, open("/home/user/workspace/socal/config/species.json", "w"), indent=1)
print("patched", len(z), "zones,", len(s), "species")
