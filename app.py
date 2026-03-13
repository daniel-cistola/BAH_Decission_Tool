import os
import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium

# Always resolve data files relative to this script's own directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

st.set_page_config(layout="wide", page_title="Military Housing Decision Tool")

st.title("Military Housing Decision Tool")
st.caption("Naval Base San Diego  ·  FY2026 Data")

# ==============================================================
# CONSTANTS
# ==============================================================

SAN_DIEGO_MHA  = "CA038"
BASE_LAT       = 32.69
BASE_LON       = -117.12
IRS_RATE       = 0.70        # $/mile (2025 IRS standard mileage rate)
WORKDAYS_MONTH = 21.7        # average workdays per month

RANK_DISPLAY = [
    "E01","E02","E03","E04","E05","E06","E07","E08","E09",
    "W01","W02","W03","W04","W05",
    "O01","O02","O03","O04","O05","O06","O07"
]

# ==============================================================
# LOAD BAH TABLE  (cached — runs once)
# ==============================================================

@st.cache_data
def load_bah():
    """Return two dicts: bah_with[rank] and bah_without[rank] for San Diego."""
    def read_sheet(sheet):
        df = pd.read_excel("2026 BAH Rates.xlsx", sheet_name=sheet, header=1)
        sd = df[df["MHA_NAME"].astype(str).str.upper().str.contains("SAN DIEGO")]
        if sd.empty:
            return {}
        row = sd.iloc[0]
        return {col: int(row[col]) for col in df.columns if col not in ("MHA","MHA_NAME")}

    return read_sheet("With"), read_sheet("Without")

bah_with, bah_without = load_bah()

# ==============================================================
# LOAD GEODATA + DASHBOARD DATASET  (cached)
# ==============================================================

@st.cache_data
def load_data():
    dataset = pd.read_csv("dashboard_dataset.csv")
    geo     = gpd.read_file("san_diego_zips.geojson")

    dataset["zip_code"]    = dataset["zip_code"].astype(str).str.zfill(5)
    geo["ZCTA5CE20"]       = geo["ZCTA5CE20"].astype(str).str.zfill(5)

    geo = geo.merge(dataset, left_on="ZCTA5CE20", right_on="zip_code")
    return geo

@st.cache_data
def load_crime_points():
    """
    Load crime point data directly from the NIBRS Excel file.
    Falls back to crime_points.csv if the Excel is not present.
    Filters to valid San Diego WGS84 bounding box only.
    """
    weight_map = {
        "aggravated assault":              9,
        "robbery":                         9,
        "rape":                            9,
        "murder and nonnegligent homicide":9,
        "burglary":                        6,
        "arson":                           6,
        "motor vehicle theft":             4,
        "larceny":                         4,
    }

    try:
        cp = pd.read_excel("pd_nibrs_2025_datasd.xlsx",
                           usecols=["latitude","longitude","ibr_offense_description"])
        cp = cp.rename(columns={
            "latitude":                  "lat",
            "longitude":                 "lon",
            "ibr_offense_description":   "offense"
        })
    except FileNotFoundError:
        try:
            cp = pd.read_csv("crime_points.csv")
        except FileNotFoundError:
            st.warning("⚠️ Crime point file not found — heatmap disabled.")
            return pd.DataFrame(columns=["lat","lon","weight","offense"])

    cp = cp.dropna(subset=["lat","lon"])
    cp = cp[cp["lat"].between(32.4, 33.2) & cp["lon"].between(-117.7, -116.8)]
    cp["offense"] = cp["offense"].astype(str).str.lower().str.strip()
    cp["weight"]  = cp["offense"].map(weight_map).fillna(3)
    return cp.reset_index(drop=True)

geo          = load_data()
crime_points = load_crime_points()

# ==============================================================
# SIDEBAR  ─  USER INPUTS
# ==============================================================

st.sidebar.header("Your Profile")

rank       = st.sidebar.selectbox("Rank", RANK_DISPLAY, index=4)   # default E05
dependents = st.sidebar.checkbox("With Dependents")
bedrooms   = st.sidebar.radio(
    "Bedrooms Needed",
    options=[1, 2, 3],
    format_func=lambda x: f"{x}BR",
    horizontal=True
)

st.sidebar.divider()
st.sidebar.header("Scoring Weights")
rent_weight     = st.sidebar.slider("Rental Cost",  1, 5, 3)
safety_weight   = st.sidebar.slider("Safety",       1, 5, 3)
distance_weight = st.sidebar.slider("Distance",     1, 5, 3)

