from __future__ import annotations
import os
import streamlit as st
import pandas as pd
from supabase import create_client, Client

st.set_page_config(page_title="Solar Live Dashboard", layout="wide")

st.title("Solar Live Dashboard")
st.caption("Live solar plant data integrated with Supabase DB.")

# Retrieve Supabase credentials from Streamlit Secrets or Environment
supabase_url = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL"))
supabase_key = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY"))

if not supabase_url or not supabase_key:
    st.error("Supabase credentials not found. Please configure SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets.")
    st.stop()

@st.cache_resource
def get_supabase_client() -> Client:
    return create_client(supabase_url, supabase_key)

supabase = get_supabase_client()

@st.cache_data(ttl=300)
def fetch_data():
    # Attempt to fetch from your solar data table (adjust table name if different)
    response = supabase.table("solar_data").select("*").execute()
    return pd.DataFrame(response.data)

try:
    df = fetch_data()
    
    if df.empty:
        st.warning("Connected to Supabase, but no data was returned from the table.")
    else:
        st.subheader("Plant Data Overview")
        st.dataframe(df, use_container_width=True)
        
        # Display key summary metrics if numerical columns exist
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        if numeric_cols:
            st.subheader("Metrics Summary")
            cols = st.columns(min(len(numeric_cols), 4))
            for idx, col in enumerate(numeric_cols[:4]):
                cols[idx % 4].metric(label=col.replace('_', ' ').title(), value=f"{df[col].iloc[-1]:,.2f}")

except Exception as e:
    st.error(f"Error fetching data from Supabase: {e}")
    st.info("Ensure your table name in Supabase matches or update the script to reflect your schema.")
