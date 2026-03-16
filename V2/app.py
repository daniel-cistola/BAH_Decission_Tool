import os
import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point

# Always resolve data files relative to this script's own directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

st.set_page_config(layout="wide", page_title="Military Housing Decision Tool")

# Session state — chart-driven map focus
if "chart_selected_zip" not in st.session_state:
    st.session_state["chart_selected_zip"] = None
if "table_selected_zip" not in st.session_state:
    st.session_state["table_selected_zip"] = None

st.title("Military Housing Decision Tool")
st.caption("Naval Base San Diego  ·  FY2026 Data")

# ==============================================================
# CONSTANTS
# ==============================================================

BASE_LAT       = 32.69
BASE_LON       = -117.12
IRS_RATE       = 0.70        # $/mile (2025 IRS standard mileage rate)
WORKDAYS_MONTH = 21.7        # average workdays per month

RANK_DISPLAY = [
    "E01","E02","E03","E04","E05","E06","E07","E08","E09",
    "W01","W02","W03","W04","W05",
    "O01","O01E","O02","O02E","O03","O03E","O04","O05","O06","O07"
]

# ==============================================================
# LOAD BAH TABLE
# ==============================================================

@st.cache_data
def load_bah():
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
# LOAD CORE DATA
# ==============================================================

@st.cache_data
def load_data():
    dataset = pd.read_csv("dashboard_dataset.csv")
    geo     = gpd.read_file("san_diego_zips.geojson")
    dataset["zip_code"] = dataset["zip_code"].astype(str).str.zfill(5)
    geo["ZCTA5CE20"]    = geo["ZCTA5CE20"].astype(str).str.zfill(5)
    geo = geo.merge(dataset, left_on="ZCTA5CE20", right_on="zip_code")
    return geo