st.sidebar.divider()
st.sidebar.header("Map Layers")
show_heatmap   = st.sidebar.checkbox("Show Crime Heatmap", value=True)
heatmap_filter = st.sidebar.radio(
    "Heatmap Crimes",
    options=["All Crimes", "Violent Only"],
    horizontal=True,
    disabled=not show_heatmap
)

st.sidebar.divider()
with st.sidebar.expander("ℹ️ Free API Data Sources"):
    st.markdown("""
**Currently integrated:**
- HUD SAFMR (FY2026)
- DoD BAH Rates (FY2026)
- Rentcast market data

**Free APIs to add later:**
- [Census ACS API](https://api.census.gov) — income, household size by ZIP *(no key needed)*
- [HUD API](https://www.huduser.gov/portal/dataset/fmr-api.html) — programmatic FMR pulls
- [Walk Score API](https://www.walkscore.com/professional/api.php) — free tier walkability
- [EPA EJScreen API](https://ejscreen.epa.gov/mapper/ejscreenapi.aspx) — environmental quality
- [NCES API](https://nces.ed.gov/ccd/) — school district quality (PCS families)
- [BLS CPI API](https://www.bls.gov/developers/) — cost-of-living trends *(free key)*
""")

# ==============================================================
# BAH LOOKUP
# ==============================================================

# Map rank col name: O01 in sidebar = "O01" in BAH table
# (no prior-enlisted distinction exposed in UI)
bah_table = bah_with if dependents else bah_without
BAH        = bah_table.get(rank, 0)

# ==============================================================
# SELECT CORRECT RENT COLUMN BY BEDROOM CHOICE
# ==============================================================

rent_col_map = {1: "rent_1br", 2: "rent_2br", 3: "rent_3br"}
rentcast_col_map = {1: "rentcast_1br", 2: "rentcast_2br", 3: "rentcast_3br"}

rent_col     = rent_col_map[bedrooms]
rentcast_col = rentcast_col_map[bedrooms]

# Use Rentcast actual-market rent where available, fall back to SAFMR
geo["effective_rent"] = geo[rentcast_col].combine_first(geo[rent_col])

# ==============================================================
# FINANCIAL CALCULATIONS
# ==============================================================

# Monthly commute cost  (round-trip, IRS mileage rate)
geo["commute_monthly"] = (
    geo["distance_mi"] * 2 * WORKDAYS_MONTH * IRS_RATE
).round(0)

# True housing cost  =  rent + commute
geo["true_cost"] = geo["effective_rent"] + geo["commute_monthly"]

# BAH surplus (+) or gap (-)
geo["bah_surplus"] = (BAH - geo["effective_rent"]).round(0)
geo["bah_surplus_vs_true"] = (BAH - geo["true_cost"]).round(0)

# Affordability: rent alone must be ≤ 110% BAH
geo["affordable"] = geo["effective_rent"] <= (1.10 * BAH)

# ==============================================================
# COMPOSITE SCORE
# ==============================================================

geo["rent_score"]     = 1 - (geo["effective_rent"] / geo["effective_rent"].max())
geo["safety_score"]   = 1 - (geo["crime_rate"]    / geo["crime_rate"].max())
geo["distance_score"] = 1 - (geo["distance_mi"]   / geo["distance_mi"].max())

geo["score"] = (
    rent_weight     * geo["rent_score"] +
    safety_weight   * geo["safety_score"] +
    distance_weight * geo["distance_score"]
)

q75 = geo["score"].quantile(0.75)
q40 = geo["score"].quantile(0.40)

# ==============================================================
# MAP COLOR LOGIC
# ==============================================================

def zip_color(row):
    if not row["affordable"]:
        return "red"
    if row["score"] > q75:
        return "green"
    if row["score"] > q40:
        return "yellow"
    return "black"

# ==============================================================
# FINANCIAL INTELLIGENCE HEADER PANEL
# ==============================================================

dep_label = "w/ Dependents" if dependents else "No Dependents"
surplus_color = "green" if BAH > 0 else "red"

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label=f"Your BAH  ({rank}, {dep_label})",
        value=f"${BAH:,}" if BAH else "Rank not found"
    )
with col2:
    st.metric(
        label=f"Median {bedrooms}BR Market Rent (SD)",
        value=f"${geo['effective_rent'].median():,.0f}"
    )
