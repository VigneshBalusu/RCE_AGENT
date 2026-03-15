"""
Quick test: Record mic → send to Sarvam STT API with language_code=unknown
This bypasses n8n completely to verify the API works for Telugu.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY")
if not API_KEY:
    print("❌ SARVAM_API_KEY not found in .env")
    exit(1)

# Record a short audio clip
print("🎙️ Recording 5 seconds of audio... Speak in Telugu!")
import sounddevice as sd
import numpy as np
import wave, io

SAMPLE_RATE = 16000
DURATION = 5

audio = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
sd.wait()
print(f"✅ Recorded {len(audio)} samples")

# Save to WAV in memory
wav_buffer = io.BytesIO()
with wave.open(wav_buffer, 'wb') as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(audio.tobytes())
wav_buffer.seek(0)

# Send to Sarvam STT with language_code=unknown
print("📡 Sending to Sarvam STT API with language_code=unknown...")
response = requests.post(
    "https://api.sarvam.ai/speech-to-text",
    headers={"api-subscription-key": API_KEY},
    files={"file": ("voice.wav", wav_buffer, "audio/wav")},
    data={"language_code": "unknown"}
)

print(f"📡 Status: {response.status_code}")
print(f"📡 Response: {response.json()}")

if response.status_code == 200:
    data = response.json()
    transcript = data.get("transcript", "")
    lang = data.get("language_code", "")
    print(f"\n✅ Transcript: {transcript}")
    print(f"✅ Detected language: {lang}")
else:
    print(f"\n❌ Error: {response.text}")