@st.cache_data
def load_crime_points():
    weight_map = {
        "aggravated assault":               9,
        "robbery":                          9,
        "rape":                             9,
        "murder and nonnegligent homicide": 9,
        "burglary":                         6,
        "arson":                            6,
        "motor vehicle theft":              4,
        "larceny":                          4,
    }
    try:
        cp = pd.read_excel("pd_nibrs_2025_datasd.xlsx",
                           usecols=["latitude","longitude","ibr_offense_description"])
        cp = cp.rename(columns={
            "latitude":                "lat",
            "longitude":               "lon",
            "ibr_offense_description": "offense"
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

# ==============================================================
# LOAD OPTIONAL ENRICHMENT DATA
# ==============================================================

@st.cache_data
def load_drive_times():
    try:
        dt = pd.read_csv("drive_times.csv")
        dt["zip_code"] = dt["zip_code"].astype(str).str.zfill(5)
        return dt
    except FileNotFoundError:
        return None

@st.cache_data
def load_schools():
    try:
        sc = pd.read_csv("schools_by_zip.csv")
        sc["zip_code"] = sc["zip_code"].astype(str).str.zfill(5)
        return sc
    except FileNotFoundError:
        return None

@st.cache_data
def load_amenities():
    try:
        return gpd.read_file("amenities.geojson")
    except FileNotFoundError:
        return None

@st.cache_data
def load_hud_live():
    """Load live HUD SAFMR data if fetch_hud_fmr.py has been run."""
    try:
        hud = pd.read_csv("hud_safmr_live.csv")
        hud["zip_code"] = hud["zip_code"].astype(str).str.zfill(5)
        return hud
    except FileNotFoundError:
        return None

# Run all loaders
geo          = load_data()
crime_points = load_crime_points()
drive_times  = load_drive_times()
schools      = load_schools()
amenities    = load_amenities()
hud_live     = load_hud_live()

# Merge HUD live rent data — overrides static dashboard_dataset.csv rent columns
if hud_live is not None:
    for br_col in ["rent_1br","rent_2br","rent_3br"]:
        if br_col in hud_live.columns:
            geo = geo.drop(columns=[br_col], errors="ignore")
    geo = geo.merge(hud_live, on="zip_code", how="left")
    hud_rent_source = "HUD API (live)"
else:
    hud_rent_source = "Static FY2026 SAFMR"

# Merge optional enrichment into geo
if drive_times is not None:
    geo = geo.merge(drive_times[["zip_code","drive_minutes"]], on="zip_code", how="left")
else:
    geo["drive_minutes"] = None

if schools is not None:
    geo = geo.merge(
        schools[["zip_code","school_rating","school_count"]],
        on="zip_code", how="left"
    )
else:
    geo["school_rating"] = None
    geo["school_count"]  = 0

# ==============================================================
# SIDEBAR — USER INPUTS
# ==============================================================

st.sidebar.header("Your Profile")

rank       = st.sidebar.selectbox("Rank", RANK_DISPLAY, index=4)
dependents = st.sidebar.checkbox("With Dependents")
bedrooms   = st.sidebar.radio(
    "Bedrooms Needed",
    options=[1, 2, 3],
    format_func=lambda x: f"{x}BR",
    horizontal=True
)

st.sidebar.divider()
st.sidebar.header("Scoring Weights")
rent_weight     = st.sidebar.slider("Rental Cost", 1, 5, 3)
safety_weight   = st.sidebar.slider("Safety",      1, 5, 3)
distance_weight = st.sidebar.slider("Distance",    1, 5, 3)

schools_available = schools is not None and geo["school_rating"].notna().any()
school_weight = st.sidebar.slider(
    "Schools",
    1, 5, 2,
    help="Active — school ratings loaded" if schools_available
         else "Run fetch_schools.py to enable this weight"
)

st.sidebar.divider()
st.sidebar.header("Map Layers")
show_heatmap   = st.sidebar.checkbox("Show Crime Heatmap", value=True)
heatmap_filter = st.sidebar.radio(
    "Heatmap Crimes",
    options=["All Crimes", "Violent Only"],
    horizontal=True,
    disabled=not show_heatmap
)
amenities_disabled = amenities is None
amenities_help_off  = "Run fetch_amenities.py to enable this layer"

show_grocery     = st.sidebar.checkbox("🛒 Grocery Stores",  value=False, disabled=amenities_disabled, help=amenities_help_off if amenities_disabled else "")
show_gym         = st.sidebar.checkbox("🏋️ Gyms",            value=False, disabled=amenities_disabled, help=amenities_help_off if amenities_disabled else "")
show_urgent_care = st.sidebar.checkbox("🏥 Urgent Care",     value=False, disabled=amenities_disabled, help=amenities_help_off if amenities_disabled else "")

show_amenities = show_grocery or show_gym or show_urgent_care

st.sidebar.divider()

# Data status panel
with st.sidebar.expander("📡 Enrichment Data Status"):
    st.markdown(f"""
| Dataset | Status |
|---|---|
| Drive Times | {"✅ Loaded" if drive_times is not None else "⚙️ Run fetch_drive_times.py"} |
| School Ratings | {"✅ Loaded" if schools is not None else "⚙️ Run fetch_schools.py"} |
| Amenities | {"✅ Loaded" if amenities is not None else "⚙️ Run fetch_amenities.py"} |
| HUD Rent Data | {"✅ Live API data" if hud_live is not None else "⚙️ Run fetch_hud_fmr.py (needs free token)"} |
""")

with st.sidebar.expander("ℹ️ Free API Data Sources"):
    st.markdown("""
**Currently integrated:**
- HUD SAFMR (FY2026)
- DoD BAH Rates (FY2026)
- Rentcast market data

**Free APIs to add later:**
- [Census ACS API](https://api.census.gov) — income, household size by ZIP
- [Walk Score API](https://www.walkscore.com/professional/api.php) — walkability
- [EPA EJScreen API](https://ejscreen.epa.gov/mapper/ejscreenapi.aspx) — env quality
- [BLS CPI API](https://www.bls.gov/developers/) — cost-of-living trends
""")

# ==============================================================
# BAH LOOKUP
# ==============================================================

bah_table = bah_with if dependents else bah_without
BAH       = bah_table.get(rank, 0)

# ==============================================================
# RENT COLUMNS
# ==============================================================

rent_col_map     = {1: "rent_1br",     2: "rent_2br",     3: "rent_3br"}
rentcast_col_map = {1: "rentcast_1br", 2: "rentcast_2br", 3: "rentcast_3br"}

rent_col     = rent_col_map[bedrooms]
rentcast_col = rentcast_col_map[bedrooms]

geo["effective_rent"] = geo[rentcast_col].combine_first(geo[rent_col])

# ==============================================================
# FINANCIAL CALCULATIONS
# ==============================================================

# Use drive time if available (avg 30 mph in traffic), else straight-line
drive_times_available = geo["drive_minutes"].notna().any()
if drive_times_available:
    geo["commute_monthly"] = (
        geo["drive_minutes"] * 2 * WORKDAYS_MONTH * IRS_RATE * (30 / 60)
    ).round(0)
    commute_label = "Actual drive time (OSRM)"
else:
    geo["commute_monthly"] = (
        geo["distance_mi"] * 2 * WORKDAYS_MONTH * IRS_RATE
    ).round(0)
    commute_label = "Straight-line distance estimate"

geo["true_cost"]           = geo["effective_rent"] + geo["commute_monthly"]
geo["bah_surplus"]         = (BAH - geo["effective_rent"]).round(0)
geo["bah_surplus_vs_true"] = (BAH - geo["true_cost"]).round(0)
geo["affordable"]          = geo["effective_rent"] <= (1.10 * BAH)

# ==============================================================
# COMPOSITE SCORE  (MinMaxScaler — robust to outliers)
# ==============================================================

from sklearn.preprocessing import MinMaxScaler

scaler = MinMaxScaler()

# Features where LOWER is better — invert after scaling
_rent_raw     = geo["effective_rent"].fillna(geo["effective_rent"].median()).values.reshape(-1,1)
_crime_raw    = geo["crime_rate"].fillna(0).values.reshape(-1,1)
_dist_raw     = geo["distance_mi"].fillna(geo["distance_mi"].median()).values.reshape(-1,1)

geo["rent_score"]     = 1 - scaler.fit_transform(_rent_raw).flatten()
geo["safety_score"]   = 1 - scaler.fit_transform(_crime_raw).flatten()
geo["distance_score"] = 1 - scaler.fit_transform(_dist_raw).flatten()

if schools_available:
    _school_raw       = geo["school_rating"].fillna(geo["school_rating"].median()).values.reshape(-1,1)
    geo["school_score"] = scaler.fit_transform(_school_raw).flatten()   # higher is better, no invert
    geo["score"] = (
        rent_weight     * geo["rent_score"] +
        safety_weight   * geo["safety_score"] +
        distance_weight * geo["distance_score"] +
        school_weight   * geo["school_score"]
    )
else:
    geo["school_score"] = 0.0
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
# HEADER METRICS
# ==============================================================

dep_label = "w/ Dependents" if dependents else "No Dependents"

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
    surplus_color  = "#21c354" if median_surplus >= 0 else "#ff4b4b"
    st.markdown(f"""
        <div style="font-size:14px; color:rgba(255,255,255,0.5); margin-bottom:4px; font-weight:400;">Median BAH Surplus</div>
        <div style="font-size:2.25rem; font-weight:700; color:{surplus_color};
                    font-family: 'Source Sans Pro', sans-serif;
                    -webkit-font-smoothing: antialiased;
                    letter-spacing: -0.005em; line-height:1.2;">${median_surplus:,.0f}</div>
    """, unsafe_allow_html=True)
with col4:
    affordable_count = int(geo["affordable"].sum())
    st.metric(label="Affordable ZIPs", value=f"{affordable_count} / {len(geo)}")

st.divider()

# ==============================================================
# TOP PICKS CARD
# ==============================================================

affordable_geo = geo[geo["affordable"]]

if not affordable_geo.empty:
    best_overall = affordable_geo.loc[affordable_geo["score"].idxmax()]
    safest       = affordable_geo.loc[affordable_geo["safety_score"].idxmax()]
    best_value   = affordable_geo.loc[affordable_geo["bah_surplus"].idxmax()]

    st.subheader("🏆 Top Picks — Based on Your Profile")

    pick1, pick2, pick3 = st.columns(3)

    with pick1:
        drive_str = ""
        if pd.notna(best_overall.get("drive_minutes")):
            drive_str = f"  ·  Drive: **{int(best_overall['drive_minutes'])} min**"
        school_str = ""
        if schools_available and pd.notna(best_overall.get("school_rating")):
            school_str = f"  ·  Schools: **{best_overall['school_rating']:.1f}/10**"
        st.success(f"""
**🥇 Best Overall · {best_overall['zip_code']}**

Score: **{best_overall['score']:.2f}**  ·  Rent: **${best_overall['effective_rent']:,.0f}/mo**

BAH Surplus: **${best_overall['bah_surplus']:,.0f}**  ·  Distance: **{best_overall['distance_mi']:.1f} mi**{drive_str}{school_str}
""")

    with pick2:
        drive_str = ""
        if pd.notna(safest.get("drive_minutes")):
            drive_str = f"  ·  Drive: **{int(safest['drive_minutes'])} min**"
        st.info(f"""
**🛡️ Safest · {safest['zip_code']}**

Crime Rate: **{safest['crime_rate']:.2f}**  ·  Rent: **${safest['effective_rent']:,.0f}/mo**

BAH Surplus: **${safest['bah_surplus']:,.0f}**  ·  Distance: **{safest['distance_mi']:.1f} mi**{drive_str}
""")

    with pick3:
        drive_str = ""
        if pd.notna(best_value.get("drive_minutes")):
            drive_str = f"  ·  Drive: **{int(best_value['drive_minutes'])} min**"
        st.warning(f"""
**💰 Best Value · {best_value['zip_code']}**

BAH Surplus: **${best_value['bah_surplus']:,.0f}/mo**  ·  Rent: **${best_value['effective_rent']:,.0f}/mo**

Crime Rate: **{best_value['crime_rate']:.2f}**  ·  Distance: **{best_value['distance_mi']:.1f} mi**{drive_str}
""")

    st.divider()

# ==============================================================
# MAP + TABLE
# ==============================================================

map_col, table_col = st.columns([3, 2])

with map_col:
    st.subheader("ZIP Code Map")

    if not crime_points.empty:
        st.caption(
            f"🔥 Heatmap: {len(crime_points):,} crime incidents loaded · "
            f"{'Violent only' if heatmap_filter == 'Violent Only' else 'All crime types'} · "
            f"**Click any ZIP for a full breakdown**"
        )
    else:
        st.caption("⚠️ Heatmap data not loaded · **Click any ZIP for a full breakdown**")

    # If a ZIP was selected from the table or bar chart, center and zoom on it
    _selected_zip = st.session_state.get("table_selected_zip") or st.session_state.get("chart_selected_zip")
    if _selected_zip is not None:
        _zip_row = geo[geo["zip_code"] == _selected_zip]
        if not _zip_row.empty:
            _centroid  = _zip_row.geometry.centroid.iloc[0]
            _map_center = [_centroid.y, _centroid.x]
            _map_zoom   = 13
        else:
            _map_center = [32.72, -117.16]
            _map_zoom   = 10
    else:
        _map_center = [32.72, -117.16]
        _map_zoom   = 10

    m = folium.Map(location=_map_center, zoom_start=_map_zoom)

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

    # Crime heatmap layer
    if show_heatmap and not crime_points.empty:
        from folium.plugins import HeatMap
        violent_offenses = {
            "aggravated assault", "robbery", "rape",
            "murder and nonnegligent homicide", "human trafficking"
        }
        hm_data = (
            crime_points[crime_points["offense"].isin(violent_offenses)]
            if heatmap_filter == "Violent Only" else crime_points
        )
        if not hm_data.empty:
            HeatMap(
                hm_data[["lat","lon","weight"]].dropna().values.tolist(),
                name="Crime Heatmap",
                min_opacity=0.35,
                max_zoom=15,
                radius=12,
                blur=15,
                gradient={0.2:"#0000ff", 0.45:"#00ff00", 0.75:"#ffaa00", 1.0:"#ff0000"}
            ).add_to(m)

    # Amenity layer
    if show_amenities and amenities is not None:
        icon_map = {
            "grocery":     ("shopping-cart", "green"),
            "gym":         ("heartbeat",     "purple"),
            "urgent_care": ("plus-square",   "red"),
        }
        type_filter = set()
        if show_grocery:     type_filter.add("grocery")
        if show_gym:         type_filter.add("gym")
        if show_urgent_care: type_filter.add("urgent_care")

        for _, row in amenities.iterrows():
            atype = row.get("type", "")
            if atype not in type_filter:
                continue
            icon_name, icon_color = icon_map.get(atype, ("info-sign", "gray"))
            folium.Marker(
                location=[row.geometry.y, row.geometry.x],
                popup=row.get("name", atype.replace("_", " ").title()),
                icon=folium.Icon(color=icon_color, icon=icon_name, prefix="fa")
            ).add_to(m)

    # Tooltip fields — add optional columns if available
    tooltip_fields   = ["zip_code","effective_rent","bah_surplus","commute_monthly",
                        "true_cost","crime_rate","distance_mi","listings_total"]
    tooltip_aliases  = ["ZIP", f"{bedrooms}BR Rent ($)","BAH Surplus ($)",
                        "Commute/Mo ($)","True Cost ($)","Crime Rate",
                        "Distance (mi)","Active Listings"]

    if drive_times_available:
        tooltip_fields.append("drive_minutes")
        tooltip_aliases.append("Drive Time (min)")
    if schools_available:
        tooltip_fields.append("school_rating")
        tooltip_aliases.append("School Rating")

    folium.GeoJson(
        geo,
        style_function=lambda feature: {
            "fillColor":   zip_color(feature["properties"]),
            "color":       zip_color(feature["properties"]),
            "weight":      3,
            "fillOpacity": 0.08,
        }
    ).add_to(m)

    map_data = st_folium(m, width=None, height=620)

# ==============================================================
# FINANCIAL INTELLIGENCE TABLE
# ==============================================================

with table_col:
    st.subheader("Financial Intelligence")
    st.caption(
        f"True Cost = rent + commute ({commute_label}, IRS $0.70/mi, RT, 21.7 days/mo)  ·  "
        f"Score = rent×{rent_weight} + safety×{safety_weight} + distance×{distance_weight}"
        + (f" + schools×{school_weight}" if schools_available else "") +
        f" (normalized 0–1)  ·  "
        f"Crime Rate = weighted incidents per 1,000 residents  ·  "
        f"Click any row to zoom map  ·  Click column header to sort"
    )

    display_cols = ["zip_code","effective_rent","bah_surplus","commute_monthly",
                    "true_cost","crime_rate","listings_total","distance_mi","score"]

    if drive_times_available:
        display_cols.insert(display_cols.index("distance_mi") + 1, "drive_minutes")
    if schools_available:
        display_cols.append("school_rating")

    df_display = (
        geo[display_cols].copy()
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )

    rename_map = {
        "zip_code":       "ZIP",
        "effective_rent": "Rent",
        "bah_surplus":    "BAH Surplus",
        "commute_monthly":"Commute/Mo",
        "true_cost":      "True Cost",
        "crime_rate":     "Crime Rate",
        "listings_total": "Listings",
        "distance_mi":    "Miles",
        "score":          "Score",
        "drive_minutes":  "Drive (min)",
        "school_rating":  "Schools",
    }
    df_display = df_display.rename(columns=rename_map)

    for col in ["Rent","BAH Surplus","Commute/Mo","True Cost"]:
        df_display[col] = df_display[col].apply(
            lambda x: round(x) if pd.notna(x) else None
        )
    df_display["Crime Rate"] = df_display["Crime Rate"].round(2)
    df_display["Miles"]      = df_display["Miles"].round(1)
    df_display["Score"]      = df_display["Score"].round(2)
    df_display["Listings"]   = df_display["Listings"].fillna(0).astype(int)
    if "Drive (min)" in df_display.columns:
        df_display["Drive (min)"] = df_display["Drive (min)"].apply(
            lambda x: int(x) if pd.notna(x) else None
        )
    if "Schools" in df_display.columns:
        df_display["Schools"] = df_display["Schools"].round(1)

    col_config = {
        "ZIP":         st.column_config.TextColumn("ZIP"),
        "Rent":        st.column_config.NumberColumn("Rent",        format="$%d"),
        "BAH Surplus": st.column_config.NumberColumn("BAH Surplus", format="$%d"),
        "Commute/Mo":  st.column_config.NumberColumn("Commute/Mo",  format="$%d"),
        "True Cost":   st.column_config.NumberColumn("True Cost",   format="$%d"),
        "Crime Rate":  st.column_config.NumberColumn("Crime Rate",  format="%.2f"),
        "Miles":       st.column_config.NumberColumn("Miles",       format="%.1f mi"),
        "Score":       st.column_config.NumberColumn("Score",       format="%.2f"),
        "Listings":    st.column_config.NumberColumn("Listings"),
    }
    if "Drive (min)" in df_display.columns:
        col_config["Drive (min)"] = st.column_config.NumberColumn("Drive (min)", format="%d min")
    if "Schools" in df_display.columns:
        col_config["Schools"] = st.column_config.NumberColumn("Schools", format="%.1f/10")

    _table_event = st.dataframe(
        df_display,
        use_container_width=True,
        height=460,
        column_config=col_config,
        on_select="rerun",
        selection_mode="single-row",
    )

    # Read selected row -> get ZIP -> zoom map
    _selected_rows = (
        _table_event.selection.get("rows", [])
        if _table_event and hasattr(_table_event, "selection")
        else []
    )
    if _selected_rows:
        _sel_zip = str(df_display.iloc[_selected_rows[0]]["ZIP"]).zfill(5)
        if _sel_zip != st.session_state.get("table_selected_zip"):
            st.session_state["table_selected_zip"] = _sel_zip
            st.session_state["chart_selected_zip"] = None  # clear bar chart selection
            st.rerun()
    else:
        # Row deselected — clear table selection but leave chart selection alone
        if st.session_state.get("table_selected_zip"):
            st.session_state["table_selected_zip"] = None
            st.rerun()

    st.divider()

    # Export button — bakes current rank/dep/bedroom context into filename
    export_df = geo[[
        "zip_code","effective_rent","bah_surplus","commute_monthly",
        "true_cost","crime_rate","distance_mi","score",
        "listings_total","affordable"
    ]].copy()

    if drive_times_available:
        export_df["drive_minutes"] = geo["drive_minutes"]
    if schools_available:
        export_df["school_rating"] = geo["school_rating"]

    export_df = export_df.rename(columns={
        "zip_code":       "ZIP",
        "effective_rent": "Rent",
        "bah_surplus":    "BAH Surplus",
        "commute_monthly":"Commute/Mo",
        "true_cost":      "True Cost",
        "crime_rate":     "Crime Rate",
        "distance_mi":    "Miles",
        "score":          "Score",
        "listings_total": "Listings",
        "affordable":     "Affordable",
        "drive_minutes":  "Drive (min)",
        "school_rating":  "School Rating",
    })
    export_df.insert(0, "Rank",       rank)
    export_df.insert(1, "Dependents", "Yes" if dependents else "No")
    export_df.insert(2, "Bedrooms",   bedrooms)
    export_df.insert(3, "BAH",        BAH)

    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    fname     = f"housing_{rank}_{'dep' if dependents else 'nodep'}_{bedrooms}br.csv"

    st.download_button(
        label="📥 Export Full Analysis (CSV)",
        data=csv_bytes,
        file_name=fname,
        mime="text/csv",
        use_container_width=True,
        help="Downloads all ZIPs with your current rank, dependent status, and bedroom settings baked in"
    )

    # ── PDF Brief ────────────────────────────────────────────────
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        import io as _io
        import datetime

        def build_pdf():
            buf = _io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=letter,
                                    leftMargin=0.75*inch, rightMargin=0.75*inch,
                                    topMargin=0.75*inch, bottomMargin=0.75*inch)
            styles = getSampleStyleSheet()
            story  = []

            # Title block
            title_style = ParagraphStyle("title", parent=styles["Title"],
                                         fontSize=16, spaceAfter=4)
            sub_style   = ParagraphStyle("sub",   parent=styles["Normal"],
                                         fontSize=10, textColor=colors.gray, spaceAfter=12)
            story.append(Paragraph("Military Housing Decision Brief", title_style))
            story.append(Paragraph(
                f"Naval Base San Diego  ·  FY2026  ·  Generated {datetime.date.today().strftime('%d %b %Y')}",
                sub_style
            ))

            # Profile summary
            body = styles["Normal"]
            story.append(Paragraph(
                f"<b>Profile:</b>  Rank: {rank}  |  "
                f"Dependents: {'Yes' if dependents else 'No'}  |  "
                f"Bedrooms: {bedrooms}BR  |  BAH: ${BAH:,}/mo",
                body
            ))
            story.append(Spacer(1, 0.15*inch))

            # Top picks
            if not affordable_geo.empty:
                story.append(Paragraph("<b>Top Picks</b>", styles["Heading2"]))
                picks_data = [["Category", "ZIP", "Rent", "BAH Surplus", "Drive", "Score"]]
                for label, row in [
                    ("Best Overall", best_overall),
                    ("Safest",       safest),
                    ("Best Value",   best_value),
                ]:
                    drive_str = f"{int(row['drive_minutes'])} min" if pd.notna(row.get('drive_minutes')) else "—"
                    picks_data.append([
                        label,
                        row["zip_code"],
                        f"${row['effective_rent']:,.0f}",
                        f"${row['bah_surplus']:,.0f}",
                        drive_str,
                        f"{row['score']:.2f}",
                    ])
                t = Table(picks_data, colWidths=[1.2*inch,0.8*inch,0.9*inch,1.0*inch,0.8*inch,0.7*inch])
                t.setStyle(TableStyle([
                    ("BACKGROUND",  (0,0), (-1,0),  colors.HexColor("#1a3c5e")),
                    ("TEXTCOLOR",   (0,0), (-1,0),  colors.white),
                    ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
                    ("FONTSIZE",    (0,0), (-1,-1), 9),
                    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#f0f4f8"), colors.white]),
                    ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
                    ("ALIGN",       (2,0), (-1,-1), "RIGHT"),
                    ("LEFTPADDING", (0,0), (-1,-1), 6),
                    ("RIGHTPADDING",(0,0), (-1,-1), 6),
                ]))
                story.append(t)
                story.append(Spacer(1, 0.2*inch))

            # Full ZIP table — top 20 affordable by score
            story.append(Paragraph("<b>Full Analysis — Top 20 Affordable ZIPs by Score</b>", styles["Heading2"]))

            top20 = export_df[export_df["Affordable"] == True].head(20)

            _cols = ["ZIP", "Rent", "BAH Surplus", "Commute/Mo", "True Cost", "Crime Rate", "Miles", "Score"]
            if "Drive (min)" in top20.columns:
                _cols.insert(_cols.index("Miles"), "Drive (min)")
            if "School Rating" in top20.columns:
                _cols.append("School Rating")

            tbl_data = [_cols]
            for _, r in top20.iterrows():
                row_vals = []
                for c in _cols:
                    v = r.get(c, "—")
                    if c in ("Rent","BAH Surplus","Commute/Mo","True Cost") and pd.notna(v):
                        row_vals.append(f"${int(v):,}")
                    elif pd.isna(v):
                        row_vals.append("—")
                    elif isinstance(v, float):
                        row_vals.append(f"{v:.1f}")
                    else:
                        row_vals.append(str(v))
                tbl_data.append(row_vals)

            col_w = (7.0 / len(_cols)) * inch
            t2 = Table(tbl_data, colWidths=[col_w]*len(_cols))
            t2.setStyle(TableStyle([
                ("BACKGROUND",  (0,0), (-1,0),  colors.HexColor("#1a3c5e")),
                ("TEXTCOLOR",   (0,0), (-1,0),  colors.white),
                ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
                ("FONTSIZE",    (0,0), (-1,-1), 7.5),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#f0f4f8"), colors.white]),
                ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
                ("ALIGN",       (1,0), (-1,-1), "RIGHT"),
                ("LEFTPADDING", (0,0), (-1,-1), 4),
                ("RIGHTPADDING",(0,0), (-1,-1), 4),
            ]))
            story.append(t2)
            story.append(Spacer(1, 0.15*inch))

            # Footer note
            story.append(Paragraph(
                f"<i>Scoring weights: Rent×{rent_weight}, Safety×{safety_weight}, Distance×{distance_weight}"
                + (f", Schools×{school_weight}" if schools_available else "") +
                f"  |  Commute: {commute_label}  |  Rent source: Rentcast ({bedrooms}BR) with SAFMR fallback</i>",
                ParagraphStyle("footer", parent=styles["Normal"], fontSize=7, textColor=colors.gray)
            ))

            doc.build(story)
            buf.seek(0)
            return buf.read()

        pdf_bytes = build_pdf()
        pdf_fname = f"housing_brief_{rank}_{'dep' if dependents else 'nodep'}_{bedrooms}br.pdf"

        st.download_button(
            label="📄 Export Housing Brief (PDF)",
            data=pdf_bytes,
            file_name=pdf_fname,
            mime="application/pdf",
            use_container_width=True,
            help="Formatted brief with Top Picks + full ZIP analysis table"
        )

    except ImportError:
        st.caption("💡 Install reportlab for PDF export: `pip install reportlab`")

st.divider()

# ==============================================================
# ZIP DRILL-DOWN PANEL
# ==============================================================

st.subheader("📍 ZIP Code Detail")

clicked_zip = None

if map_data and map_data.get("last_clicked"):
    click_lat = map_data["last_clicked"]["lat"]
    click_lng = map_data["last_clicked"]["lng"]
    click_pt  = Point(click_lng, click_lat)

    hit = geo[geo.geometry.contains(click_pt)]
    if not hit.empty:
        clicked_zip = hit.iloc[0]

if clicked_zip is not None:
    z = clicked_zip
    zc = z["zip_code"]

    # Color header based on affordability
    header_color = "#21c354" if z["affordable"] else "#ff4b4b"
    aff_label    = "✅ Affordable" if z["affordable"] else "⛔ Over BAH"
    st.markdown(
        f'<div style="font-size:1.3rem;font-weight:700;color:{header_color};'
        f'margin-bottom:0.5rem;">{aff_label} · ZIP {zc}</div>',
        unsafe_allow_html=True
    )

    # Row 1 — financials
    d1, d2, d3, d4, d5 = st.columns(5)
    with d1:
        st.metric(f"{bedrooms}BR Rent", f"${z['effective_rent']:,.0f}")
    with d2:
        sv = z["bah_surplus"]
        st.metric("BAH Surplus",
                  f"${sv:,.0f}",
                  delta="Above BAH" if sv >= 0 else "Below BAH",
                  delta_color="normal" if sv >= 0 else "inverse")
    with d3:
        st.metric("Commute/Mo", f"${z['commute_monthly']:,.0f}")
    with d4:
        st.metric("True Cost", f"${z['true_cost']:,.0f}")
    with d5:
        surplus_vs_true = z["bah_surplus_vs_true"]
        st.metric("BAH vs True Cost",
                  f"${surplus_vs_true:,.0f}",
                  delta="Covered" if surplus_vs_true >= 0 else "Gap",
                  delta_color="normal" if surplus_vs_true >= 0 else "inverse")

    # Row 2 — location / safety / market
    d6, d7, d8, d9, d10 = st.columns(5)
    with d6:
        st.metric("Composite Score", f"{z['score']:.2f}")
    with d7:
        st.metric("Crime Rate", f"{z['crime_rate']:.2f}",
                  help="Weighted incidents per 1,000 residents")
    with d8:
        pop_val = z.get("population")
        st.metric("Population",
                  f"{int(pop_val):,}" if pd.notna(pop_val) and pop_val > 0 else "—")
    with d9:
        listings = z.get("listings_total")
        st.metric("Active Listings",
                  f"{int(listings)}" if pd.notna(listings) else "—")
    with d10:
        dom = z.get("days_on_market")
        st.metric("Days on Market",
                  f"{dom:.0f}" if pd.notna(dom) else "—")

    # Row 3 — enrichment data (only shown if data exists)
    enrichment_cols = []
    if drive_times_available:
        enrichment_cols.append(("Drive Time", f"{int(z['drive_minutes'])} min" if pd.notna(z.get('drive_minutes')) else "—"))
    if schools_available:
        sr = z.get("school_rating")
        enrichment_cols.append(("School Rating", f"{sr:.1f} / 10" if pd.notna(sr) else "—"))
        sc_count = z.get("school_count", 0)
        enrichment_cols.append(("Schools Nearby", f"{int(sc_count)}" if pd.notna(sc_count) else "—"))

    enrichment_cols.append(("Distance", f"{z['distance_mi']:.1f} mi"))

    if enrichment_cols:
        ecols = st.columns(len(enrichment_cols))
        for col, (label, val) in zip(ecols, enrichment_cols):
            with col:
                st.metric(label, val)

    # Narrative affordability summary
    st.markdown("")
    if z["affordable"]:
        monthly_savings = sv
        annual_savings  = monthly_savings * 12
        st.success(
            f"**ZIP {zc}** is affordable for {rank} {dep_label}. "
            f"At ${z['effective_rent']:,.0f}/mo rent your BAH surplus is **${monthly_savings:,.0f}/mo "
            f"(${annual_savings:,.0f}/yr)**. True cost with commute is **${z['true_cost']:,.0f}/mo**."
        )
    else:
        gap = abs(sv)
        st.error(
            f"**ZIP {zc}** exceeds your BAH (${BAH:,}/mo). "
            f"Rent of ${z['effective_rent']:,.0f}/mo is **${gap:,.0f}/mo over** your allowance. "
            f"You would need to cover the gap out of pocket."
        )

else:
    st.info("💡 Click any ZIP code on the map above to see a full financial and neighborhood breakdown here.")

st.divider()

# ==============================================================
# BAH SURPLUS BAR CHART
# ==============================================================

_chart_header_col, _reset_col = st.columns([5, 1])
with _chart_header_col:
    st.subheader("BAH Surplus / Gap by ZIP  (sorted by surplus)")
with _reset_col:
    if st.session_state.get("chart_selected_zip"):
        if st.button("🔍 Reset Map", use_container_width=True):
            st.session_state["chart_selected_zip"] = None
            st.rerun()

import altair as alt

chart_data = (
    geo[["zip_code","bah_surplus","affordable"]]
    .dropna(subset=["bah_surplus"])
    .sort_values("bah_surplus", ascending=False)
    .head(40)
    .copy()
)

zip_selection = alt.selection_point(fields=["zip_code"], on="click", empty=False)

chart = (
    alt.Chart(chart_data)
    .mark_bar()
    .encode(
        x=alt.X("zip_code:N", sort="-y", title="ZIP Code  (click to zoom map)",
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("bah_surplus:Q", title="BAH Surplus / Gap ($)"),
        color=alt.condition(
            alt.datum.bah_surplus >= 0,
            alt.value("#2ecc71"),
            alt.value("#e74c3c")
        ),
        opacity=alt.condition(zip_selection, alt.value(1.0), alt.value(0.45)),
        tooltip=[
            alt.Tooltip("zip_code:N",    title="ZIP"),
            alt.Tooltip("bah_surplus:Q", title="Surplus ($)", format=",.0f")
        ]
    )
    .add_params(zip_selection)
    .properties(height=300)
)

chart_event = st.altair_chart(chart, use_container_width=True, on_select="rerun")

# Read selected ZIP from chart event
# selection is a dict keyed by param name — iterate to find zip_code regardless of param name
_new_zip = None
if chart_event and hasattr(chart_event, "selection"):
    for _param_val in chart_event.selection.values():
        if isinstance(_param_val, dict) and "zip_code" in _param_val:
            _zips = _param_val["zip_code"]
            if _zips:
                _new_zip = str(_zips[0]).zfill(5)
            break

# If selection changed, update session state and rerun so the map picks it up
# (chart is below the map in page order so we need one extra rerun)
if _new_zip and _new_zip != st.session_state.get("chart_selected_zip"):
    st.session_state["chart_selected_zip"] = _new_zip
    st.rerun()

st.caption(
    f"Green bars = BAH surplus (rent below BAH).  "
    f"Red bars = BAH gap (rent exceeds BAH).  "
    f"Rent source: Rentcast {bedrooms}BR actuals where available, {hud_rent_source} fallback.  "
    f"Commute method: {commute_label}."
)