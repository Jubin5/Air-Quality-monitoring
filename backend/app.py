import streamlit as st
import pandas as pd
import numpy as np
import requests
import pydeck as pdk
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from krigging import perform_kriging_correct
from krigging import get_aqi_at_location
import geopandas as gpd
from shapely.geometry import Point
import pyproj
from shapely.ops import transform
import smtplib
from email.message import EmailMessage
import json
from pathlib import Path

# ==========================
# PAGE CONFIGURATION
# ==========================
st.set_page_config(
    layout="wide",
    page_title="Delhi Air Quality Dashboard",
    page_icon="💨"
)

# ==========================
# STATIC CONFIG
# ==========================
API_TOKEN = "97a0e712f47007556b57ab4b14843e72b416c0f9"
DELHI_BOUNDS = "28.404,76.840,28.883,77.349"
DELHI_LAT = 28.6139
DELHI_LON = 77.2090

DELHI_GEOJSON_URL = "https://raw.githubusercontent.com/shuklaneerajdev/IndiaStateTopojsonFiles/master/Delhi.geojson"

# Historical data storage path
HISTORICAL_DATA_DIR = Path("aqi_historical_data")
HISTORICAL_DATA_DIR.mkdir(exist_ok=True)
HISTORICAL_DATA_FILE = HISTORICAL_DATA_DIR / "aqi_history.json"

# ==========================
# EMAIL TO SMS CONFIGURATION
# ==========================
SENDER_EMAIL = "anumaliknr@gmail.com"
GMAIL_APP_PASSWORD = "xczo lasg vcek olqp"

SMS_GATEWAYS = {
    "Airtel": "@airtelmail.com",
    "Jio": "@jionet.com", 
    "Vi (Vodafone Idea)": "@myvi.in",
    "BSNL": "@bsnlmail.com",
    "AT&T (USA)": "@txt.att.net",
    "T-Mobile (USA)": "@tmomail.net",
    "Verizon (USA)": "@vtext.com",
    "Sprint (USA)": "@messaging.sprintpcs.com"
}

def send_sms_via_email(phone_number, carrier_gateway, message, subject="AQI Alert"):
    """Send SMS using Email-to-SMS gateway via Gmail SMTP"""
    try:
        phone_clean = ''.join(filter(str.isdigit, phone_number))
        gateway_address = f"{phone_clean}{carrier_gateway}"
        
        msg = EmailMessage()
        msg.set_content(message)
        msg["From"] = SENDER_EMAIL
        msg["To"] = gateway_address
        msg["Subject"] = subject
        
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        
        return True, f"SMS sent successfully to {phone_clean} via {carrier_gateway}"
        
    except smtplib.SMTPAuthenticationError:
        return False, "Email authentication failed. Please check your Gmail credentials and ensure 'App Password' is enabled."
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {str(e)}"
    except Exception as e:
        return False, f"SMS sending failed: {str(e)}"

# ==========================
# HISTORICAL DATA FUNCTIONS
# ==========================

