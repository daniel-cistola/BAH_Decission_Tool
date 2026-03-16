# -*- coding: utf-8 -*-
"""
fetch_schools.py
----------------
Fetches public school data for San Diego County using the Urban Institute
Education Data Portal API (free, no API key required).

API docs: https://educationdata.urban.org/documentation/

Output: schools_by_zip.csv  (columns: zip_code, school_count, school_rating)

Quality score proxy (0-10):
  - Title I status      (40%) — non-Title-I = better
  - Locale type         (35%) — suburban/town > city > rural
  - Student-teacher ratio (25%) — lower is better

Run once, drop in project folder, restart app.

Dependencies:
    pip install requests pandas numpy
"""

import time
import requests
import numpy as np
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────

ZIP_TABLE = "zip_table_clean.csv"
OUTPUT    = "schools_by_zip.csv"

# Urban Institute Education Data Portal
# fips=6 = California, county_code=073 = San Diego
# year=2022 = most recent available CCD directory data
BASE_URL  = "https://educationdata.urban.org/api/v1/schools/ccd/directory/2022/"
PARAMS    = {
    "fips":        6,
    "county_code": 73,
}

DELAY_SEC = 1.0
TIMEOUT   = 30

# ── Load target ZIPs ──────────────────────────────────────────────────────────

print(f"Loading target ZIPs from {ZIP_TABLE}...")
zips = pd.read_csv(ZIP_TABLE)
zips["zip_code"] = zips["zip_code"].astype(str).str.zfill(5)
target_zips = set(zips["zip_code"].tolist())
print(f"Targeting {len(target_zips)} ZIP codes.\n")

# ── Fetch paginated results ───────────────────────────────────────────────────

print("Fetching school data from Urban Institute Education Data Portal...")
print(f"URL: {BASE_URL}")
print(f"Filters: fips=6 (California), county_code=073 (San Diego)\n")

all_records = []
url = BASE_URL

