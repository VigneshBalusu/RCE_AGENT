import os
import struct
import asyncio
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Import from your files
from voice_handler import process_voice

# Need pysqlite3 fix before importing main
import sys
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

from main import search_db


def record(seconds=4, rate=16000):
    print(f"\n  Speak now... ({seconds} seconds)")
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype='int16')
    sd.wait()
    print("  Recording done.")

    raw = audio.tobytes()
    header = struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + len(raw), b'WAVE', b'fmt ', 16,
        1, 1, rate, rate * 2, 2, 16, b'data', len(raw)
    )
    return header + raw


def play(audio_bytes):
    if not audio_bytes or len(audio_bytes) < 100:
        print("  No audio to play")
        return

    path = "test_call_response.wav"
    with open(path, "wb") as f:
        f.write(audio_bytes)
    print(f"  Saved: {path} ({len(audio_bytes)} bytes)")

    try:
        import wave
        with wave.open(path, 'rb') as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if channels > 1:
                data = data.reshape(-1, channels)
            print(f"  Playing... (rate={rate})")
            sd.play(data, rate)
            sd.wait()
            print("  Playback done.")
    except Exception as e:
        print(f"  Playback error: {e}")
        print(f"  Manually play: {path}")


async def test():

    # --- TURN 1 ---
    print("\n" + "="*60)
    print("TURN 1: Ask a question (e.g. 'Who is the principal?')")
    print("="*60)

    wav1 = record(seconds=4)
    audio1 = await process_voice(wav1, search_db)

    print("\n  AI Response:")
    play(audio1)

    # --- TURN 2 ---
    print("\n" + "="*60)
    print("TURN 2: Ask a follow-up (e.g. 'What is his phone number?')")
    print("="*60)

    wav2 = record(seconds=4)
    audio2 = await process_voice(wav2, search_db)

    print("\n  AI Response:")
    play(audio2)

    # --- TURN 3 ---
    print("\n" + "="*60)
    print("TURN 3: Ask something new (e.g. 'What are CSE fees?')")
    print("="*60)

    wav3 = record(seconds=4)
    audio3 = await process_voice(wav3, search_db)

    print("\n  AI Response:")
    play(audio3)

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("  Check audio files: test_call_response.wav")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(test())