# -*- coding: utf-8 -*-
"""
fetch_drive_times.py
────────────────────
Fetches actual driving time (minutes) from each ZIP centroid to the
main gate of Naval Base San Diego using the free public OSRM API.
No API key required.

Output: drive_times.csv  (columns: zip_code, drive_minutes)

Run once, then drop drive_times.csv in your project folder.
The app will auto-detect and use it on next launch.

Usage:
    python fetch_drive_times.py
"""

import time
import requests
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────

BASE_LAT  = 32.6881    # Naval Base San Diego main gate (more precise than centroid)
BASE_LON  = -117.1322

OSRM_URL  = "http://router.project-osrm.org/route/v1/driving"
DELAY_SEC = 0.5        # polite delay between requests (public server)
TIMEOUT   = 10         # seconds per request

ZIP_TABLE = "zip_table_clean.csv"
OUTPUT    = "drive_times.csv"

# ── Load ZIP centroids ──────────────────────────────────────────────────────

print(f"Loading ZIP centroids from {ZIP_TABLE}...")
zips = pd.read_csv(ZIP_TABLE)
zips["zip_code"] = zips["zip_code"].astype(str).str.zfill(5)

print(f"Found {len(zips)} ZIP codes to process.\n")

# ── Fetch drive times ───────────────────────────────────────────────────────

results = []
failed  = []

for idx, row in zips.iterrows():
    zc       = row["zip_code"]
    src_lat  = row["lat"]
    src_lon  = row["lon"]

    url = (
        f"{OSRM_URL}/"
        f"{src_lon},{src_lat};"
        f"{BASE_LON},{BASE_LAT}"
        f"?overview=false&steps=false"
    )

    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == "Ok" and data.get("routes"):
            duration_sec = data["routes"][0]["duration"]
            drive_min    = round(duration_sec / 60, 1)
            results.append({"zip_code": zc, "drive_minutes": drive_min})
            print(f"  [{idx+1:>3}/{len(zips)}]  ZIP {zc}  →  {drive_min:.1f} min")
        else:
            print(f"  [{idx+1:>3}/{len(zips)}]  ZIP {zc}  →  OSRM returned no route")
            failed.append(zc)
            results.append({"zip_code": zc, "drive_minutes": None})

    except requests.exceptions.RequestException as e:
        print(f"  [{idx+1:>3}/{len(zips)}]  ZIP {zc}  →  Request failed: {e}")
        failed.append(zc)
        results.append({"zip_code": zc, "drive_minutes": None})

    time.sleep(DELAY_SEC)

# ── Save ────────────────────────────────────────────────────────────────────

df = pd.DataFrame(results)
df.to_csv(OUTPUT, index=False)

print(f"\n✅ Saved {OUTPUT}  ({len(df)} rows)")

if failed:
    print(f"⚠️  {len(failed)} ZIPs failed or returned no route: {', '.join(failed)}")
    print("   These will show None in the app and fall back to straight-line distance.")
else:
    print("✅ All ZIPs fetched successfully — no failures.")

print("\nDrop drive_times.csv in your project folder and restart the app.")
