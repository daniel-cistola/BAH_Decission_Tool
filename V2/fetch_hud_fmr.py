# -*- coding: utf-8 -*-
"""
fetch_hud_fmr.py
----------------
Fetches current Small Area Fair Market Rents (SAFMRs) for San Diego County
from the HUD USER API, replacing the static fy2026_safmrs.xlsx file.

HUD API requires a FREE token — register at:
  https://www.huduser.gov/hudapi/public/register

Set your token in the HUD_TOKEN variable below.

Output: hud_safmr_live.csv  (columns: zip_code, rent_1br, rent_2br, rent_3br)

Run annually — HUD releases updated SAFMRs each October.

Dependencies:
    pip install requests pandas
"""

import os
import requests
import pandas as pd

# ── Config ────────────────────────────────────────────────────────────────────

# Paste your new token here
HUD_TOKEN = os.environ.get("HUD_API_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiI2IiwianRpIjoiMzE3MmVlOWEyMTNkMTU3ZTBiMmZjNDc2MzYxMTk1ZDczOWM5OTFmNGE3OGQ4OGFkMTYyZjM5MjMyOGJjNzQ2MjUwNmUxZTY5MWRjNzU2Y2QiLCJpYXQiOjE3NzM2MTkyMzIuMDQ2OTcxLCJuYmYiOjE3NzM2MTkyMzIuMDQ2OTczLCJleHAiOjIwODkyMzg0MzIuMDQxMDk2LCJzdWIiOiIxMjI4MzIiLCJzY29wZXMiOltdfQ.Mc611BCcGwI01TCME80c4Bbfa3VIxekbg27jtpAPzNWL5llbcVX4vnMZVyL2zFC6JTWl9zKa4W4z1cl5_VVu_w")

# San Diego-Carlsbad MSA CBSA code — this is the correct entity ID for SAFMR lookups
# San Diego is a designated Small Area FMR area so data comes back ZIP by ZIP
SD_ENTITY_ID = "METRO41740M41740"

BASE_URL = "https://www.huduser.gov/hudapi/public/fmr"
OUTPUT   = "hud_safmr_live.csv"

# Payment standard = 110% of SAFMR (what BAH is designed to cover)
PAYMENT_STANDARD = 1.10

# ── Token check ───────────────────────────────────────────────────────────────

if HUD_TOKEN == "YOUR_NEW_TOKEN_HERE":
    print("=" * 60)
    print("ERROR: No HUD API token set.")
    print("Register for a free token at:")
    print("  https://www.huduser.gov/hudapi/public/register")
    print("Then paste it into fetch_hud_fmr.py line 24")
    print("=" * 60)
    raise SystemExit(1)

headers = {
    "Authorization": f"Bearer {HUD_TOKEN}",
    "Accept":        "application/json",
}

# ── Step 1: Confirm entity ID via listCounties ────────────────────────────────
# This also validates your token before the main call

print("Validating token and finding San Diego entity ID...")
try:
    r = requests.get(
        f"{BASE_URL}/listCounties/CA",
        headers=headers,
        timeout=30
    )
    if r.status_code == 401:
        print("401 Unauthorized — token is invalid or expired.")
        print("Get a new token at https://www.huduser.gov/hudapi/public/register")
        raise SystemExit(1)
    if r.status_code == 403:
        print("403 Forbidden — you may not have registered for the FMR dataset.")
        print("Log in at huduser.gov and ensure FMR API access is enabled for your token.")
        raise SystemExit(1)
    r.raise_for_status()
    raw = r.json()
    counties = raw.get("data", raw) if isinstance(raw, dict) else raw
    sd = [c for c in counties if isinstance(c, dict) and "San Diego" in str(c.get("county_name",""))]
    if sd:
        print(f"Found San Diego entry: {sd[0]}")
    else:
        print("San Diego not found in county list — proceeding with known CBSA code.")
except requests.exceptions.RequestException as e:
    print(f"County list request failed: {e}")
    print("Proceeding with known CBSA code anyway...")

# ── Step 2: Fetch SAFMR data for San Diego MSA ───────────────────────────────

print(f"\nFetching SAFMR data for entity: {SD_ENTITY_ID}...")
print(f"URL: {BASE_URL}/data/{SD_ENTITY_ID}")

try:
    resp = requests.get(
        f"{BASE_URL}/data/{SD_ENTITY_ID}",
        headers=headers,
        timeout=30
    )

    if resp.status_code == 401:
        print("401 Unauthorized — token rejected.")
        raise SystemExit(1)
    if resp.status_code == 404:
        print(f"404 Not Found for entity {SD_ENTITY_ID}")
        print("The CBSA code may have changed. Check:")
        print("  https://www.huduser.gov/hudapi/public/fmr/listMetroAreas")
        raise SystemExit(1)

    resp.raise_for_status()
    data = resp.json().get("data", {})

except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")
    raise SystemExit(1)

# ── Step 3: Parse ZIP-level basicdata ─────────────────────────────────────────
# San Diego is a Small Area FMR area, so basicdata is a list of ZIP records
# Field names per HUD docs: "One-Bedroom", "Two-Bedroom", "Three-Bedroom"

print(f"\nResponse keys: {list(data.keys())}")
print(f"Small Area status: {data.get('smallarea_status')}")
print(f"Year: {data.get('year')}")
print(f"Metro: {data.get('metro_name')}")

basicdata = data.get("basicdata", [])

if not basicdata:
    print("\nNo basicdata returned. Full response:")
    print(str(data)[:500])
    raise SystemExit(1)

if isinstance(basicdata, dict):
    # Non-small-area response — single county-level record, no ZIP breakdown
    print("WARNING: Got county-level data instead of ZIP-level.")
    print("San Diego should return ZIP-level SAFMRs. Check entity ID.")
    print(f"Basicdata: {basicdata}")
    raise SystemExit(1)

print(f"\nZIP-level records returned: {len(basicdata)}")
print(f"Sample record: {basicdata[1] if len(basicdata) > 1 else basicdata[0]}")

# ── Step 4: Build output DataFrame ───────────────────────────────────────────

records = []
for item in basicdata:
    zc = str(item.get("zip_code", "")).strip()
    if not zc or zc.upper() == "MSA LEVEL":
        continue   # skip the MSA-level summary row

    # HUD field names exactly as documented
    br1 = float(item.get("One-Bedroom",   item.get("one_bedroom",   0)) or 0)
    br2 = float(item.get("Two-Bedroom",   item.get("two_bedroom",   0)) or 0)
    br3 = float(item.get("Three-Bedroom", item.get("three_bedroom", 0)) or 0)

    records.append({
        "zip_code": zc.zfill(5),
        "rent_1br": round(br1 * PAYMENT_STANDARD),
        "rent_2br": round(br2 * PAYMENT_STANDARD),
        "rent_3br": round(br3 * PAYMENT_STANDARD),
    })

if not records:
    print("Could not extract any ZIP records. Check sample record above.")
    raise SystemExit(1)

# ── Step 5: Save ──────────────────────────────────────────────────────────────

df = pd.DataFrame(records).drop_duplicates(subset=["zip_code"])
df.to_csv(OUTPUT, index=False)

print(f"\nSaved {OUTPUT}  ({len(df)} ZIP codes)")
print(f"\nSample (first 5):")
print(df.head().to_string(index=False))
print(f"\n1BR range (110% payment standard): ${df['rent_1br'].min():,} – ${df['rent_1br'].max():,}")
print(f"\nDrop {OUTPUT} in your project folder and restart the app.")
print("The app will show '✅ Live API data' in the sidebar status panel.")