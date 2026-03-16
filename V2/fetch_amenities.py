# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""
fetch_amenities.py
------------------
Uses the Overpass API (OpenStreetMap) to pull amenity locations near
Naval Base San Diego.

Fetches:
  - Grocery stores / supermarkets
  - Gyms / fitness centers
  - Urgent care / medical clinics

Output: amenities.geojson

Run once, drop in project folder, restart app.

Dependencies:
    pip install requests pandas geopandas shapely
"""

import time
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ── Config ────────────────────────────────────────────────────────────────────

SD_BBOX      = "(32.4,-117.7,33.2,-116.8)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUTPUT       = "amenities.geojson"

# Delays — be conservative with the public server
DELAY_BETWEEN_QUERIES = 10.0   # seconds between each query
RETRY_WAIT            = 30.0   # seconds to wait after any 429 or 5xx before retry
MAX_RETRIES           = 3

BBOX = SD_BBOX

# ── Query definitions ─────────────────────────────────────────────────────────
#
# Grocery: brand-name chains only via "brand" tag OR explicit supermarket shop tag
#   — avoids hundreds of tiny OSM-tagged corner stores
#
# Urgent care: split into a single clean query using only "amenity=clinic"
#   and "healthcare=urgent_care" — avoids the heavy hospital way query that
#   was timing out the server

QUERIES = {
    "grocery": (
        "[out:json][timeout:30];\n"
        "(\n"
        '  node["shop"="supermarket"]' + BBOX + ";\n"
        '  way["shop"="supermarket"]' + BBOX + ";\n"
        ");\n"
        "out center;\n"
    ),
    "gym": (
        "[out:json][timeout:30];\n"
        "(\n"
        '  node["leisure"="fitness_centre"]' + BBOX + ";\n"
        '  way["leisure"="fitness_centre"]' + BBOX + ";\n"
        ");\n"
        "out center;\n"
    ),
    "urgent_care": (
        "[out:json][timeout:30];\n"
        "(\n"
        '  node["healthcare"="urgent_care"]' + BBOX + ";\n"
        '  node["amenity"="clinic"]' + BBOX + ";\n"
        '  way["healthcare"="urgent_care"]' + BBOX + ";\n"
        ");\n"
        "out center;\n"
    ),
}

# ── Fetch with retry ──────────────────────────────────────────────────────────

def fetch_query(atype, query):
    """Fetch one Overpass query. Returns list of feature dicts or empty list."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  Attempt {attempt}/{MAX_RETRIES}...")
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=60
            )

            if resp.status_code == 429:
                print(f"  429 rate-limited — waiting {RETRY_WAIT:.0f}s...")
                time.sleep(RETRY_WAIT)
                continue

            if resp.status_code in (502, 503, 504):
                print(f"  {resp.status_code} server error — waiting {RETRY_WAIT:.0f}s...")
                time.sleep(RETRY_WAIT)
                continue

            resp.raise_for_status()
            elements = resp.json().get("elements", [])

            features = []
            for el in elements:
                if el["type"] == "way":
                    lat = el.get("center", {}).get("lat")
                    lon = el.get("center", {}).get("lon")
                else:
                    lat = el.get("lat")
                    lon = el.get("lon")

                if lat is None or lon is None:
                    continue

                name = el.get("tags", {}).get("name", atype.replace("_", " ").title())
                features.append({
                    "type":   atype,
                    "name":   name,
                    "lat":    lat,
                    "lon":    lon,
                    "osm_id": el.get("id"),
                })

            print(f"  OK  {len(features)} {atype} locations")
            return features

        except requests.exceptions.Timeout:
            print(f"  Request timed out — waiting {RETRY_WAIT:.0f}s...")
            time.sleep(RETRY_WAIT)
        except Exception as e:
            print(f"  Unexpected error: {e}")
            break

    print(f"  SKIP {atype} — failed after {MAX_RETRIES} attempts")
    return []


all_features = []

for atype, query in QUERIES.items():
    print(f"\nFetching {atype}...")
    results = fetch_query(atype, query)
    all_features.extend(results)
    print(f"  Waiting {DELAY_BETWEEN_QUERIES:.0f}s before next query...")
    time.sleep(DELAY_BETWEEN_QUERIES)

# ── Guard ─────────────────────────────────────────────────────────────────────

if not all_features:
    print("\nNo features fetched — check your connection and try again.")
    raise SystemExit(1)

# ── Deduplicate ───────────────────────────────────────────────────────────────

df = pd.DataFrame(all_features)
print(f"\nTotal before dedupe: {len(df)}")
df = df.drop_duplicates(subset=["osm_id"]).reset_index(drop=True)
print(f"Total after dedupe:  {len(df)}")

# ── Filter to SD bounding box ─────────────────────────────────────────────────

df = df[
    df["lat"].between(32.4, 33.2) &
    df["lon"].between(-117.7, -116.8)
].copy()
print(f"After bbox filter:   {len(df)}")

# ── Build GeoDataFrame ────────────────────────────────────────────────────────

geometry = [Point(row["lon"], row["lat"]) for _, row in df.iterrows()]
gdf = gpd.GeoDataFrame(
    df[["type", "name", "lat", "lon", "osm_id"]],
    geometry=geometry,
    crs="EPSG:4326"
)

# ── Save ──────────────────────────────────────────────────────────────────────

gdf.to_file(OUTPUT, driver="GeoJSON")

print(f"\nSaved {OUTPUT}  ({len(gdf)} features)")
print(f"\nBreakdown by type:")
print(gdf["type"].value_counts().to_string())
print("\nDrop amenities.geojson in your project folder and restart the app.")