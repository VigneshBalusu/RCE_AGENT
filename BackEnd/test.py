# test.py
import asyncio
import websockets
import sounddevice as sd
import numpy as np

SERVER_URL = "ws://localhost:8000/ws/voice"
MIC_RATE = 16000
SPK_RATE = 22050
CHUNK = 1024

ws_global = None
loop = None

def mic_callback(indata, frames, time, status):
    if ws_global is None:
        return
    pcm = (indata[:, 0] * 32767).astype(np.int16)
    asyncio.run_coroutine_threadsafe(ws_global.send(pcm.tobytes()), loop)

async def main():
    global ws_global, loop
    loop = asyncio.get_event_loop()

    print("🔌 Connecting to server...")
    async with websockets.connect(SERVER_URL) as ws:
        ws_global = ws
        print("✅ Connected! Speak into mic.\n")

        mic_stream = sd.InputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype='float32',
            blocksize=CHUNK,
            callback=mic_callback
        )

        with mic_stream:
            print("🎙️  Mic live!\n")
            async for message in ws:
                if isinstance(message, bytes):
                    print(f"📦 Binary: {len(message)} bytes")
                    if len(message) >= 2:
                        pcm = np.frombuffer(message, dtype=np.int16).astype(np.float32) / 32768.0
                        sd.play(pcm, samplerate=SPK_RATE, blocking=False)
                else:
                    print(f"📝 Text: {message[:100]}")

try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\n🛑 Stopped.")