def save_historical_snapshot(df):
    """Save current AQI snapshot to historical database"""
    try:
        if HISTORICAL_DATA_FILE.exists():
            with open(HISTORICAL_DATA_FILE, 'r') as f:
                historical_data = json.load(f)
        else:
            historical_data = []
        
        timestamp = datetime.now().isoformat()
        snapshot = {
            "timestamp": timestamp,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "hour": datetime.now().hour,
            "stations": []
        }
        
        for _, row in df.iterrows():
            snapshot["stations"].append({
                "station_name": row["station_name"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "aqi": float(row["aqi"]),
                "category": row["category"]
            })
        
        existing_dates = [s["timestamp"][:13] for s in historical_data]
        current_hour_key = timestamp[:13]
        
        if current_hour_key not in existing_dates:
            historical_data.append(snapshot)
            
            cutoff_date = (datetime.now() - timedelta(days=90)).isoformat()
            historical_data = [s for s in historical_data if s["timestamp"] >= cutoff_date]
            
            with open(HISTORICAL_DATA_FILE, 'w') as f:
                json.dump(historical_data, f)
            
            return True, len(historical_data)
        return False, len(historical_data)
        
    except Exception as e:
        return False, 0

def load_historical_data():
    """Load historical AQI data"""
    try:
        if HISTORICAL_DATA_FILE.exists():
            with open(HISTORICAL_DATA_FILE, 'r') as f:
                return json.load(f)
        return []
    except Exception as e:
        return []

def get_historical_stats():
    """Calculate statistics from historical data"""
    historical_data = load_historical_data()
    
    if not historical_data:
        return None
    
    records = []
    for snapshot in historical_data:
        for station in snapshot["stations"]:
            records.append({
                "timestamp": snapshot["timestamp"],
                "date": snapshot["date"],
                "hour": snapshot["hour"],
                "station_name": station["station_name"],
                "aqi": station["aqi"],
                "category": station["category"]
            })
    
    if not records:
        return None
        
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    return df

# ==========================
# AI AGENT HELPER FUNCTIONS
# ==========================

def get_aqi_category_for_agent(aqi_value):
    """Categorize AQI for AI agent"""
    if aqi_value <= 50:
        return "Good"
    elif aqi_value <= 100:
        return "Moderate"
    elif aqi_value <= 150:
        return "Unhealthy for Sensitive Groups"
    elif aqi_value <= 200:
        return "Unhealthy"
    elif aqi_value <= 300:
        return "Very Unhealthy"
    elif aqi_value <= 400:
        return "Hazardous"
    elif aqi_value <= 450:
        return "Severe"
    else:
        return "Severe+"

def determine_risk_profile(health_conditions):
    """Determine user's risk profile"""
    if not health_conditions:
        return "general"
    
    conditions_lower = [c.lower().strip() for c in health_conditions]
    
    critical = {'transplant', 'immunocompromised', 'chemotherapy', 'cancer treatment'}
    high_risk = {'copd', 'emphysema', 'severe asthma', 'heart disease', 'cardiovascular'}
    children_markers = {'child', 'children', 'kid', 'infant', 'toddler', 'baby'}
    pregnant_markers = {'pregnant', 'pregnancy', 'expecting'}
    elderly_markers = {'elderly', 'senior', 'old age', 'aged', '60+'}
    sensitive = {'asthma', 'allergies', 'bronchitis'}
    
    for c in conditions_lower:
        for crit in critical:
            if crit in c:
                return "critical"
    
    for c in conditions_lower:
        for preg in pregnant_markers:
            if preg in c:
                return "pregnant"
    
    for c in conditions_lower:
        for child in children_markers:
            if child in c:
                return "children"
    
    for c in conditions_lower:
        for old in elderly_markers:
            if old in c:
                return "elderly"
    
    for c in conditions_lower:
        for hr in high_risk:
            if hr in c:
                return "high_risk"
    
    for c in conditions_lower:
        for sens in sensitive:
            if sens in c:
                return "sensitive"
    
    return "sensitive" if health_conditions else "general"

def get_rule_based_recommendation(aqi_value, risk_profile, aqi_category):
    """Rule-based health recommendations"""
    recommendations = {
        "Good": {
            "summary": "Air quality is excellent! Perfect day to enjoy outdoor activities.",
            "precautions": ["Stay hydrated", "Keep a basic mask handy as conditions can change"],
            "recommended_activities": ["Outdoor exercise", "Park visits", "Sports activities", "Morning walks"],
            "health_implications": "Air quality poses minimal risk. Feel free to enjoy outdoor activities."
        },
        "Moderate": {
            "summary": "Air quality is acceptable. Most people can go about normal activities.",
            "precautions": ["Watch for any symptoms", "Consider masks in heavy traffic", "Take normal breaks during extended outdoor work"],
            "recommended_activities": ["Normal outdoor activities", "Moderate exercise", "Regular commuting"],
            "health_implications": "Air quality is acceptable for most people."
        },
        "Unhealthy for Sensitive Groups": {
            "summary": "Sensitive groups should take precautions. Others can continue with awareness.",
            "precautions": ["Wear mask when outdoors", "Use air purifiers indoors", "Limit prolonged outdoor time", "Watch for throat irritation"],
            "recommended_activities": ["Prefer indoor activities", "Short outdoor errands with mask", "Reduce strenuous activities"],
            "health_implications": "Sensitive groups will notice effects. General public may experience minor symptoms."
        },
        "Unhealthy": {
            "summary": "Everyone affected. Stay indoors and use protection when going out.",
            "precautions": ["Wear N95 mask outdoors", "Keep windows closed", "Use air purifiers", "Avoid outdoor exercise"],
            "recommended_activities": ["Stay indoors when possible", "Work from home if available", "Indoor activities only"],
            "health_implications": "Everyone may experience health effects. Sensitive groups at higher risk."
        },
        "Very Unhealthy": {
            "summary": "Health alert for everyone. Stay indoors with air filtration.",
            "precautions": ["Complete indoor isolation", "Multiple air purifiers essential", "N95 masks mandatory for any outdoor time", "Monitor health closely"],
            "recommended_activities": ["Indoor activities only", "Work from home mandatory", "Essential travel only with maximum protection"],
            "health_implications": "Everyone at increased risk. Serious effects for sensitive groups."
        },
        "Hazardous": {
            "summary": "Health emergency. Everyone must stay indoors with air filtration.",
            "precautions": ["Seal windows and doors", "Multiple air purifiers essential", "Complete indoor isolation", "Medical consultation if symptoms worsen"],
            "recommended_activities": ["Stay indoors in protected environment", "No outdoor activities", "Follow all emergency guidelines"],
            "health_implications": "Serious health effects for everyone. Follow all emergency protocols."
        },
        "Severe": {
            "summary": "Severe health emergency. Maximum protection required for everyone.",
            "precautions": ["Maximum air filtration essential", "Complete indoor isolation", "Consider evacuation if possible", "Medical supervision recommended"],
            "recommended_activities": ["Protected indoor environment only", "No outdoor exposure", "Follow emergency directives"],
            "health_implications": "Severe emergency affecting everyone. Maximum protection essential."
        },
        "Severe+": {
            "summary": "Extreme emergency. Evacuation advisable if possible.",
            "precautions": ["Consider evacuation to cleaner area", "Maximum protection if staying", "Hospital care may be necessary for vulnerable groups"],
            "recommended_activities": ["Evacuation advisable", "Maximum protected environment if staying", "Follow all emergency protocols"],
            "health_implications": "Extreme emergency. Everyone at serious risk. Maximum protection or evacuation essential."
        }
    }
    
    rec = recommendations.get(aqi_category, recommendations["Moderate"]).copy()
    
    if risk_profile in ["children", "pregnant", "elderly", "high_risk", "critical"]:
        rec["precautions"].insert(0, f"As a {risk_profile} individual, extra caution is essential")
    
    return rec

def get_personalized_recommendation(aqi_value, health_conditions):
    """Generate personalized recommendations"""
    aqi_category = get_aqi_category_for_agent(aqi_value)
    risk_profile = determine_risk_profile(health_conditions)
    
    rec = get_rule_based_recommendation(aqi_value, risk_profile, aqi_category)
    
    return {
        "aqi_value": aqi_value,
        "aqi_category": aqi_category,
        "risk_profile": risk_profile,
        "health_conditions": health_conditions,
        "summary": rec["summary"],
        "precautions": rec["precautions"],
        "recommended_activities": rec["recommended_activities"],
        "health_implications": rec["health_implications"],
        "ai_powered": False
    }

# ==========================
# STATISTICAL VISUALIZATION FUNCTIONS
# ==========================

def render_hourly_boxplot(historical_df):
    """Render hourly box plot showing daily variability patterns"""
    st.markdown("### 📦 Best Time to Go Out: Hourly AQI Patterns")
    
    if historical_df is None or len(historical_df) < 24:
        data_count = len(historical_df) if historical_df is not None else 0
        st.info(f"""
        📊 **Historical Data Collection in Progress**
        
        Currently collecting: **{data_count}** hourly snapshots
        
        **What you'll see here after 7+ days:**
        - Box plots showing AQI patterns for each hour (0-23)
        - Best time windows for outdoor activities (lowest median AQI)
        - Risk assessment for each hour (worst-case scenarios)
        - Variability indicators (how consistent each hour is)
        
        **Interpretation Guide:**
        - **Box center line** = Typical AQI for that hour
        - **Box height** = How much variation to expect
        - **Whiskers** = Range of historical values
        - **Dots** = Unusual outlier days
        
        Keep the dashboard running to collect more data!
        """)
        return
    
    hourly_stats = historical_df.groupby('hour')['aqi'].apply(list).to_dict()
    
    fig = go.Figure()
    
    hours = sorted(hourly_stats.keys())
    for hour in hours:
        values = hourly_stats[hour]
        fig.add_trace(go.Box(
            y=values,
            name=f"{hour:02d}:00",
            boxmean='sd',
            marker_color='lightblue',
            line=dict(color='rgb(8,81,156)')
        ))
    
    fig.add_hrect(y0=0, y1=50, fillcolor="green", opacity=0.1, line_width=0)
    fig.add_hrect(y0=50, y1=100, fillcolor="yellow", opacity=0.1, line_width=0)
    fig.add_hrect(y0=100, y1=150, fillcolor="orange", opacity=0.1, line_width=0)
    fig.add_hrect(y0=150, y1=200, fillcolor="red", opacity=0.1, line_width=0)
    fig.add_hrect(y0=200, y1=300, fillcolor="purple", opacity=0.1, line_width=0)
    fig.add_hrect(y0=300, y1=500, fillcolor="maroon", opacity=0.1, line_width=0)
    
    fig.update_layout(
        title="AQI Distribution by Hour of Day",
        xaxis_title="Hour of Day",
        yaxis_title="AQI Value",
        showlegend=False,
        height=500,
        yaxis=dict(range=[0, min(400, historical_df['aqi'].max() * 1.1)])
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    median_by_hour = {h: np.median(vals) for h, vals in hourly_stats.items()}
    best_hour = min(median_by_hour, key=median_by_hour.get)
    worst_hour = max(median_by_hour, key=median_by_hour.get)
    
    col1, col2 = st.columns(2)
    with col1:
        st.success(f"""
        **✅ Best Time for Outdoor Activities**
        
        **{best_hour:02d}:00 - {(best_hour+1)%24:02d}:00**
        
        Median AQI: {median_by_hour[best_hour]:.1f}
        """)
    
    with col2:
        st.error(f"""
        **⚠️ Most Polluted Hour**
        
        **{worst_hour:02d}:00 - {(worst_hour+1)%24:02d}:00**
        
        Median AQI: {median_by_hour[worst_hour]:.1f}
        """)


def render_risk_frequency_histogram(historical_df):
    """Render histogram showing frequency of AQI categories over time"""
    st.markdown("### 📊 Air Quality Risk Distribution (Last 30-90 Days)")
    
    if historical_df is None or len(historical_df) < 7:
        data_count = len(historical_df) if historical_df is not None else 0
        st.info(f"""
        📈 **Long-term Risk Assessment (Building Dataset)**
        
        Currently collected: **{data_count}** data points
        
        **What you'll see here after 30+ days:**
        - Frequency of each AQI category (Good, Moderate, Unhealthy, etc.)
        - Percentage of days meeting health standards
        - Trend analysis showing if air quality is improving or worsening
        - Compliance metrics for environmental goals
        
        **Use Cases:**
        - Assess long-term health risk exposure
        - Identify seasonal patterns
        - Track effectiveness of pollution control measures
        - Set realistic expectations for air quality
        
        Continue collecting data for comprehensive analysis!
        """)
        return
    
    daily_stats = historical_df.groupby('date').agg({
        'aqi': ['mean', 'max']
    }).reset_index()
    daily_stats.columns = ['date', 'avg_aqi', 'max_aqi']
    
    daily_stats['category'] = daily_stats['max_aqi'].apply(lambda x: get_aqi_category(x)[0])
    
    category_counts = daily_stats['category'].value_counts()
    
    category_order = ["Good", "Moderate", "Unhealthy for Sensitive Groups", 
                     "Unhealthy", "Very Unhealthy", "Hazardous"]
    category_colors = {
        "Good": "#009E60",
        "Moderate": "#FFD600",
        "Unhealthy for Sensitive Groups": "#F97316",
        "Unhealthy": "#DC2626",
        "Very Unhealthy": "#9333EA",
        "Hazardous": "#7E22CE"
    }
    
    plot_data = []
    for cat in category_order:
        count = category_counts.get(cat, 0)
        plot_data.append({
            'Category': cat,
            'Days': count,
            'Percentage': (count / len(daily_stats) * 100) if len(daily_stats) > 0 else 0
        })
    
    plot_df = pd.DataFrame(plot_data)
    
    fig = go.Figure(data=[
        go.Bar(
            x=plot_df['Category'],
            y=plot_df['Days'],
            text=plot_df['Percentage'].apply(lambda x: f'{x:.1f}%'),
            textposition='auto',
            marker_color=[category_colors.get(cat, '#999999') for cat in plot_df['Category']]
        )
    ])
    
    fig.update_layout(
        title=f"Air Quality Distribution Over Last {len(daily_stats)} Days",
        xaxis_title="AQI Category",
        yaxis_title="Number of Days",
        height=400,
        showlegend=False
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    total_days = len(daily_stats)
    good_moderate_days = category_counts.get("Good", 0) + category_counts.get("Moderate", 0)
    unhealthy_days = total_days - good_moderate_days
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Healthy Days",
            f"{good_moderate_days} days",
            f"{good_moderate_days/total_days*100:.1f}%",
            delta_color="normal"
        )
    
    with col2:
        st.metric(
            "Unhealthy Days",
            f"{unhealthy_days} days",
            f"{unhealthy_days/total_days*100:.1f}%",
            delta_color="inverse"
        )
    
    with col3:
        goal_pct = 80
        current_pct = good_moderate_days/total_days*100
        delta = current_pct - goal_pct
        st.metric(
            "Health Goal (80%)",
            f"{current_pct:.1f}%",
            f"{delta:+.1f}%",
            delta_color="normal" if delta >= 0 else "inverse"
        )

# ==========================
# CUSTOM CSS FOR STYLING
# ==========================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    
    html, body, [class*="st-"] {
        font-family: 'Inter', sans-serif;
    }

    .stApp {
        background: linear-gradient(to right, #acffff, #00cec8);
    }

    header, footer, #MainMenu {
        visibility: hidden;
    }
    
    .main-title {
        font-size: 3.5rem;
        font-weight: 900;
        color: #0D47A1;
        padding: 1.5rem 0 0.5rem 0;
        text-align: center;
        text-shadow: 2px 2px 4px rgba(13, 71, 161, 0.2);
        letter-spacing: -1px;
    }

    .subtitle {
        font-size: 1.2rem;
        color: #1565C0;
        text-align: center;
        padding-bottom: 1.5rem;
        font-weight: 500;
    }

    .metric-card {
        background-color: #FFFFFF;
        border-radius: 15px;
        padding: 1.5rem;
        border: 2px solid #BBDEFB;
        box-shadow: 0 4px 20px rgba(33, 150, 243, 0.15);
        text-align: center;
        height: 100%;
    }
    .metric-card-label {
        font-size: 1rem;
        font-weight: 600;
        color: #1565C0;
        margin-bottom: 0.5rem;
    }
    .metric-card-value {
        font-size: 2.5rem;
        font-weight: 800;
        color: #0D47A1;
        margin: 0.5rem 0;
    }
    .metric-card-delta {
        font-size: 0.9rem;
        color: #1976D2;
        font-weight: 500;
    }

    .weather-widget {
        background-color: #FFFFFF;
        border-radius: 15px;
        padding: 1.5rem;
        border: 2px solid #BBDEFB;
        box-shadow: 0 4px 20px rgba(33, 150, 243, 0.15);
        height: 100%;
    }
    .weather-temp {
        font-size: 2.5rem;
        font-weight: 800;
        color: #0D47A1;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
        background-color: transparent;
        padding: 1rem 0;
    }
    
    .stTabs [data-baseweb="tab"] {
        font-size: 1.1rem;
        font-weight: 600;
        background-color: white;
        border-radius: 15px;
        padding: 1rem 2rem;
        border: 2px solid #BBDEFB;
        color: #1565C0;
        box-shadow: 0 2px 10px rgba(33, 150, 243, 0.1);
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        background-color: #E3F2FD;
        border-color: #2196F3;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #2196F3 0%, #1976D2 100%);
        color: white !important;
        border-color: #1976D2;
    }

    .content-card {
        background-color: #FFFFFF;
        padding: 2rem;
        border-radius: 20px;
        border: 2px solid #BBDEFB;
        box-shadow: 0 10px 40px rgba(33, 150, 243, 0.2);
        margin-top: 1.5rem;
    }

    .alert-card {
        padding: 1rem 1.5rem;
        border-radius: 12px;
        margin-bottom: 1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        color: white;
        font-weight: 600;
    }
    .alert-hazardous { 
        background: linear-gradient(135deg, #EF5350 0%, #E53935 100%);
        box-shadow: 0 4px 15px rgba(239, 83, 80, 0.3);
    }
    .alert-very-unhealthy { 
        background: linear-gradient(135deg, #FF9800 0%, #F57C00 100%);
        box-shadow: 0 4px 15px rgba(255, 152, 0, 0.3);
    }
    .alert-unhealthy { 
        background: linear-gradient(135deg, #FFA726 0%, #FB8C00 100%);
        box-shadow: 0 4px 15px rgba(255, 167, 38, 0.3);
    }

    .section-header {
        font-size: 1.5rem;
        font-weight: 700;
        color: #0D47A1;
        margin-bottom: 1.5rem;
        padding-bottom: 0.5rem;
        border-bottom: 3px solid #BBDEFB;
    }

    div[data-testid="stAlert"] {
        background-color: white;
        border-left: 5px solid #2196F3;
        border-radius: 10px;
        color: #0D47A1;
    }

    div[data-testid="stSuccess"] {
        background-color: white;
        border-left: 5px solid #4CAF50;
        border-radius: 10px;
        color: #2E7D32;
    }

    div[data-testid="stError"] {
        background-color: white;
        border-left: 5px solid #EF5350;
        border-radius: 10px;
        color: #C62828;
    }

    div[data-testid="stDataFrame"] {
        border: 2px solid #BBDEFB;
        border-radius: 10px;
        background-color: white;
    }
    
    div[data-testid="stPlotlyChart"] {
        background-color: white;
        border-radius: 10px;
        padding: 0.5rem;
    }
    
    .element-container {
        background-color: transparent;
    }
    
    .block-container {
        background-color: transparent;
        padding-top: 2rem;
    }
    
    /* Health advisor styles */
    .health-card {
        background: linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%);
        border-radius: 15px;
        padding: 1.5rem;
        margin: 1rem 0;
        border-left: 5px solid #2196F3;
    }

</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner="Loading Delhi boundary...")
def load_delhi_boundary_from_url():
    try:
        gdf = gpd.read_file(DELHI_GEOJSON_URL)
        if gdf.crs is None or gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        polygon = gdf.unary_union
        return gdf, polygon
    except Exception as e:
        st.error(f"Failed to load Delhi polygon: {e}")
        return None, None

if "delhi_gdf" not in st.session_state or "delhi_polygon" not in st.session_state:
    gdf, polygon = load_delhi_boundary_from_url()
    st.session_state["delhi_gdf"] = gdf
    st.session_state["delhi_polygon"] = polygon


@st.cache_data(ttl=600, show_spinner="Fetching Air Quality Data...")
def fetch_live_data():
    """Fetches and processes live AQI data from the WAQI API."""
    url = f"https://api.waqi.info/map/bounds/?latlng={DELHI_BOUNDS}&token={API_TOKEN}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "ok":
            df = pd.DataFrame(data["data"])
            df = df[df['aqi'] != "-"]
            df['aqi'] = pd.to_numeric(df['aqi'], errors='coerce')
            df = df.dropna(subset=['aqi'])

            def safe_get_name(x):
                if isinstance(x, dict):
                    return x.get('name', 'N/A')
                elif isinstance(x, str):
                    return x
                else:
                    return 'N/A'

            def safe_get_time(x):
                if isinstance(x, dict):
                    time_data = x.get('time', {})
                    if isinstance(time_data, dict):
                        return time_data.get('s', 'N/A')
                    elif isinstance(time_data, str):
                        return time_data
                    else:
                        return 'N/A'
                else:
                    return 'N/A'

            df['station_name'] = df['station'].apply(safe_get_name)
            df['last_updated'] = df['station'].apply(safe_get_time)
            df[['category', 'color', 'emoji', 'advice']] = df['aqi'].apply(
                get_aqi_category).apply(pd.Series)
            df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
            df['lon'] = pd.to_numeric(df['lon'], errors='coerce')
            df = df.dropna(subset=['lat', 'lon'])
            return df
        return pd.DataFrame()
    except requests.RequestException:
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner="Fetching Weather Data...")
def fetch_weather_data():
    """Fetches current weather data from Open-Meteo API."""
    url = f"https://api.open-meteo.com/v1/forecast?latitude={DELHI_LAT}&longitude={DELHI_LON}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&timezone=Asia/Kolkata"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def get_aqi_category(aqi):
    """Categorizes AQI value and provides color, emoji, and health advice."""
    if aqi <= 50:
        return "Good", [0, 158, 96], "✅", "Enjoy outdoor activities."
    elif aqi <= 100:
        return "Moderate", [255, 214, 0], "🟡", "Unusually sensitive people should consider reducing prolonged or heavy exertion."
    elif aqi <= 150:
        return "Unhealthy for Sensitive Groups", [249, 115, 22], "🟠", "Sensitive groups should reduce prolonged or heavy exertion."
    elif aqi <= 200:
        return "Unhealthy", [220, 38, 38], "🔴", "Everyone may begin to experience health effects."
    elif aqi <= 300:
        return "Very Unhealthy", [147, 51, 234], "🟣", "Health alert: everyone may experience more serious health effects."
    else:
        return "Hazardous", [126, 34, 206], "☠️", "Health warnings of emergency conditions. The entire population is more likely to be affected."

def render_kriging_tab(df):
    st.subheader("Spatial Interpolation (Kriging)")
    delhi_bounds_tuple = (28.40, 28.88, 76.84, 77.35)
    delhi_polygon = st.session_state.get("delhi_polygon", None)

    if delhi_polygon is None:
        st.error("Delhi boundary could not be loaded.")
        return

    if len(df) < 3:
        st.error("Not enough AQI stations within Delhi boundary for kriging interpolation (minimum 3 required).")
        return

    with st.spinner("Performing spatial interpolation..."):
        try:
            lon_grid, lat_grid, z = perform_kriging_correct(
                df,
                delhi_bounds_tuple,
                polygon=delhi_polygon,
                resolution=250
            )

            st.session_state["kriging_output"] = (lon_grid, lat_grid, z)
            st.success("✅ Kriging interpolation completed successfully!")

            heatmap_df = pd.DataFrame({
                "lon": lon_grid.flatten(),
                "lat": lat_grid.flatten(),
                "aqi": z.flatten()
            })
            
            heatmap_df = heatmap_df.dropna(subset=['aqi'])

            fig = px.density_mapbox(
                heatmap_df,
                lat="lat",
                lon="lon",
                z="aqi",
                radius=15,
                center=dict(lat=28.6139, lon=77.2090),
                zoom=9.5,
                mapbox_style="carto-positron",
                color_continuous_scale=[
                    "#009E60", "#FFD600", "#F97316",
                    "#DC2626", "#9333EA", "#7E22CE"
                ],
                range_color=[0, 400],
                title="Interpolated AQI Heatmap across Delhi"
            )
            
            fig.update_layout(
                margin=dict(t=40, b=0, l=0, r=0),
                coloraxis_colorbar=dict(
                    title="AQI",
                    thicknessmode="pixels",
                    thickness=15,
                    lenmode="pixels",
                    len=300
                )
            )

            st.plotly_chart(fig, use_container_width=True)
        
        except Exception as e:
            st.error(f"Error performing kriging: {str(e)}")
            import traceback
            st.code(traceback.format_exc())



def get_weather_info(code):
    """Converts WMO weather code to a description and icon."""
    codes = {
        0: ("Clear sky", "☀️"), 1: ("Mainly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
        3: ("Overcast", "☁️"), 45: ("Fog", "🌫️"), 48: ("Depositing rime fog", "🌫️"),
        51: ("Light drizzle", "💧"), 53: ("Moderate drizzle", "💧"), 55: ("Dense drizzle", "💧"),
        61: ("Slight rain", "🌧️"), 63: ("Moderate rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
        80: ("Slight rain showers", "🌦️"), 81: ("Moderate rain showers", "🌦️"),
        82: ("Violent rain showers", "⛈️"), 95: ("Thunderstorm", "⚡"),
        96: ("Thunderstorm, slight hail", "⛈️"), 99: ("Thunderstorm, heavy hail", "⛈️")
    }
    return codes.get(code, ("Unknown", "❓"))


# ==========================
# UI RENDERING FUNCTIONS
# ==========================


def render_header(df):
    """Renders the main header with summary metrics and weather."""
    st.markdown('<div class="main-title">🌍 Delhi Air Quality Dashboard</div>',
                unsafe_allow_html=True)
    
    historical_data = load_historical_data()
    data_points = len(historical_data)
    
    if data_points > 0:
        first_snapshot = datetime.fromisoformat(historical_data[0]["timestamp"])
        days_collecting = (datetime.now() - first_snapshot).days
        ready_text = ' ✅ Ready for analysis!' if data_points >= 168 else ' 🔄 Keep collecting...'
        st.markdown(f"""
        <div style="background-color: #E8F5E9; padding: 0.75rem; border-radius: 8px; text-align: center; margin-bottom: 1rem; border: 2px solid #4CAF50;">
            <span style="color: #2E7D32; font-weight: 600;">
                📊 Historical Data: {data_points} snapshots collected over {days_collecting} days{ready_text}
            </span>
        </div>
        """, unsafe_allow_html=True)
    
    last_update_time = df['last_updated'].max() if not df.empty and 'last_updated' in df.columns else "N/A"
    st.markdown(f'<p class="subtitle">Real-time monitoring • Last updated: {last_update_time}</p>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    if not df.empty:
        with c1:
            avg_aqi = df["aqi"].mean()
            avg_cat = get_aqi_category(avg_aqi)[0]
            st.markdown(
                f'<div class="metric-card"><div class="metric-card-label">Average AQI</div><div class="metric-card-value">{avg_aqi:.1f}</div><div class="metric-card-delta">{avg_cat}</div></div>', unsafe_allow_html=True)
        with c2:
            min_station = df.loc[df["aqi"].idxmin()]["station_name"]
            min_aqi = df["aqi"].min()
            st.markdown(
                f'<div class="metric-card"><div class="metric-card-label">Minimum AQI</div><div class="metric-card-value">{min_aqi:.0f}</div><div class="metric-card-delta">{min_station}</div></div>', unsafe_allow_html=True)
        with c3:
            max_station = df.loc[df["aqi"].idxmax()]["station_name"]
            max_aqi = df["aqi"].max()
            st.markdown(
                f'<div class="metric-card"><div class="metric-card-label">Maximum AQI</div><div class="metric-card-value">{max_aqi:.0f}</div><div class="metric-card-delta">{max_station}</div></div>', unsafe_allow_html=True)

    with c4:
        weather_data = fetch_weather_data()
        if weather_data and 'current' in weather_data:
            current = weather_data['current']
            desc, icon = get_weather_info(current.get('weather_code', 0))
            temp = current['temperature_2m']
            humidity = current['relative_humidity_2m']
            wind = current['wind_speed_10m']
            st.markdown(f"""
            <div class="weather-widget">
                <div style="display: flex; justify-content: space-between; align-items: start;">
                    <div>
                        <div class="metric-card-label">Current Weather</div>
                        <div class="weather-temp">{temp:.1f}°C</div>
                    </div>
                    <div style="font-size: 3rem;">{icon}</div>
                </div>
                <div style="text-align: left; font-size: 0.9rem; color: #1976D2; margin-top: 1rem; font-weight: 500;">
                    {desc}<br/>Humidity: {humidity}%<br/>Wind: {wind} km/h
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="weather-widget">
                <div class="metric-card-label">Current Weather</div>
                <div style="color: #1976D2; margin-top: 1rem;">Weather data unavailable</div>
            </div>
            """, unsafe_allow_html=True)


def render_map_tab(df):
    """Renders the interactive map of AQI stations."""
    st.markdown('<div class="section-header">📍 Interactive Air Quality Map (Stations inside Delhi)</div>',
                unsafe_allow_html=True)

    if df.empty:
        st.warning("No monitoring stations found inside the Delhi boundary.")
        return

    st.markdown("""
    <div style="background-color: white; padding: 1rem; border-radius: 10px; border: 2px solid #BBDEFB; margin-bottom: 1rem;">
        <div style="font-weight: 700; color: #0D47A1; margin-bottom: 0.75rem; font-size: 1.1rem;">AQI Color Legend</div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem;">
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <div style="width: 20px; height: 20px; border-radius: 50%; background-color: rgb(0, 158, 96);"></div>
                <span style="color: #1E293B; font-weight: 500;">Good (0-50)</span>
            </div>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <div style="width: 20px; height: 20px; border-radius: 50%; background-color: rgb(255, 214, 0);"></div>
                <span style="color: #1E293B; font-weight: 500;">Moderate (51-100)</span>
            </div>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <div style="width: 20px; height: 20px; border-radius: 50%; background-color: rgb(249, 115, 22);"></div>
                <span style="color: #1E293B; font-weight: 500;">Unhealthy for Sensitive (101-150)</span>
            </div>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <div style="width: 20px; height: 20px; border-radius: 50%; background-color: rgb(220, 38, 38);"></div>
                <span style="color: #1E293B; font-weight: 500;">Unhealthy (151-200)</span>
            </div>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <div style="width: 20px; height: 20px; border-radius: 50%; background-color: rgb(147, 51, 234);"></div>
                <span style="color: #1E293B; font-weight: 500;">Very Unhealthy (201-300)</span>
            </div>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <div style="width: 20px; height: 20px; border-radius: 50%; background-color: rgb(126, 34, 206);"></div>
                <span style="color: #1E293B; font-weight: 500;">Hazardous (300+)</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.pydeck_chart(pdk.Deck(
        map_style="light",
        initial_view_state=pdk.ViewState(
            latitude=DELHI_LAT, longitude=DELHI_LON, zoom=9.5, pitch=50),
        layers=[pdk.Layer(
            "ScatterplotLayer",
            data=df,
            get_position='[lon, lat]',
            get_fill_color='color',
            get_radius=250,
            pickable=True,
            opacity=0.8,
            stroked=True,
            get_line_color=[0, 0, 0, 100],
            line_width_min_pixels=1,
        )],
        tooltip={"html": "<b>{station_name}</b><br/>AQI: {aqi}<br/>Category: {category}<br/>Last Updated: {last_updated}",
                 "style": {"color": "white"}}
    ))

def render_alerts_tab(df):
    """Renders health alerts and advice based on current AQI levels."""
    st.markdown('<div class="section-header">🔔 Health Alerts & Recommendations</div>',
                unsafe_allow_html=True)
    max_aqi = df['aqi'].max()
    advice = get_aqi_category(max_aqi)[3]
    st.info(
        f"**Current Situation:** Based on the highest AQI of **{max_aqi:.0f}**, the recommended action is: **{advice}**", icon="ℹ️")

    alerts = {
        "Hazardous": (df[df['aqi'] > 300], "alert-hazardous"),
        "Very Unhealthy": (df[(df['aqi'] > 200) & (df['aqi'] <= 300)], "alert-very-unhealthy"),
        "Unhealthy": (df[(df['aqi'] > 150) & (df['aqi'] <= 200)], "alert-unhealthy")
    }
    has_alerts = False
    for level, (subset, card_class) in alerts.items():
        if not subset.empty:
            has_alerts = True
            st.markdown(
                f"**{subset.iloc[0]['emoji']} {level} Conditions Detected**")
            for _, row in subset.sort_values('aqi', ascending=False).iterrows():
                st.markdown(
                    f'<div class="alert-card {card_class}"><span style="font-weight: 600;">{row["station_name"]}</span> <span style="font-weight: 700; font-size: 1.2rem;">AQI {row["aqi"]:.0f}</span></div>', unsafe_allow_html=True)

    if not has_alerts:
        st.success("✅ No significant air quality alerts at the moment. AQI levels are currently within the good to moderate range for most areas.", icon="✅")


def render_alert_subscription_tab(df):
    st.subheader("📩 Real-Time AQI Alerts (via SMS)")

    polygon = st.session_state.get("delhi_polygon", None)
    if polygon is None:
        st.error("Delhi boundary polygon not loaded.")
        return

    kriging_data = st.session_state.get("kriging_output", None)
    if kriging_data is None:
        st.info("🔄 Generating kriging interpolation automatically...")
        
        if len(df) < 3:
            st.error("Not enough AQI stations within Delhi boundary for interpolation (minimum 3 required).")
            return
            
        delhi_bounds_tuple = (28.40, 28.88, 76.84, 77.35)
        
        try:
            with st.spinner("Performing spatial interpolation..."):
                lon_grid, lat_grid, z_grid = perform_kriging_correct(
                    df,
                    delhi_bounds_tuple,
                    polygon=polygon,
                    resolution=200
                )
                st.session_state["kriging_output"] = (lon_grid, lat_grid, z_grid)
                st.success("✅ Kriging data generated successfully!")
                kriging_data = (lon_grid, lat_grid, z_grid)
        except Exception as e:
            st.error(f"Error generating kriging data: {str(e)}")
            return
    
    lon_grid, lat_grid, z_grid = kriging_data

    st.markdown("### 📍 Select Your Location")
    
    location_method = st.radio(
        "Choose how to provide your location:",
        ["🗺️ Select from Map/Dropdown", "✍️ Enter Coordinates Manually", "📡 Use Device GPS"],
        horizontal=True
    )
    
    user_lat = None
    user_lon = None
    
    if location_method == "🗺️ Select from Map/Dropdown":
        st.info("💡 Select a popular location in Delhi or choose from monitoring stations")
        
        popular_locations = {
            "Connaught Place": (28.6315, 77.2167),
            "India Gate": (28.6129, 77.2295),
            "Red Fort": (28.6562, 77.2410),
            "Qutub Minar": (28.5244, 77.1855),
            "Lotus Temple": (28.5535, 77.2588),
            "Chandni Chowk": (28.6506, 77.2303),
            "Karol Bagh": (28.6519, 77.1906),
            "Dwarka": (28.5921, 77.0460),
            "Rohini": (28.7496, 77.0670),
            "Nehru Place": (28.5494, 77.2501)
        }
        
        station_locations = {}
        for _, row in df.iterrows():
            station_locations[f"📍 {row['station_name']} (AQI: {row['aqi']:.0f})"] = (row['lat'], row['lon'])
        
        all_locations = {**popular_locations, **station_locations}
        
        selected_location = st.selectbox(
            "Select Location:",
            options=list(all_locations.keys())
        )
        
        user_lat, user_lon = all_locations[selected_location]
        st.success(f"✅ Selected: {selected_location} ({user_lat:.4f}, {user_lon:.4f})")
        
    elif location_method == "✍️ Enter Coordinates Manually":
        st.info("💡 Enter latitude and longitude coordinates")
        col1, col2 = st.columns(2)
        with col1:
            user_lat = st.number_input("Latitude", format="%.6f", step=0.000001, value=28.6139)
        with col2:
            user_lon = st.number_input("Longitude", format="%.6f", step=0.000001, value=77.2090)
            
    else:
        st.info("📡 Click the button below to request your device location")
        
        if st.button("📍 Get My Location", key="gps_button"):
            st.markdown("""
                <script>
                if (navigator.geolocation) {
                    navigator.geolocation.getCurrentPosition(
                        function(position) {
                            const lat = position.coords.latitude;
                            const lon = position.coords.longitude;
                            sessionStorage.setItem('user_lat', lat);
                            sessionStorage.setItem('user_lon', lon);
                            window.location.reload();
                        },
                        function(error) {
                            alert('Error getting location: ' + error.message);
                        }
                    );
                } else {
                    alert('Geolocation is not supported by your browser');
                }
                </script>
            """, unsafe_allow_html=True)
        
        query_params = st.experimental_get_query_params()
        if 'lat' in query_params and 'lon' in query_params:
            try:
                user_lat = float(query_params['lat'][0])
                user_lon = float(query_params['lon'][0])
                st.success(f"✅ GPS Location: {user_lat:.4f}, {user_lon:.4f}")
            except:
                st.warning("⚠️ Could not parse GPS coordinates")

    st.markdown("---")
    st.markdown("### 📱 SMS Alert Configuration")
    
    col1, col2 = st.columns(2)
    
    with col1:
        phone_number = st.text_input(
            "Phone Number (with country code)", 
            placeholder="+919876543210 or 919876543210",
            help="Enter phone number with country code (e.g., +91 for India). The + sign is optional."
        )
        
    with col2:
        carrier_name = st.selectbox(
            "Select Carrier (for Gateway)",
            options=list(SMS_GATEWAYS.keys()),
            help="Select your mobile carrier to route the SMS correctly."
        )

    if st.button("🚀 Get AQI Alert via SMS", type="primary", use_container_width=True):
        if not phone_number:
            st.warning("⚠️ Please enter a phone number!")
            return

        if user_lat is None or user_lon is None:
            st.warning("⚠️ Please provide your location!")
            return

        try:
            aqi_value, outside = get_aqi_at_location(
                user_lat,
                user_lon,
                lat_grid,
                lon_grid,
                z_grid,
                polygon
            )

            if np.isnan(aqi_value):
                st.error("❌ Could not determine AQI for this location. Please try a different location.")
                return

            if outside:
                st.warning("⚠️ Your location is outside Delhi boundary. Using nearest interpolated AQI value.")

            weather = fetch_weather_data()
            if weather and "current" in weather:
                weather_desc, _ = get_weather_info(weather["current"]["weather_code"])
                temp = weather["current"]["temperature_2m"]
            else:
                weather_desc = "N/A"
                temp = 0.0

            category, _, emoji, advice = get_aqi_category(aqi_value)

            message = f"""📍 Delhi Air Quality Alert

Location: {user_lat:.4f}, {user_lon:.4f}
{emoji} AQI: {aqi_value:.0f} ({category})
🌡️ Temperature: {temp:.1f}°C
🌤️ Weather: {weather_desc}

💡 Health Advice: {advice}

Stay safe!
"""
            gateway = SMS_GATEWAYS[carrier_name]
            
            with st.spinner("Sending SMS via Email Gateway..."):
                success, status_msg = send_sms_via_email(phone_number, gateway, message)
                
                if success:
                    st.success(f"✅ {status_msg}")
                    st.info("ℹ️ Note: SMS delivery depends on carrier gateway policies. It may appear as an email or text.")
                else:
                    st.error(f"❌ Failed: {status_msg}")
                    
        except Exception as e:
            st.error(f"An unexpected error occurred: {str(e)}")
            import traceback
            st.code(traceback.format_exc())


def render_dummy_forecast_tab():
    """Render a dummy 24-hour AQI forecast using simulated data."""
    st.markdown('<div class="section-header">📈 24-Hour AQI Forecast (Sample)</div>',
                unsafe_allow_html=True)

    st.markdown("""
    <div style="background-color: #E3F2FD; padding: 1rem; border-radius: 10px; border-left: 4px solid #2196F3; margin-bottom: 1rem;">
        <p style="color: #0D47A1; margin: 0; font-weight: 500;">
        This sample forecast simulates how the Air Quality Index (AQI) may change over the next 24 hours.
        </p>
    </div>
    """, unsafe_allow_html=True)

    hours = np.arange(0, 24)
    base_aqi = 120 + 40 * np.sin(hours / 3) + np.random.normal(0, 5, size=24)
    timestamps = [datetime.now() + timedelta(hours=i) for i in range(24)]
    forecast_df = pd.DataFrame({
        "timestamp": timestamps,
        "forecast_aqi": np.clip(base_aqi, 40, 300)
    })

    fig = px.line(
        forecast_df,
        x="timestamp",
        y="forecast_aqi",
        title="Predicted AQI Trend for Next 24 Hours (Simulated)",
        markers=True,
        line_shape="spline"
    )
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Predicted AQI",
        showlegend=False,
        margin=dict(t=40, b=20, l=0, r=20),
        paper_bgcolor='white',
        plot_bgcolor='white',
        title_font_color="#0D47A1",
        font_color="#0D47A1",
        xaxis=dict(gridcolor='#E3F2FD'),
        yaxis=dict(gridcolor='#E3F2FD')
    )

    st.plotly_chart(fig, use_container_width=True)

    avg_aqi = forecast_df["forecast_aqi"].mean()
    max_aqi = forecast_df["forecast_aqi"].max()
    min_aqi = forecast_df["forecast_aqi"].min()

    st.markdown(f"""
    <div style="background-color: white; padding: 1rem; border-radius: 10px; border-left: 5px solid #1976D2; margin-top: 1rem; color: #1E293B;">
        <b>Average Forecasted AQI:</b> {avg_aqi:.1f}  
        <br><b>Expected Range:</b> {min_aqi:.1f} – {max_aqi:.1f}
        <br><b>Air Quality Outlook:</b> Moderate to Unhealthy range over the next day.
    </div>
    """, unsafe_allow_html=True)

def render_analytics_tab(df):
    """Renders charts and data analytics."""
    st.markdown('<div class="section-header">📊 Data Analytics</div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])

    with c1:
        st.markdown("**AQI Category Distribution**")
        category_counts = df['category'].value_counts()
        fig = px.pie(
            values=category_counts.values, names=category_counts.index, hole=0.4,
            color=category_counts.index,
            color_discrete_map={
                "Good": "#009E60", "Moderate": "#FFD600", "Unhealthy for Sensitive Groups": "#F97316",
                "Unhealthy": "#DC2626", "Very Unhealthy": "#9333EA", "Hazardous": "#7E22CE"
            }
        )
        fig.update_traces(textinfo='percent+label',
                          pull=[0.05]*len(category_counts.index))
        fig.update_layout(
            showlegend=False,
            margin=dict(t=0, b=0, l=0, r=0),
            paper_bgcolor='#F5F5F5',
            plot_bgcolor='#F5F5F5'
        )
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("**Top 10 Most Polluted Stations**")
        top_10 = df.nlargest(10, 'aqi').sort_values('aqi', ascending=True)
        fig = px.bar(
            top_10, x='aqi', y='station_name', orientation='h',
            color='aqi', color_continuous_scale=px.colors.sequential.Reds
        )
        fig.update_layout(
            xaxis_title="AQI",
            yaxis_title="",
            showlegend=False,
            margin=dict(t=20, b=20, l=0, r=20),
            paper_bgcolor='#F5F5F5',
            plot_bgcolor='#F5F5F5',
            xaxis=dict(gridcolor='#DDDDDD'),
            yaxis=dict(gridcolor='#DDDDDD')
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Full Station Data**")
    display_df = df[['station_name', 'aqi', 'category',
                     'last_updated']].sort_values('aqi', ascending=False)
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def render_statistical_insights_tab(df):
    """New tab for statistical insights based on historical data"""
    st.markdown('<div class="section-header">📈 Statistical Insights & Patterns</div>',
                unsafe_allow_html=True)
    
    historical_df = get_historical_stats()
    
    render_hourly_boxplot(historical_df)
    
    st.markdown("---")
    
    render_risk_frequency_histogram(historical_df)


def render_health_advisor_tab(df):
    """Individual health advisor tab with personalized recommendations"""
    st.markdown('<div class="section-header">🧍 Personal Health Advisor</div>',
                unsafe_allow_html=True)
    
    st.info("💡 Get personalized air quality recommendations based on your health profile")

    if df.empty:
        st.warning("No AQI data available for analysis.")
        return

    df['dist'] = df.apply(
        lambda r: ((r['lat']-DELHI_LAT)**2 + (r['lon']-DELHI_LON)**2)**0.5, axis=1
    )
    nearest = df.loc[df['dist'].idxmin()]
    est_aqi = nearest['aqi']

    st.metric("Estimated AQI at Your Location", int(est_aqi), 
             delta=f"{get_aqi_category(est_aqi)[0]}")

    st.markdown("#### 🏥 Your Health Profile")
    conds = st.text_input(
        "Enter your health conditions (comma-separated):",
        placeholder="e.g., asthma, child, elderly, pregnant",
        help="Be specific: asthma, COPD, heart disease, pregnant, child, elderly, etc."
    )
    cond_list = [c.strip() for c in conds.split(",") if c.strip()]
    
    if cond_list:
        st.info(f"📋 Health conditions: {', '.join(cond_list)}")

    if st.button("🤖 Get Personalized Advice", type="primary", use_container_width=True):
        with st.spinner("Analyzing your situation..."):
            rec = get_personalized_recommendation(est_aqi, cond_list)

        st.markdown(
            f"""<div class='health-card'>
            <h3>{rec['aqi_category']} (AQI {rec['aqi_value']:.0f})</h3>
            <p><strong>Risk Profile:</strong> {rec['risk_profile'].title()}</p>
            <p>{rec['summary']}</p>
            </div>""",
            unsafe_allow_html=True
        )

        col1, col2 = st.columns(2)
        
        with col1:
            with st.expander("⚠️ Precautions", expanded=True):
                for p in rec["precautions"]:
                    st.write("• " + p)

        with col2:
            with st.expander("✅ Recommended Activities", expanded=True):
                for a in rec["recommended_activities"]:
                    st.write("• " + a)

        with st.expander("🏥 Health Implications"):
            st.write(rec.get("health_implications", "No specific health implications noted."))


def render_family_advisor_tab(df):
    """Family health advisor tab"""
    st.markdown('<div class="section-header">👨‍👩‍👧 Family Health Advisor</div>',
                unsafe_allow_html=True)
    
    st.info("💡 Get personalized recommendations for each family member")

    if "family" not in st.session_state:
        st.session_state.family = []

    st.markdown("#### 👥 Add Family Members")
    
    with st.form("add_family"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name:", placeholder="e.g., John")
            age = st.number_input("Age:", 1, 120, 30)
        with col2:
            conds = st.text_input(
                "Health conditions:", 
                placeholder="e.g., asthma, child",
                help="Enter relevant health conditions or age group"
            )
        
        submit = st.form_submit_button("➕ Add Family Member", use_container_width=True)
        if submit and name:
            st.session_state.family.append({
                "name": name,
                "age": age,
                "conds": [c.strip() for c in conds.split(",") if c.strip()]
            })
            st.success(f"✅ Added {name} to family members")
            st.rerun()

    if st.session_state.family:
        st.markdown("#### 👨‍👩‍👧‍👦 Current Family Members")
        
        for idx, m in enumerate(st.session_state.family):
            col1, col2 = st.columns([4, 1])
            with col1:
                conditions_text = ', '.join(m['conds']) if m['conds'] else 'No specific conditions'
                st.write(f"**{m['name']}** • {m['age']} years • {conditions_text}")
            with col2:
                if st.button("🗑️", key=f"remove_{idx}", help=f"Remove {m['name']}"):
                    st.session_state.family.pop(idx)
                    st.rerun()

        st.markdown("---")
        
        if st.button("🤖 Generate Family Recommendations", type="primary", use_container_width=True):
            if df.empty:
                st.warning("No AQI data available for analysis.")
            else:
                est_aqi = df['aqi'].mean()
                
                st.markdown(f"### 📊 Family Analysis for AQI {est_aqi:.0f}")
                
                with st.spinner("Analyzing recommendations for each family member..."):
                    for m in st.session_state.family:
                        rec = get_personalized_recommendation(est_aqi, m['conds'])
                        
                        st.markdown(
                            f"""<div class='health-card'>
                            <h4>{m['name']} ({m['age']} years)</h4>
                            <p><b>Category:</b> {rec['aqi_category']} | <b>Risk Profile:</b> {rec['risk_profile'].title()}</p>
                            <p>{rec['summary']}</p>
                            <details>
                            <summary style='cursor: pointer; color: #2196F3; font-weight: 600;'>View Details</summary>
                            <div style='margin-top: 1rem;'>
                            <p><b>Key Precautions:</b></p>
                            <ul>{''.join([f'<li>{p}</li>' for p in rec['precautions'][:3]])}</ul>
                            <p><b>Recommended Activities:</b></p>
                            <ul>{''.join([f'<li>{a}</li>' for a in rec['recommended_activities'][:3]])}</ul>
                            </div>
                            </details>
                            </div>""",
                            unsafe_allow_html=True
                        )
        
        if st.button("🗑️ Clear All Family Members"):
            st.session_state.family = []
            st.rerun()
    else:
        st.info("👥 No family members added yet. Use the form above to add family members.")


# ==========================
# MAIN APP EXECUTION
# ==========================
aqi_data_raw = fetch_live_data()

if aqi_data_raw.empty:
    st.error("⚠️ **Could not fetch live AQI data.** The API may be down or there's a network issue. Please try again later.", icon="🚨")
    render_header(aqi_data_raw)
else:
    saved, total_snapshots = save_historical_snapshot(aqi_data_raw)
    if saved:
        st.toast(f"✅ Snapshot saved! Total: {total_snapshots}", icon="💾")
    
    delhi_gdf = st.session_state.get("delhi_gdf", None)
    delhi_polygon = st.session_state.get("delhi_polygon", None)
    
    aqi_data_filtered = pd.DataFrame()
    
    if delhi_polygon is not None:
        geometry = [Point(xy) for xy in zip(aqi_data_raw['lon'], aqi_data_raw['lat'])]
        stations_gdf = gpd.GeoDataFrame(aqi_data_raw, crs="epsg:4326", geometry=geometry)
        clipped_gdf = gpd.clip(stations_gdf, delhi_polygon)
        
        if not clipped_gdf.empty:
            aqi_data_filtered = pd.DataFrame(clipped_gdf.drop(columns='geometry'))
    
    if aqi_data_filtered.empty:
        st.warning("⚠️ **No monitoring stations found inside the Delhi boundary.** Showing all available data for the region.", icon="⚠️")
        aqi_data_to_display = aqi_data_raw
    else:
        st.success(f"✅ Loaded {len(aqi_data_filtered)} monitoring stations inside the Delhi boundary.", icon="🛰️")
        aqi_data_to_display = aqi_data_filtered

    render_header(aqi_data_to_display)

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "🗺️ Live Map", 
        "🔔 Alerts & Health",
        "📊 Analytics", 
        "📈 Statistical Insights",
        "📱 SMS Alerts",
        "🔮 Forecast",
        "🔥 Kriging Heatmap",
        "🏥 Health Advisor"
    ])

    with tab1:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_map_tab(aqi_data_to_display)
            st.markdown('</div>', unsafe_allow_html=True)
            
    with tab2:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_alerts_tab(aqi_data_to_display)
            st.markdown('</div>', unsafe_allow_html=True)
            
    with tab3:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_analytics_tab(aqi_data_to_display)
            st.markdown('</div>', unsafe_allow_html=True)
            
    with tab4:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_statistical_insights_tab(aqi_data_to_display)
            st.markdown('</div>', unsafe_allow_html=True)
            
    with tab5:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_alert_subscription_tab(aqi_data_to_display)
            st.markdown('</div>', unsafe_allow_html=True)
            
    with tab6:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_dummy_forecast_tab()
            st.markdown('</div>', unsafe_allow_html=True)
            
    with tab7:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            render_kriging_tab(aqi_data_to_display)
            st.markdown('</div>', unsafe_allow_html=True)
    
    with tab8:
        with st.container():
            st.markdown('<div class="content-card">', unsafe_allow_html=True)
            
            # Create sub-tabs for Individual and Family advisors
            subtab1, subtab2 = st.tabs(["🧍 Individual Advisor", "👨‍👩‍👧 Family Advisor"])
            
            with subtab1:
                render_health_advisor_tab(aqi_data_to_display)
            
            with subtab2:
                render_family_advisor_tab(aqi_data_to_display)
            
            st.markdown('</div>', unsafe_allow_html=True)
