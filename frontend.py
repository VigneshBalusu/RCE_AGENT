import streamlit as st
import requests
import re

# --- CONFIGURATION ---
API_URL = "http://localhost:5678/webhook/chat"

# --- PAGE SETUP ---
st.set_page_config(page_title="RCE AI Assistant", page_icon="🎓")
st.title("RCE Agent")

# --- CSS TO HIDE GLITCHES ---
st.markdown("""
    <style>
    .stDeployButton {display:none;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- CLEANER FUNCTION ---
def clean_ai_response(text):
    if not text: return ""
    text = text.strip()
    parts = re.split(r'\n\s*\n', text)
    if len(parts) == 2:
        if parts[0].strip()[:20] == parts[1].strip()[:20]:
            return parts[0].strip()
    mid = len(text) // 2
    first_half = text[:mid].strip()
    second_half = text[mid:].strip()
    if len(text) > 40 and second_half.startswith(first_half[:30]):
        return first_half
    return text

# --- DISPLAY HISTORY ---
# This loops through past messages and paints them first
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- MAIN CHAT LOGIC ---
if prompt := st.chat_input("Ask about Syllabus, Fees, or Faculty..."):
    
    # 1. Display User Message immediately
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 2. Handle the Assistant Response
    with st.chat_message("assistant"):
        
        # ✅ FIX: Create a dedicated placeholder for the spinner/text
        # This forces Streamlit to treat this area as "fresh"
        response_placeholder = st.empty()
        
        full_response = ""
        
        # 3. Run the "Thinking" Spinner inside the placeholder
        with response_placeholder.container():
            with st.spinner("Searching official records..."):
                try:
                    # Call the API
                    response = requests.post(API_URL, json={"query": prompt})
                    
                    if response.status_code == 200:
                        data = response.json()
                        raw_text = data.get('output', "⚠️ No response text.") or data.get('response', "")
                        
                        # Apply Cleaning
                        full_response = clean_ai_response(raw_text)
                    else:
                        full_response = f"⚠️ Server Error: {response.status_code}"
                except Exception as e:
                    full_response = f"❌ Connection Error: {e}"

        # 4. Final Display (Overwrites the spinner container)
        # We check if we actually have text to avoid printing empty bubbles
        if full_response:
            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})