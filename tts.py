from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware  # <--- Added
from pydantic import BaseModel
import edge_tts
import uvicorn
import io

# --- CONFIGURATION ---
app = FastAPI(title="RCE Voice Service (Edge TTS)")

# 1. Add CORS (Safety Net for Web Calls)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Voice Mapping (Official Edge TTS Voices)
VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",   # English (India)
    "te": "te-IN-ShrutiNeural",    # Telugu
    "hi": "hi-IN-SwaraNeural"      # Hindi
}

# Define the Input Format
class TTSRequest(BaseModel):
    text: str
    lang: str = "en"  # Default to English

@app.post("/tts")
async def generate_audio(request: TTSRequest):
    try:
        print(f"🎤 Generating Audio for: '{request.text[:30]}...' ({request.lang})")
        
        # 1. Select Voice
        voice = VOICE_MAP.get(request.lang, VOICE_MAP["en"])
        
        # 2. Generate Audio
        communicate = edge_tts.Communicate(request.text, voice)
        
        # 3. Capture Audio Bytes efficiently
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]

        # 4. Return Audio (MP3 is the default format for Edge TTS)
        return Response(content=audio_data, media_type="audio/mpeg")

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Ensure this port (8005) is different from your RAG API (8000)
    print("---------------------------------------------------------")
    print("🚀 TTS Server Running on Port 8005")
    print("---------------------------------------------------------")
    uvicorn.run(app, host="0.0.0.0", port=8005)