with col3:
    median_surplus = BAH - geo["effective_rent"].median()
    st.metric(
        label="Median BAH Surplus",
        value=f"${median_surplus:,.0f}",
        delta=f"{'Above' if median_surplus >= 0 else 'Below'} BAH"
    )
with col4:
    affordable_count = geo["affordable"].sum()
    st.metric(
        label="Affordable ZIPs",
        value=f"{affordable_count} / {len(geo)}"
    )

st.divider()

# ==============================================================
# MAP
# ==============================================================

map_col, table_col = st.columns([3, 2])

with map_col:
    st.subheader("ZIP Code Map")

    if not crime_points.empty:
        st.caption(f"🔥 Heatmap: {len(crime_points):,} crime incidents loaded · "
                   f"{'Violent only' if heatmap_filter == 'Violent Only' else 'All crime types'} · "
                   f"Toggle in sidebar")
    else:
        st.caption("⚠️ Heatmap data not loaded — check pd_nibrs_2025_datasd.xlsx is present")

    m = folium.Map(location=[32.72, -117.16], zoom_start=10)

    folium.Marker(
        location=[BASE_LAT, BASE_LON],
        popup="Naval Base San Diego",
        icon=folium.Icon(color="blue", icon="anchor", prefix="fa")
    ).add_to(m)

    for r_meters in [16093, 32186, 48280]:
        folium.Circle(
            location=[BASE_LAT, BASE_LON],
            radius=r_meters,
            color="#444",
            fill=False,
            weight=1,
            dash_array="6"
        ).add_to(m)

    # Legend — explicit colors so it's legible on any base map theme
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
                background:#ffffff;color:#111111;
                padding:10px 16px;border-radius:8px;
                border:2px solid #555;font-size:13px;
                font-family:Arial,sans-serif;line-height:2;
                box-shadow:2px 2px 6px rgba(0,0,0,0.4);">
        <div style="font-weight:bold;margin-bottom:4px;color:#111;">Border Color Key</div>
        <div><span style="display:inline-block;width:16px;height:16px;background:#2ca02c;border:1px solid #222;vertical-align:middle;margin-right:6px;"></span><span style="color:#111;">Affordable + High Score</span></div>
        <div><span style="display:inline-block;width:16px;height:16px;background:#f0c030;border:1px solid #222;vertical-align:middle;margin-right:6px;"></span><span style="color:#111;">Affordable + Mid Score</span></div>
        <div><span style="display:inline-block;width:16px;height:16px;background:#444444;border:1px solid #222;vertical-align:middle;margin-right:6px;"></span><span style="color:#111;">Affordable + Low Score</span></div>
        <div><span style="display:inline-block;width:16px;height:16px;background:#d62728;border:1px solid #222;vertical-align:middle;margin-right:6px;"></span><span style="color:#111;">Over BAH (unaffordable)</span></div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # ==============================================================
    # CRIME HEATMAP LAYER
    # ==============================================================

    if show_heatmap:
        from folium.plugins import HeatMap

        if crime_points.empty:
            st.warning("⚠️ No crime point data loaded — check that pd_nibrs_2025_datasd.xlsx is in the project folder.")
        else:
            violent_offenses = {
                "aggravated assault", "robbery", "rape",
                "murder and nonnegligent homicide", "human trafficking"
            }

            if heatmap_filter == "Violent Only":
                hm_data = crime_points[crime_points["offense"].isin(violent_offenses)]
            else:
                hm_data = crime_points

            heat_points = hm_data[["lat","lon","weight"]].dropna().values.tolist()

            HeatMap(
                heat_points,
                name="Crime Heatmap",
                min_opacity=0.35,
                max_zoom=15,
                radius=12,
                blur=15,
                gradient={0.2: "#0000ff", 0.45: "#00ff00", 0.75: "#ffaa00", 1.0: "#ff0000"}
            ).add_to(m)

    folium.GeoJson(
        geo,
        style_function=lambda feature: {
            "fillColor":   zip_color(feature["properties"]),
            "color":       zip_color(feature["properties"]),
            "weight":      3,
            "fillOpacity": 0.08,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "zip_code", "effective_rent", "bah_surplus",
                "commute_monthly", "true_cost",
                "crime_rate", "distance_mi", "listings_total"
            ],
            aliases=[
                "ZIP", f"{bedrooms}BR Rent ($)", "BAH Surplus ($)",
                "Commute/Mo ($)", "True Cost ($)",
                "Crime Rate", "Distance (mi)", "Active Listings"
            ],
            localize=True
        )
    ).add_to(m)

    st_folium(m, width=None, height=620)

