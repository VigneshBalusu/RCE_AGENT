import streamlit as st
import requests
import re

# --------------------------------------------------
# CONFIGURATION (SAFE FOR LOCAL + CLOUD)
# --------------------------------------------------
try:
    API_URL = st.secrets["N8N_WEBHOOK_URL"]  # Streamlit Cloud
except Exception:
    API_URL = "http://localhost:5678/webhook/chat"  # Local fallback


# --------------------------------------------------
# PAGE SETUP
# --------------------------------------------------
st.set_page_config(
    page_title="RCE AI Assistant",
    page_icon="🎓",
    layout="centered"
)

st.title("🎓 RCE Intelligent Assistant")


# --------------------------------------------------
# CSS (CLEAN UI)
# --------------------------------------------------
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
# CLEANER FUNCTION (REMOVES DUPLICATE / MIRRORED TEXT)
# --------------------------------------------------
def clean_ai_response(text: str) -> str:
    if not text:
        return ""

    text = text.strip()

    # Strategy 1: Remove duplicated blocks
    parts = re.split(r'\n\s*\n', text)
    if len(parts) == 2:
        if parts[0].strip()[:25] == parts[1].strip()[:25]:
            return parts[0].strip()

    # Strategy 2: Mirror detection
    mid = len(text) // 2
    first_half = text[:mid].strip()
    second_half = text[mid:].strip()

    if len(text) > 40 and second_half.startswith(first_half[:30]):
        return first_half

    return text


# --------------------------------------------------
# DISPLAY CHAT HISTORY
# --------------------------------------------------
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


# --------------------------------------------------
# MAIN CHAT LOGIC
# --------------------------------------------------
if prompt := st.chat_input("Ask about syllabus, fees, faculty, admissions..."):

    # Show user message
    with st.chat_message("user"):
        st.markdown(prompt)

    st.session_state.messages.append(
        {"role": "user", "content": prompt}
    )

    # Assistant response
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        final_response = ""

        with response_placeholder.container():
            with st.spinner("Searching official records..."):
                try:
                    response = requests.post(
                        API_URL,
                        json={"query": prompt},
                        timeout=30
                    )

                    if response.status_code == 200:
                        data = response.json()

                        raw_text = (
                            data.get("output")
                            or data.get("response")
                            or "⚠️ No response received."
                        )

                        final_response = clean_ai_response(raw_text)

                    else:
                        final_response = f"⚠️ Server Error: {response.status_code}"

                except requests.exceptions.RequestException as e:
                    final_response = f"❌ Connection Error: {e}"

        if final_response:
            response_placeholder.markdown(final_response)
            st.session_state.messages.append(
                {"role": "assistant", "content": final_response}
            )
