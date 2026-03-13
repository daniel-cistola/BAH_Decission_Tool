# -*- coding: utf-8 -*-
"""
prepare_dashboard_data.py
Builds dashboard_dataset.csv from raw source files.

Added in Financial Intelligence update:
  - Multi-bedroom SAFMR columns (1BR / 2BR / 3BR at 110% payment standard)
  - Rentcast market data (by-bedroom actuals, listings count, days on market)
"""

import os
import pandas as pd

# Always resolve data files relative to this script's own directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("Loading datasets...")

crime1 = pd.read_csv("san_diego_crime_dataset.csv")
crime2 = pd.read_excel("pd_nibrs_2025_datasd.xlsx")

zip_table  = pd.read_csv("zip_table_clean.csv")
safmr      = pd.read_excel("fy2026_safmrs.xlsx")
rentcast   = pd.read_excel("rentcast_project_ready.xlsx")

# ---------------------------------------------------
# CLEAN COLUMN NAMES
# ---------------------------------------------------

def clean_columns(df):
    df.columns = (
        df.columns
        .str.lower()
        .str.replace("\n", " ", regex=True)
        .str.strip()
    )
    return df

crime1   = clean_columns(crime1)
crime2   = clean_columns(crime2)
zip_table = clean_columns(zip_table)
safmr    = clean_columns(safmr)
rentcast = clean_columns(rentcast)

print("Columns cleaned")

# ---------------------------------------------------
# STANDARDIZE CRIME DATASET 2
# ---------------------------------------------------

crime2 = crime2.rename(columns={
    "zip": "zip_code",
    "latitude": "lat",
    "longitude": "lon",
    "ibr_offense_description": "offense"
})

# ---------------------------------------------------
# CLEAN ZIP CODES
# ---------------------------------------------------

def clean_zip(series):
    return (
        series
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.strip()
        .str.zfill(5)
    )

crime1["zip_code"]    = clean_zip(crime1["zip_code"])
crime2["zip_code"]    = clean_zip(crime2["zip_code"])
zip_table["zip_code"] = clean_zip(zip_table["zip_code"])
safmr["zip code"]     = clean_zip(safmr["zip code"])
rentcast["zip_code"]  = clean_zip(rentcast["zip_code"])

crime1 = crime1[crime1["zip_code"].str.len() == 5]
crime2 = crime2[crime2["zip_code"].str.len() == 5]

print("ZIP codes standardized")

# ---------------------------------------------------
# ASSIGN CRIME WEIGHTS
# ---------------------------------------------------

print("Assigning crime weights")

weight_map = {
    "aggravated assault": 9,
    "robbery":            9,
    "burglary":           6,
    "arson":              6,
    "motor vehicle theft": 4,
    "larceny":            4
}

crime2["weight"] = (
    crime2["offense"]
    .astype(str).str.lower().str.strip()
    .map(weight_map)
    .fillna(3)
)

crime1["weight"] = crime1["weight"].fillna(3)

# ---------------------------------------------------
# COMBINE & DEDUPE CRIME
# ---------------------------------------------------

print("Combining crime datasets")

crime = pd.concat([
    crime1[["zip_code", "lat", "lon", "offense", "weight"]],
    crime2[["zip_code", "lat", "lon", "offense", "weight"]]
], ignore_index=True)

print("Total crimes loaded:", len(crime))

crime = crime.drop_duplicates(subset=["lat", "lon", "offense"])

print("Crimes after dedupe:", len(crime))

# ---------------------------------------------------
# AGGREGATE CRIME BY ZIP
# ---------------------------------------------------

crime_summary = crime.groupby("zip_code").agg(
    crime_count=("offense", "count"),
    crime_score=("weight",  "sum")
).reset_index()

print("ZIPs with crime data:", len(crime_summary))

# ---------------------------------------------------
# BASE DATASET: zip_table + crime
# ---------------------------------------------------

dataset = zip_table.merge(crime_summary, on="zip_code", how="left")
dataset["crime_count"] = dataset["crime_count"].fillna(0)
dataset["crime_score"] = dataset["crime_score"].fillna(0)

dataset["crime_rate"] = (
    dataset["crime_score"] /
    dataset["population"].replace(0, pd.NA)
) * 1000
dataset["crime_rate"] = dataset["crime_rate"].fillna(0)

# ---------------------------------------------------
# SAFMR RENT  —  1BR / 2BR / 3BR at 110% standard
# ---------------------------------------------------

print("Preparing SAFMR rent data")

def parse_currency(series):
    return (
        series.astype(str)
        .str.replace(r'[\$,]', '', regex=True)
        .str.strip()
        .replace('nan', float('nan'))
        .astype(float)
    )

rent_cols = {
    "zip code":                              "zip_code",
    "safmr 1br - 110% payment standard":    "rent_1br",
    "safmr 2br - 110% payment standard":    "rent_2br",
    "safmr 3br - 110% payment standard":    "rent_3br",
}

rent = safmr[list(rent_cols.keys())].copy()
rent = rent.rename(columns=rent_cols)

for col in ["rent_1br", "rent_2br", "rent_3br"]:
    rent[col] = parse_currency(rent[col])

# Keep one row per ZIP (some ZIPs appear twice in SAFMR; take the first)
rent = rent.drop_duplicates(subset="zip_code", keep="first")

dataset = dataset.merge(rent, on="zip_code", how="left")

# Backward-compatible alias used by the map color logic
dataset["rent"] = dataset["rent_1br"]

# ---------------------------------------------------
# RENTCAST MARKET DATA
# ---------------------------------------------------

print("Merging Rentcast data")

rc_cols = {
    "zip_code":                "zip_code",
    "total_listings":          "listings_total",
    "new_listings":            "listings_new",
    "average_days_on_market":  "days_on_market",
    "avg_rent_bed_1":          "rentcast_1br",
    "avg_rent_bed_2":          "rentcast_2br",
    "avg_rent_bed_3":          "rentcast_3br",
}

rc = rentcast[list(rc_cols.keys())].copy()
rc = rc.rename(columns=rc_cols)
rc = rc.drop_duplicates(subset="zip_code", keep="first")

dataset = dataset.merge(rc, on="zip_code", how="left")

# ---------------------------------------------------
# SORT & SAVE
# ---------------------------------------------------

dataset = dataset.sort_values("crime_rate")

print("Saving dataset")
dataset.to_csv("dashboard_dataset.csv", index=False)

print("Done — Final rows:", len(dataset))
print("Columns:", dataset.columns.tolist())

# ---------------------------------------------------
# SAVE CRIME POINTS FOR HEATMAP
# Only NIBRS (crime2) has valid WGS84 coordinates.
# crime1 uses a projected CRS and cannot be plotted directly.
# ---------------------------------------------------

print("Saving crime points for heatmap")

crime_points = crime2[
    crime2["lat"].between(32.4, 33.2) &
    crime2["lon"].between(-117.7, -116.8)
][["lat", "lon", "weight", "offense"]].copy()

crime_points = crime_points.drop_duplicates(subset=["lat", "lon", "offense"])

crime_points.to_csv("crime_points.csv", index=False)

print(f"Crime points saved: {len(crime_points)} records")