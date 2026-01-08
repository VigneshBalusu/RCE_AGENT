import streamlit as st
import requests
import re

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------
# DIRECT CONNECTION: Streamlit -> Railway n8n
# This is the "Production" setup.
API_URL = "https://n8n-production-19b7.up.railway.app/webhook/chat"

# --------------------------------------------------
# PAGE SETUP
# --------------------------------------------------
st.set_page_config(
    page_title="RCE AI Assistant",
    page_icon="🎓",
    layout="centered"
)

st.title("🎓 RCE Intelligent Assistant")

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

    # 1. Show User Message
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 2. Get Assistant Response
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        
        with st.spinner("Processing request..."):
            try:
                # DIRECT CALL TO N8N
                response = requests.post(
                    API_URL,
                    json={"query": prompt},
                    timeout=30  # Wait up to 30s for n8n + Python DB
                )

                if response.status_code == 200:
                    data = response.json()
                    # Check common JSON keys n8n might return
                    raw_text = (
                        data.get("output") 
                        or data.get("response") 
                        or data.get("text") 
                        or "⚠️ No 'output' key in n8n response."
                    )
                    final_response = clean_ai_response(raw_text)
                else:
                    final_response = f"⚠️ n8n Error: {response.status_code}"

            except requests.exceptions.RequestException as e:
                final_response = f"❌ Connection Error: Could not reach n8n.\nError: {e}"

        response_placeholder.markdown(final_response)
        st.session_state.messages.append({"role": "assistant", "content": final_response})