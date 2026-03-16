# -*- coding: utf-8 -*-
"""
Run this ONCE to produce a small San Diego-only GeoJSON from the
full national zip_codes.geojson file.

Output: san_diego_zips.geojson  (~100-200 KB instead of 1.4 GB)

Run from your project folder:
    python filter_geojson.py
"""

import geopandas as gpd
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent

print("Loading dashboard dataset to get target ZIP codes...")
dataset = pd.read_csv(ROOT / "dashboard_dataset.csv")
target_zips = set(dataset["zip_code"].astype(str).str.zfill(5).tolist())
print(f"  Target ZIPs: {len(target_zips)}")

print("Loading national GeoJSON (this will take a moment — one time only)...")
geo = gpd.read_file(ROOT / "zip_codes.geojson")
print(f"  Total national polygons loaded: {len(geo)}")

# Standardize ZIP field
geo["ZCTA5CE20"] = geo["ZCTA5CE20"].astype(str).str.zfill(5)

# Filter to San Diego ZIPs only
sd_geo = geo[geo["ZCTA5CE20"].isin(target_zips)].copy()
print(f"  Polygons after filtering: {len(sd_geo)}")

# Simplify geometry to reduce file size (tolerance in degrees, ~100m)
sd_geo["geometry"] = sd_geo["geometry"].simplify(tolerance=0.001, preserve_topology=True)

# Save
out_path = ROOT / "san_diego_zips.geojson"
sd_geo.to_file(out_path, driver="GeoJSON")
print(f"\nDone! Saved to: {out_path}")
print(f"File size: {out_path.stat().st_size / 1024:.1f} KB")
