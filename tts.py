import os
import uvicorn
import uuid
import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- CONFIGURATION ---
# Create a folder for temporary audio files to prevent memory crashes
OUTPUT_DIR = "generated_audio"
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="RCE Voice Service")

# 1. Add CORS (Essential for n8n & External Access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Voice Mapping (Your Language Logic)
VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",   # English (India)
    "te": "te-IN-ShrutiNeural",    # Telugu
    "hi": "hi-IN-SwaraNeural"      # Hindi
}

class TTSRequest(BaseModel):
    text: str
    lang: str = "en"  # Default to English

@app.get("/")
def health_check():
    return {"status": "Voice API Online", "provider": "Edge-TTS"}

@app.post("/tts")
async def generate_audio(request: TTSRequest):
    try:
        print(f"🎤 Generating Audio for: '{request.text[:30]}...' ({request.lang})")
        
        # 1. Select Voice
        voice = VOICE_MAP.get(request.lang, VOICE_MAP["en"])
        
        # 2. Create Unique Filename (Prevents conflicts between users)
        filename = f"{uuid.uuid4()}.mp3"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # 3. Generate Audio & Save to Disk
        communicate = edge_tts.Communicate(request.text, voice)
        await communicate.save(filepath)
        
        # 4. Return File (Better for n8n than streaming bytes)
        return FileResponse(filepath, media_type="audio/mpeg", filename="voice_output.mp3")

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # --- CRITICAL FIX FOR RENDER ---
    # Render assigns a random port. We MUST listen on that port.
    # We fallback to 8000 only if running locally.
    port = int(os.environ.get("PORT", 8000))
    
    print("---------------------------------------------------------")
    print(f"🚀 TTS Server Running on Port {port}")
    print("---------------------------------------------------------")
    
    # host="0.0.0.0" is MANDATORY for Cloud
    uvicorn.run(app, host="0.0.0.0", port=port)
