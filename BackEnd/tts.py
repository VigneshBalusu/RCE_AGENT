import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import edge_tts
import uvicorn

# --- CONFIGURATION ---
app = FastAPI(title="RCE Voice Service (Edge TTS)")

# 1. Add CORS (Essential for external access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Voice Mapping
VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",   # English (India)
    "te": "te-IN-ShrutiNeural",    # Telugu
    "hi": "hi-IN-SwaraNeural"      # Hindi
}

class TTSRequest(BaseModel):
    text: str
    lang: str = "en"

@app.post("/tts")
async def generate_audio(request: TTSRequest):
    try:
        print(f"🎤 Generating Audio for: '{request.text[:30]}...' ({request.lang})")
        voice = VOICE_MAP.get(request.lang, VOICE_MAP["en"])
        communicate = edge_tts.Communicate(request.text, voice)
        
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]

        return Response(content=audio_data, media_type="audio/mpeg")

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # --- CRITICAL FIX FOR RENDER ---
    # Render provides the port in the 'PORT' environment variable.
    # We fallback to 8005 only if running locally.
    port = int(os.environ.get("PORT", 8005))
    
    print("---------------------------------------------------------")
    print(f"🚀 TTS Server Running on Port {port}")
    print("---------------------------------------------------------")
    
    # host="0.0.0.0" is required for Cloud/Docker
    uvicorn.run(app, host="0.0.0.0", port=port)