while url:
    try:
        resp = requests.get(url, params=PARAMS if url == BASE_URL else None, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        all_records.extend(results)
        print(f"  Fetched {len(all_records)} schools so far...")

        url = data.get("next")   # paginate
        if url:
            time.sleep(DELAY_SEC)

    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Response: {resp.text[:300]}")
        break
    except Exception as e:
        print(f"Error: {e}")
        break

print(f"\nTotal schools fetched: {len(all_records)}")

# ── Guard ─────────────────────────────────────────────────────────────────────

if not all_records:
    print("\nNo records returned.")
    print("Saving empty placeholder — school weight slider will be disabled in app.")
    out = pd.DataFrame({
        "zip_code":      list(target_zips),
        "school_count":  0,
        "school_rating": np.nan
    })
    out.to_csv(OUTPUT, index=False)
    raise SystemExit(0)

# ── Build DataFrame ───────────────────────────────────────────────────────────

df = pd.DataFrame(all_records)
print(f"\nColumns available: {list(df.columns)}\n")

# Normalize ZIP — Urban Institute returns zip_mailing or zip_location
zip_col = next((c for c in df.columns if "zip" in c.lower()), None)
if zip_col is None:
    print("No ZIP column found in response — saving empty file.")
    out = pd.DataFrame({
        "zip_code":      list(target_zips),
        "school_count":  0,
        "school_rating": np.nan
    })
    out.to_csv(OUTPUT, index=False)
    raise SystemExit(0)

print(f"Using ZIP column: '{zip_col}'")
df["zip_code"] = df[zip_col].astype(str).str[:5].str.zfill(5)

# Filter to target ZIPs
df_filtered = df[df["zip_code"].isin(target_zips)].copy()
print(f"Schools in target ZIPs: {len(df_filtered)}")

if df_filtered.empty:
    print("No schools matched target ZIPs.")
    out = pd.DataFrame({
        "zip_code":      list(target_zips),
        "school_count":  0,
        "school_rating": np.nan
    })
    out.to_csv(OUTPUT, index=False)
    raise SystemExit(0)

# ── Quality score proxy ───────────────────────────────────────────────────────

# 1. Title I  (column: title_i_status or similar)
title_col = next((c for c in df_filtered.columns if "title" in c.lower()), None)
if title_col:
    print(f"Title I column: '{title_col}'")
    # Typical coding: 1=eligible, 2=schoolwide, 5=not eligible, 6=not applicable
    title_map = {1: 0.3, 2: 0.0, 5: 0.8, 6: 1.0}
    df_filtered["title_i_score"] = (
        pd.to_numeric(df_filtered[title_col], errors="coerce")
        .map(title_map)
        .fillna(0.5)
    )
else:
    print("No Title I column found — using neutral score 0.5")
    df_filtered["title_i_score"] = 0.5

# 2. Locale type  (column: urban_centric_locale or locale)
locale_col = next((c for c in df_filtered.columns if "locale" in c.lower()), None)
if locale_col:
    print(f"Locale column: '{locale_col}'")
    def locale_score(code):
        try:
            code = int(float(code))
        except (ValueError, TypeError):
            return 0.5
        if 21 <= code <= 23: return 1.0   # suburb
        if 31 <= code <= 33: return 0.8   # town
        if 11 <= code <= 13: return 0.5   # city
        if 41 <= code <= 43: return 0.4   # rural
        return 0.5
    df_filtered["locale_score"] = df_filtered[locale_col].apply(locale_score)
else:
    print("No locale column found — using neutral score 0.5")
    df_filtered["locale_score"] = 0.5

# 3. Student-teacher ratio  (column: teachers_fte or similar)
# Urban Institute provides teachers_fte and enrollment — compute ratio
teachers_col   = next((c for c in df_filtered.columns if "teacher" in c.lower() and "fte" in c.lower()), None)
enrollment_col = next((c for c in df_filtered.columns if c.lower() in ("enrollment", "students")), None)

if teachers_col and enrollment_col:
    print(f"Teacher col: '{teachers_col}', Enrollment col: '{enrollment_col}'")
    t = pd.to_numeric(df_filtered[teachers_col],   errors="coerce").replace(0, np.nan)
    e = pd.to_numeric(df_filtered[enrollment_col], errors="coerce")
    ratio = (e / t).fillna(20).clip(10, 35)
    df_filtered["ratio_score"] = 1 - ((ratio - 10) / 25)
else:
    print("Teacher/enrollment columns not found — using neutral score 0.5")
    df_filtered["ratio_score"] = 0.5

# Composite
df_filtered["quality_score"] = (
    df_filtered["title_i_score"] * 0.40 +
    df_filtered["locale_score"]  * 0.35 +
    df_filtered["ratio_score"]   * 0.25
)

# ── Aggregate by ZIP ──────────────────────────────────────────────────────────

id_col = next((c for c in df_filtered.columns if "ncessch" in c.lower()), df_filtered.columns[0])

agg = df_filtered.groupby("zip_code").agg(
    school_count  = (id_col,         "count"),
    school_rating = ("quality_score", "mean")
).reset_index()

agg["school_rating"] = (agg["school_rating"] * 10).round(2)

# Fill any missing target ZIPs
all_zips_df = pd.DataFrame({"zip_code": list(target_zips)})
agg = all_zips_df.merge(agg, on="zip_code", how="left")
agg["school_count"] = agg["school_count"].fillna(0).astype(int)

# ── Save ──────────────────────────────────────────────────────────────────────

agg.to_csv(OUTPUT, index=False)

print(f"\nSaved {OUTPUT}  ({len(agg)} rows)")
print(f"\nTop 10 ZIPs by school rating:")
print(agg.sort_values("school_rating", ascending=False).head(10).to_string(index=False))
print(f"\nRating range: {agg['school_rating'].min():.1f} – {agg['school_rating'].max():.1f} / 10")
print(f"Avg schools per ZIP: {agg['school_count'].mean():.1f}")
print(f"\nDrop {OUTPUT} in your project folder and restart the app.")