# ==============================================================
# FINANCIAL INTELLIGENCE TABLE
# ==============================================================

with table_col:
    st.subheader("Financial Intelligence")

    display_cols = ["zip_code", "effective_rent", "bah_surplus",
                    "commute_monthly", "true_cost", "listings_total", "distance_mi"]

    df_display = (
        geo[display_cols + ["score","affordable"]]
        .copy()
        .sort_values("bah_surplus", ascending=False)
    )

    df_display = df_display.rename(columns={
        "zip_code":       "ZIP",
        "effective_rent": f"{bedrooms}BR Rent",
        "bah_surplus":    "BAH Surplus",
        "commute_monthly":"Commute/Mo",
        "true_cost":      "True Cost",
        "listings_total": "Listings",
        "distance_mi":    "Miles",
        "score":          "Score",
        "affordable":     "Affordable"
    })

    # Format dollar columns
    for col in [f"{bedrooms}BR Rent", "BAH Surplus", "Commute/Mo", "True Cost"]:
        df_display[col] = df_display[col].apply(
            lambda x: f"${x:,.0f}" if pd.notna(x) else "—"
        )

    df_display["Miles"]    = df_display["Miles"].round(1)
    df_display["Score"]    = df_display["Score"].round(2)
    df_display["Listings"] = df_display["Listings"].fillna(0).astype(int)

    tab1, tab2, tab3 = st.tabs(["BAH Surplus by ZIP", "True Cost Analysis", "Composite Score"])

    with tab1:
        st.caption(f"BAH for {rank} {'w/ Deps' if dependents else 'No Deps'}: **${BAH:,}/mo**  ·  {bedrooms}BR rent source: Rentcast (SAFMR fallback)")
        st.dataframe(
            df_display[["ZIP", f"{bedrooms}BR Rent", "BAH Surplus", "Score", "Listings", "Miles"]],
            use_container_width=True,
            height=500
        )

    with tab2:
        st.caption("True Cost = market rent + estimated monthly commute (IRS $0.70/mi, round-trip, 21.7 days/mo)")
        df_truecost = df_display[["ZIP", f"{bedrooms}BR Rent", "Commute/Mo", "True Cost", "Score", "Miles"]].copy()
        st.dataframe(df_truecost, use_container_width=True, height=500)

    with tab3:
        st.caption(f"Composite score = rent×{rent_weight} + safety×{safety_weight} + distance×{distance_weight}  (all normalized 0–1, higher = better)")
        df_score = (
            df_display[["ZIP", "Score", f"{bedrooms}BR Rent", "BAH Surplus", "Miles"]]
            .copy()
            .sort_values("Score", ascending=False)
        )
        st.dataframe(df_score, use_container_width=True, height=500)

st.divider()

# ==============================================================
# BAH SURPLUS BAR CHART
# ==============================================================

st.subheader("BAH Surplus / Gap by ZIP  (sorted by surplus)")

chart_data = (
    geo[["zip_code","bah_surplus","affordable"]]
    .dropna(subset=["bah_surplus"])
    .sort_values("bah_surplus", ascending=False)
    .head(40)
    .copy()
)

chart_data["color"] = chart_data["bah_surplus"].apply(
    lambda x: "#2ecc71" if x >= 0 else "#e74c3c"
)

# Streamlit bar chart (native)
import altair as alt

chart = (
    alt.Chart(chart_data)
    .mark_bar()
    .encode(
        x=alt.X("zip_code:N", sort="-y", title="ZIP Code",
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("bah_surplus:Q", title="BAH Surplus / Gap ($)"),
        color=alt.condition(
            alt.datum.bah_surplus >= 0,
            alt.value("#2ecc71"),
            alt.value("#e74c3c")
        ),
        tooltip=[
            alt.Tooltip("zip_code:N", title="ZIP"),
            alt.Tooltip("bah_surplus:Q", title="Surplus ($)", format=",.0f")
        ]
    )
    .properties(height=300)
)

st.altair_chart(chart, use_container_width=True)

st.caption(
    f"Green bars = BAH surplus (rent below BAH).  "
    f"Red bars = BAH gap (rent exceeds BAH).  "
    f"Rent source: Rentcast {bedrooms}BR actuals where available, SAFMR 110% standard otherwise."
)