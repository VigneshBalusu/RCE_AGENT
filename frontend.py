import streamlit as st
import requests
import re
import os

# --------------------------------------------------
# PAGE CONFIG (Must be first)
# --------------------------------------------------
st.set_page_config(
    page_title="RCE AI Assistant",
    page_icon="🎓",
    layout="centered"
)

# --------------------------------------------------
# UNIVERSAL CONNECTION LOGIC
# --------------------------------------------------
# 1. Try to find the Production URL in Streamlit Secrets (Online)
# 2. If not found, default to Localhost (Offline/Testing)
try:
    API_URL = st.secrets["N8N_WEBHOOK_URL"]
    connection_status = "☁️ Cloud Mode"
except (FileNotFoundError, KeyError):
    API_URL = "http://localhost:5678/webhook/chat"
    connection_status = "💻 Local Mode"

# --------------------------------------------------
# UI SETUP
# --------------------------------------------------
st.title("🎓 RCE Intelligent Assistant")
st.caption(f"Status: {connection_status}")  # Helpful indicator

# Clean UI CSS
st.markdown(
    """
    <style>
    .stDeployButton {display:none;}
    footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True
)

# --------------------------------------------------
# SESSION STATE
# --------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

# --------------------------------------------------
# HELPER: CLEAN RESPONSE
# --------------------------------------------------
def clean_ai_response(text: str) -> str:
    if not text: return ""
    text = text.strip()
    parts = re.split(r'\n\s*\n', text)
    if len(parts) == 2 and parts[0].strip()[:25] == parts[1].strip()[:25]:
        return parts[0].strip()
    return text

# --------------------------------------------------
# DISPLAY HISTORY
# --------------------------------------------------
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --------------------------------------------------
# MAIN CHAT LOGIC
# --------------------------------------------------
if prompt := st.chat_input("Ask about syllabus, fees, faculty, admissions..."):

    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        
        with st.spinner("Processing request..."):
            try:
                response = requests.post(
                    API_URL,
                    json={"query": prompt},
                    timeout=30
                )

                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list): data = data[0]
                    
                    raw_text = (
                        data.get("output") 
                        or data.get("response") 
                        or data.get("text") 
                        or "⚠️ No 'output' key in n8n response."
                    )
                    final_response = clean_ai_response(raw_text)
                else:
                    final_response = f"⚠️ n8n Error: {response.status_code}"

            except requests.exceptions.RequestException:
                final_response = f"❌ Connection Error: Could not reach n8n at {API_URL}"

        response_placeholder.markdown(final_response)
        st.session_state.messages.append({"role": "assistant", "content": final_response})