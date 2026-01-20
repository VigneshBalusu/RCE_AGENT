import streamlit as st
import requests
import base64
import time

# --------------------------------------------------
# 1. PAGE CONFIG & MODERN STYLING
# --------------------------------------------------
st.set_page_config(
    page_title="RCE Voice Assistant",
    page_icon="🎙️",
    layout="centered"
)

st.markdown("""
    <style>
    /* Hide Header & Footer */
    header, footer, .stDeployButton {display:none !important;}
    
    /* Center the Main Interface */
    .block-container {
        padding-top: 2rem;
        text-align: center;
    }
    
    /* Status Badge */
    .status-badge {
        background-color: #1e1e1e;
        color: #00FF94;
        padding: 8px 16px;
        border-radius: 20px;
        border: 1px solid #333;
        font-family: monospace;
        font-size: 14px;
        margin-bottom: 20px;
        display: inline-block;
    }
    
    /* AI Response Card */
    .ai-card {
        background-color: #262730;
        border-left: 5px solid #FF4B4B;
        padding: 20px;
        border-radius: 10px;
        margin-top: 20px;
        text-align: left;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    
    /* Chat Input Styling */
    .stChatInput {
        position: fixed;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%);
        width: 80%;
        max-width: 700px;
        z-index: 100;
    }
    </style>
""", unsafe_allow_html=True)

# --------------------------------------------------
# 2. CONFIGURATION
# --------------------------------------------------
try:
    API_URL = st.secrets["N8N_WEBHOOK_URL"]
except:
    API_URL = "http://localhost:5678/webhook/chat"

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_audio" not in st.session_state:
    st.session_state.last_audio = None
if "status" not in st.session_state:
    st.session_state.status = "Ready"

# --------------------------------------------------
# 3. HELPER FUNCTION (With Error Handling)
# --------------------------------------------------
def query_n8n(payload, mode="text"):
    try:
        if mode == "voice":
            files = {'file': ('voice.wav', payload, 'audio/wav')}
            data = {'voice_mode': 'true'}
            return requests.post(API_URL, files=files, data=data, timeout=120)
        else:
            return requests.post(API_URL, json={"query": payload, "voice_mode": False}, timeout=120)
    except Exception as e:
        st.error(f"Network Error: {e}")
        return None

# --------------------------------------------------
# 4. MAIN UI
# --------------------------------------------------
st.title("🎙️ RCE Assistant")

# Status Indicator
status_color = "#00FF94" if st.session_state.status == "Ready" else "#FF4B4B"
st.markdown(f"<div><span class='status-badge' style='color:{status_color}'>● {st.session_state.status}</span></div>", unsafe_allow_html=True)

# --- A. VOICE INPUT ---
voice_disabled = (st.session_state.status != "Ready")
audio_value = st.audio_input("Tap to Speak")

# --- B. TEXT INPUT ---
text_query = st.chat_input("Type a message...", disabled=voice_disabled)

# --------------------------------------------------
# 5. LOGIC ENGINE
# --------------------------------------------------

# LOGIC 1: HANDLE VOICE
if audio_value is not None:
    # Only run if the audio is NEW
    if st.session_state.last_audio != audio_value:
        st.session_state.last_audio = audio_value
        st.session_state.status = "Thinking..."
        st.rerun() 

    # Processing State
    if st.session_state.status == "Thinking...":
        
        # 1. Play User Audio
        audio_bytes = audio_value.read()
        with st.expander("Your Audio", expanded=False):
            st.audio(audio_bytes, format="audio/wav")

        # 2. Send to AI
        with st.spinner("Processing Voice..."):
            response = query_n8n(audio_bytes, mode="voice")

        # 3. Handle Response
        if response and response.status_code == 200:
            st.session_state.status = "Speaking..."
            content_type = response.headers.get('Content-Type', '')

            # Case A: Audio File Returned (Binary)
            if 'audio' in content_type or 'application/octet-stream' in content_type:
                st.markdown("<div class='ai-card'><h3>🔊 AI Speaking...</h3></div>", unsafe_allow_html=True)
                st.audio(response.content, format="audio/mp3", autoplay=True)
                st.session_state.messages.append({"role": "assistant", "type": "audio", "data": "Audio Response"})
            
            # Case B: Text JSON Returned
            else:
                try:
                    data = response.json()
                    if isinstance(data, list): data = data[0]
                    text = data.get("output") or "Done"
                    st.markdown(f"<div class='ai-card'><b>AI:</b> {text}</div>", unsafe_allow_html=True)
                    st.session_state.messages.append({"role": "assistant", "type": "text", "data": text})
                except Exception as e:
                    # DEBUGGING: If JSON fails, show exactly what n8n sent
                    st.error("Error: n8n did not return valid JSON.")
                    with st.expander("See Raw Response"):
                        st.write(response.text)

        else:
            st.error("Connection Failed or Timeout")

        # 4. Reset Status
        st.session_state.status = "Ready"

# LOGIC 2: HANDLE TEXT
elif text_query:
    st.session_state.status = "Thinking..."
    st.session_state.messages.append({"role": "user", "type": "text", "data": text_query})
    
    with st.spinner("Thinking..."):
        response = query_n8n(text_query, mode="text")
        
    if response and response.status_code == 200:
        try:
            data = response.json()
            if isinstance(data, list): data = data[0]
            text = data.get("output") or "Done"
            
            st.markdown(f"<div class='ai-card'><b>AI:</b> {text}</div>", unsafe_allow_html=True)
            st.session_state.messages.append({"role": "assistant", "type": "text", "data": text})
        except Exception:
             st.error("Error: n8n did not return valid JSON.")
             with st.expander("See Raw Response"):
                 st.write(response.text)
    else:
        st.error("Connection Failed")
    
    st.session_state.status = "Ready"
    st.rerun()

# --------------------------------------------------
# 6. HISTORY DRAWER
# --------------------------------------------------
with st.expander("View Conversation History"):
    for msg in st.session_state.messages:
        icon = "👤" if msg['role'] == "user" else "🤖"
        if msg.get("type") == "audio":
            st.write(f"{icon} [Audio Message]")
        else:
            st.write(f"{icon} {msg.get('data')}")