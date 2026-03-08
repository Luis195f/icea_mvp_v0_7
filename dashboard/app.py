import os
import time
import requests
import streamlit as st

API_BASE = os.environ.get("ICEA_API_BASE", "http://backend:8000/api/v1").rstrip("/")

st.set_page_config(page_title="ICEA+ Pilot Dashboard", layout="wide")

st.title("ICEA+ Pilot Dashboard (v0.5)")

refresh = st.sidebar.slider("Auto-refresh (seconds)", min_value=0, max_value=60, value=10)


def fetch_summary():
    r = requests.get(f"{API_BASE}/dashboard/summary/", timeout=10)
    r.raise_for_status()
    return r.json()


def render():
    try:
        data = fetch_summary()
    except Exception as e:
        st.error(f"Cannot reach API: {e}")
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Episodes", data.get("episodes"))
    col2.metric("Raw FHIR resources", data.get("raw_fhir"))
    col3.metric("Dataset rows", data.get("dataset_rows"))
    col3.metric("Window rows", data.get("window_rows"))
    latest_model = data.get("latest_model") or {}
    col4.metric("Latest model", f"{latest_model.get('name')}:{latest_model.get('version')}")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Roster shifts", data.get("roster_shifts"))
    col6.metric("Writebacks", (data.get("writebacks") or {}).get("count"))
    col6.metric("Audit events", data.get("audit_events"))
    col6.metric("Governance decisions", data.get("governance_decisions"))
    col7.metric("Latest causal run", "OK" if (data.get("latest_causal") or {}).get("id") else "—")
    col8.metric("Data quality", "OK" if (data.get("latest_data_quality") or {}).get("id") else "—")

    st.subheader("Normalized volumes")
    st.json(data.get("normalized", {}))

    st.subheader("Latest compute summary")
    st.json((data.get("latest_compute") or {}).get("summary", {}))

    st.subheader("Latest causal summary")
    st.json((data.get("latest_causal") or {}).get("summary", {}))

    st.subheader("Latest data quality snapshot")
    st.json((data.get("latest_data_quality") or {}).get("report", {}))


render()

if refresh > 0:
    time.sleep(refresh)
    st.rerun()
