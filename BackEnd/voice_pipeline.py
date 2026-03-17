import asyncio
import os
import re
from sarvam_client import speech_to_text, text_to_speech
from llm_client import generate_query, generate_answer, compress_turn

MAX_HISTORY = 5


class CallSession:
    """Manages one voice call session with conversation history."""

    def __init__(self):
        self.history = []
        print("[SESSION] New call session started", flush=True)

    async def handle_turn(self, audio_bytes: bytes, search_func) -> dict:
        """
        Full voice pipeline:
        Audio → STT → Query Gen → DB Search → Answer Gen → TTS → Audio
        """
        print(f"\n{'-'*40}", flush=True)
        print("[TURN] Processing new voice turn", flush=True)
        print(f"{'-'*40}", flush=True)

        # Step 1: Speech to Text
        print("[STEP 1] Converting speech to text...", flush=True)
        stt_result = await speech_to_text(audio_bytes)
        transcript = stt_result.get("transcript", "")
        language = stt_result.get("language", "en-IN")

        if not transcript:
            print("[TURN] Empty transcript, asking user to repeat", flush=True)
            error_msg = "Sorry, I didn't catch that. Could you please repeat?"
            error_audio = await text_to_speech(error_msg, language)
            return {
                "audio": error_audio,
                "transcript": "",
                "answer": error_msg,
                "language": language,
                "query": "",
                "sources": []
            }

        print(f"[STEP 1] Transcript: '{transcript}'", flush=True)
        print(f"[STEP 1] Language: {language}", flush=True)

        # Step 2: Generate optimized DB query
        print("[STEP 2] Generating optimized query...", flush=True)
        query_result = generate_query(transcript, self.history)
        db_query = query_result["query"]
        web_search = query_result["web_search"]

        if web_search:
            print("[STEP 2] Web search needed but not available in voice mode", flush=True)

        # Step 3: Search database
        print(f"[STEP 3] Searching DB with: '{db_query}'", flush=True)
        chunks = search_func(db_query)
        print(f"[STEP 3] Got {len(chunks)} chunks", flush=True)

        # Step 4: Generate answer
        print("[STEP 4] Generating answer...", flush=True)
        if web_search and not chunks:
            answer = "I don't have the latest information on this. Please check the college website rcee.ac.in for recent updates."
        elif not chunks:
            answer = "I don't have that info. Please contact the college helpdesk at helpdesk@rcee.ac.in or call +91-9492936222."
        else:
            answer = generate_answer(transcript, chunks, self.history)

        print(f"[STEP 4] Answer: {answer[:100]}...", flush=True)

        # Step 5: Text to Speech
        print(f"[STEP 5] Converting answer to speech ({language})...", flush=True)
        audio = await text_to_speech(answer, language)
        print(f"[STEP 5] Audio generated: {len(audio)} bytes", flush=True)

        # Step 6: Compress and save to history
        print("[STEP 6] Compressing turn for history...", flush=True)
        await self._save_history(transcript, answer)

        print(f"[TURN] Complete", flush=True)
        print(f"{'-'*40}\n", flush=True)

        return {
            "audio": audio,
            "transcript": transcript,
            "answer": answer,
            "language": language,
            "query": db_query,
            "chunks_used": len(chunks)
        }

    async def _save_history(self, query: str, answer: str):
        try:
            compressed = compress_turn(query, answer)
            self.history.append(compressed)
            if len(self.history) > MAX_HISTORY:
                self.history = self.history[-MAX_HISTORY:]
            print(f"[SESSION] History: {len(self.history)} turns stored", flush=True)
        except Exception as e:
            print(f"[SESSION] Failed to compress turn: {e}", flush=True)

    def end_session(self):
        turns = len(self.history)
        self.history.clear()
        print(f"[SESSION] Call ended. {turns} turns in session.", flush=True)