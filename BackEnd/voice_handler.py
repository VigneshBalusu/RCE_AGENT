import os
import re
import json
import base64
import asyncio
import aiohttp
import websockets
from openai import OpenAI

# =========================================================
# CONFIG
# =========================================================
LLM_MODEL            = "gpt-4o-mini"
LLM_REASONING_MODEL  = "gpt-4"  # For query generation with enhanced thinking
MAX_MEMORY           = 5
SARVAM_STT_HTTP_URL  = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL       = "wss://api.sarvam.ai/text-to-speech/ws"
SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
TTS_TIMEOUT          = 15

WEB_SEARCH_KEYWORDS = {
    "latest", "recent", "news", "update", "today", "current",
    "announcement", "upcoming", "this month", "this year"
}

CASUAL_WORDS = {
    "thank", "thanks", "bye", "goodbye", "hello", "hi", "hey",
    "okay", "ok", "good", "fine", "welcome", "great", "cool",
    "nice", "sure", "yes", "no", "alright", "right"
}

# =========================================================
# CONTEXT-AWARE ANSWER CHECKING
# =========================================================
async def check_answer_in_context(user_query: str, mem: list) -> tuple:
    """
    Check if the user's question can be answered using previous context.
    Returns (found: bool, answer: str)
    
    Examples:
    - Memory: "Principal: Dr. K. Subba Rao, email principal@rcee.ac.in"
    - Query: "Who is the principal?" → (True, "Dr. K. Subba Rao is the principal...")
    
    - Memory: "CSE HOD: Dr G Chamundeswari, phone 94929,36222"
    - Query: "What is his contact number?" → (True, "You can reach him at 94929,36222")
    """

    if not mem:
        return (False, "")

    context_text = "\n".join(mem)

    client = get_openai()
    
    prompt = f"""You are a context-matching expert. Given previous conversation context and a new question,
determine if the answer is ALREADY in the context.

PREVIOUS CONTEXT:
{context_text}

NEW QUESTION: {user_query}

RULES:
1. If the context HAS the answer → Respond ONLY with: ANSWER: [the answer in natural speech format]
2. If context is PARTIALLY related but no exact answer → Respond: PARTIAL
3. If NO direct relationship → Respond: NOT_FOUND

Examples:
- Context: "Principal: Dr. K. Subba Rao, email principal@rcee.ac.in"
  Question: "Who is the principal?"
  Response: ANSWER: The principal is Dr. K. Subba Rao. You can contact him at principal@rcee.ac.in

- Context: "CSE HOD: Dr G Chamundeswari, phone 94929,36222"
  Question: "What about him?" (him = CSE HOD from context)
  Response: ANSWER: Dr G Chamundeswari is the HOD of CSE department. You can reach him at 94929,36222

RESPOND ONLY with one line: ANSWER: [answer], PARTIAL, or NOT_FOUND"""

    try:
        response = client.chat.completions.create(
            model=LLM_REASONING_MODEL,
            messages=[
                {"role": "system", "content": "You are a context matcher. Respond with only ANSWER:[...], PARTIAL, or NOT_FOUND"},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=300
        )
        
        result = response.choices[0].message.content.strip()
        print(f"[CONTEXT_CHECK] Question: '{user_query[:50]}'...", flush=True)
        print(f"[CONTEXT_CHECK] Result: {result[:80]}...", flush=True)
        
        if result.startswith("ANSWER:"):
            answer = result.replace("ANSWER:", "").strip()
            return (True, answer)
        
        return (False, "")
    
    except Exception as e:
        print(f"[CONTEXT_CHECK] Error: {e}", flush=True)
        return (False, "")

# =========================================================
# SESSION MEMORY (per user)
# =========================================================
sessions = {}

def get_memory(session_id: str) -> list:
    if session_id not in sessions:
        sessions[session_id] = []
    return sessions[session_id]

# =========================================================
# CANCELLED SESSIONS TRACKING
# =========================================================
cancelled_sessions = set()

def cancel_session(session_id: str):
    """Mark a session as cancelled so in-flight processing stops."""
    cancelled_sessions.add(session_id)
    print(f"[CANCEL] Session {session_id} marked for cancellation", flush=True)

def is_session_cancelled(session_id: str) -> bool:
    return session_id in cancelled_sessions

def clear_cancellation(session_id: str):
    cancelled_sessions.discard(session_id)

# =========================================================
# OPENAI CLIENT
# =========================================================
_client = None

def get_openai():
    global _client
    if _client:
        return _client
    _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

# =========================================================
# LANGUAGE DETECTION
# =========================================================
def detect_language(text: str) -> str:
    for char in text:
        if '\u0C00' <= char <= '\u0C7F':
            return "te-IN"
    return "en-IN"

# =========================================================
# WEB SEARCH CHECK
# =========================================================
def needs_web_search(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in WEB_SEARCH_KEYWORDS)

# =========================================================
# CASUAL MESSAGE CHECK
# =========================================================
def is_casual_message(text: str) -> bool:
    words = text.lower().strip().split()
    if len(words) > 4:
        return False
    return any(w.rstrip(".,!?") in CASUAL_WORDS for w in words)

# =========================================================
# SARVAM STT (audio file → text) via HTTP
# =========================================================
async def stt(audio_bytes: bytes) -> dict:

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        return {"transcript": "", "language": "en-IN", "error": "SARVAM_API_KEY missing"}

    print("[STT] Sending file to Sarvam HTTP API...", flush=True)

    data = aiohttp.FormData()
    data.add_field("file", audio_bytes, filename="voice.wav", content_type="audio/wav")
    data.add_field("model", "saarika:v2.5")

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(SARVAM_STT_HTTP_URL, data=data, headers=headers) as response:

                if response.status != 200:
                    error_text = await response.text()
                    print(f"[STT] HTTP Error {response.status}: {error_text}", flush=True)
                    return {"transcript": "", "language": "en-IN", "error": error_text}

                result = await response.json()
                transcript = result.get("transcript", "").strip()
                language = result.get("language_code", "") or detect_language(transcript)

                print(f"[STT] Transcript: '{transcript}'", flush=True)
                print(f"[STT] Language: {language}", flush=True)

                return {"transcript": transcript, "language": language}

    except Exception as e:
        print(f"[STT] Connection error: {e}", flush=True)
        return {"transcript": "", "language": "en-IN", "error": str(e)}

# =========================================================
# TEXT PREPROCESSOR — makes text sound natural when spoken
# =========================================================
def humanize_text(text: str, language: str = "en-IN") -> str:
    """Clean up text for TTS — safe for all languages."""

    # For non-English languages, handle differently
    if language != "en-IN":
        # Only clean up extra spaces and markdown, preserve script-specific characters
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # Remove markdown links
        text = re.sub(r'[*_#`]', '', text)  # Remove markdown formatting
        text = re.sub(r'\s+', ' ', text).strip()  # Clean extra spaces
        return text

    # ── ENGLISH-ONLY PREPROCESSING ──
    # Remove any leftover markdown/formatting
    text = re.sub(r'[*_#`]', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # ── Step 1: Protect phone numbers FIRST ──
    # Find phone numbers (7-12 digit sequences, with optional spaces/dashes)
    # and convert them to digit-by-digit with spaces
    def phone_to_digits(match):
        digits = re.sub(r'[^0-9]', '', match.group())
        if 7 <= len(digits) <= 12:
            # Group as: XXXXX XXXXX or XXX XXXX XXXX
            if len(digits) == 10:
                return f"{digits[:5]}, {digits[5:]}"
            elif len(digits) == 11:
                return f"{digits[:3]}, {digits[3:7]}, {digits[7:]}"
            elif len(digits) == 12:
                return f"{digits[:2]}, {digits[2:7]}, {digits[7:]}"
            else:
                # Generic grouping
                spaced = ' '.join(digits)
                return spaced
        return match.group()

    # Match phone number patterns: +91-XXXXX-XXXXX, 9492936222, 94929 36222, etc.
    text = re.sub(r'\+?91[-\s]?\d{5}[-\s,]?\d{5}', phone_to_digits, text)
    text = re.sub(r'\b\d{10}\b', phone_to_digits, text)
    text = re.sub(r'\b\d{5}[-\s,]\s?\d{5}\b', phone_to_digits, text)

    # Remove leftover +91 prefixes
    text = text.replace("+91-", "").replace("+91 ", "").replace("+91", "")

    # ── Step 2: Abbreviation expansion (only for English) ──
    if language == "en-IN":
        replacements = {
            "Dr.": "Doctor",
            "Prof.": "Professor",
            "HOD": "H O D",
            "CSE": "C S E",
            "ECE": "E C E",
            "EEE": "E E E",
            "AI&DS": "A I and D S",
            "AIML": "A I M L",
            "IOT": "I O T",
            "MBA": "M B A",
            "M.Tech": "M Tech",
            "B.Tech": "B Tech",
            "BTech": "B Tech",
            "MTech": "M Tech",
            "RCEE": "R C E E",
            "RCE": "R C E",
            "NBA": "N B A",
            "NAAC": "N A A C",
            "EAMCET": "E A M C E T",
            "EAPCET": "E A P C E T",
            "ECET": "E C E T",
            "ICET": "I C E T",
            "PGECET": "P G E C E T",
            "GATE": "gate",
            "IEEE": "I triple E",
            "ISTE": "I S T E",
            "Rs.": "Rupees",
            "Rs": "Rupees",
            "₹": "Rupees",
            "LPA": "lakhs per annum",
            "CTC": "C T C",
            "9AM": "9 A M",
            "5PM": "5 P M",
            "9 AM": "9 A M",
            "5 PM": "5 P M",
        }

        for abbr, spoken in replacements.items():
            text = text.replace(abbr, spoken)

    # ── Step 3: Number conversion (skip phone numbers already handled) ──
    # Only convert numbers that are NOT already digit-grouped phone numbers
    def speak_number(match):
        raw = match.group()
        # Skip if it looks like part of a phone number (already handled)
        num = int(raw.replace(",", ""))
        if num >= 100000:
            lakhs = num / 100000
            if lakhs == int(lakhs):
                return f"{int(lakhs)} lakhs"
            return f"{lakhs:.1f} lakhs"
        elif num >= 1000:
            thousands = num / 1000
            if thousands == int(thousands):
                return f"{int(thousands)} thousand"
            return f"{thousands:.1f} thousand"
        return raw

    # Only match numbers that are clearly NOT phone numbers
    # (numbers preceded by ₹, Rs, "fee", "rank", "salary", or standalone large numbers)
    text = re.sub(r'(?<!\d[\s,])\b\d{1,3}(?:,\d{3})+\b', speak_number, text)
    text = re.sub(r'(?<!\d[\s,])\b\d{4,6}\b(?![\s,]\d)', speak_number, text)

    # ── Step 4: Light pause insertion (safe for all languages) ──
    # Only add a brief pause after sentence-ending periods
    # Do NOT use "..." — it causes stuttering in Sarvam TTS
    # Sarvam naturally handles commas and periods as pauses

    # Clean up extra spaces
    text = re.sub(r'\s+', ' ', text).strip()

    return text

# =========================================================
# SARVAM TTS (text → audio) — ENHANCED for natural speech
# =========================================================
async def tts(text: str, language: str = "en-IN") -> bytes:

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        return b""

    # Humanize the text before sending to TTS (pass language)
    spoken_text = humanize_text(text, language)
    print(f"[TTS] Original:  '{text[:100]}'", flush=True)
    print(f"[TTS] Humanized: '{spoken_text[:100]}'", flush=True)

    url = f"{SARVAM_TTS_URL}?model=bulbul:v2&send_completion_event=true"
    headers = {"Api-Subscription-Key": api_key}

    audio_chunks = []

    print(f"[TTS] Connecting (lang={language})...", flush=True)

    try:
        async with websockets.connect(url, additional_headers=headers) as ws:

            await ws.send(json.dumps({
                "type": "config",
                "data": {
                    "target_language_code": language,
                    "speaker": "anushka",
                    "pitch": 0,
                    "pace": 1.1,
                    "loudness": 1.5,
                    "speech_sample_rate": 22050,
                    "enable_preprocessing": True
                }
            }))

            await ws.send(json.dumps({
                "type": "text",
                "data": {"text": spoken_text}
            }))

            print(f"[TTS] Text sent ({len(spoken_text)} chars)", flush=True)
            await ws.send(json.dumps({"type": "flush"}))

            try:
                while True:
                    response = await asyncio.wait_for(ws.recv(), timeout=TTS_TIMEOUT)
                    data = json.loads(response)

                    if data.get("type") == "audio":
                        b64 = data.get("data", {}).get("audio", "")
                        if b64:
                            audio_chunks.append(base64.b64decode(b64))

                    elif data.get("type") == "event":
                        print("[TTS] Complete", flush=True)
                        break

                    elif data.get("type") == "error":
                        print(f"[TTS] Error: {data.get('data', {}).get('message', 'Unknown')}", flush=True)
                        break

            except asyncio.TimeoutError:
                print(f"[TTS] Timeout after {TTS_TIMEOUT}s", flush=True)

        result = b"".join(audio_chunks)
        print(f"[TTS] Audio: {len(result)} bytes", flush=True)
        return result

    except Exception as e:
        print(f"[TTS] Connection error: {e}", flush=True)
        return b""

# =========================================================
# SARVAM TRANSLATE (any language → any language) via HTTP
# =========================================================
async def translate(text: str, source_lang: str, target_lang: str) -> str:

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        return text

    print(f"[TRANSLATE] {source_lang} → {target_lang}...", flush=True)

    payload = {
        "input": text,
        "source_language_code": source_lang,
        "target_language_code": target_lang,
        "model": "mayura:v1"
    }

    headers = {"Api-Subscription-Key": api_key, "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(SARVAM_TRANSLATE_URL, json=payload, headers=headers) as response:

                if response.status != 200:
                    error_text = await response.text()
                    print(f"[TRANSLATE] Error {response.status}: {error_text}", flush=True)
                    print(f"[TRANSLATE] Falling back to original text in {target_lang}", flush=True)
                    return text

                result = await response.json()
                translated = result.get("translated_text", "").strip()

                if translated:
                    print(f"[TRANSLATE] Done ({len(translated)} chars)", flush=True)
                    return translated
                return text

    except Exception as e:
        print(f"[TRANSLATE] Error: {e}. Falling back to original text.", flush=True)
        return text

# =========================================================
# LLM: CASUAL REPLY
# =========================================================
def generate_casual_reply(user_text: str, mem: list, language: str = "en-IN") -> str:

    history_context = ""
    if mem:
        lines = [f"  {i+1}. {entry}" for i, entry in enumerate(mem)]
        history_context = "Previous Context:\n" + "\n".join(lines)

    language_instruction = ""
    if language == "te-IN":
        language_instruction = "Generate your response in Telugu (తెలుగు)."
    else:
        language_instruction = "Always reply in English."

    prompt = f"""You are a warm, friendly voice receptionist at RCE Ramachandra College of Engineering.
You are on a LIVE PHONE CALL. The caller said something casual like a greeting, thank you, or goodbye.

{language_instruction}

Respond exactly like a real human receptionist would speak on a phone:
- Use natural warm tone with occasional fillers like "Sure!", "Of course!", "Hey there!"
- Keep it to ONE short sentence maximum.
- If they say thank you: "You're welcome! Is there anything else about the college I can help with?"
- If they say bye: "Alright, have a great day! Feel free to call back anytime."
- If they say hello/hi: "Hey there! Welcome to RCEE. How can I help you today?"
- No markdown, no special characters.

{history_context if history_context else "Previous Context:\nNone"}"""

    client = get_openai()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text}
        ],
        temperature=0.6,
        max_tokens=60
    )

    answer = response.choices[0].message.content.strip()

    if len(answer) > 150:
        answer = answer[:150].rsplit(".", 1)[0].strip() + "."

    print(f"[CASUAL] '{user_text}' → '{answer}'", flush=True)
    return answer

# =========================================================
# LLM: WEB SEARCH ANSWER (searches rcee.ac.in only)
# =========================================================
def generate_web_answer(user_query: str, mem: list) -> str:

    history_context = ""
    if mem:
        lines = [f"  {i+1}. {entry}" for i, entry in enumerate(mem)]
        history_context = "Previous Context:\n" + "\n".join(lines)

    # The prompt now acts as the strict commander of the search tool
    prompt = f"""You are a friendly voice assistant for RCE Ramachandra College of Engineering on a phone call.
Your job is to search the web to answer the user's query.

CRITICAL SEARCH INSTRUCTION:
When you use the web search tool, you MUST restrict your search to the college website by appending "site:rcee.ac.in" to your search queries. Ignore all other websites.

{history_context if history_context else "Previous Context:\nNone"}

CRITICAL RULES FOR SPOKEN OUTPUT:
EXTREMELY SHORT: Maximum 1 or 2 short sentences. Do NOT exceed 30 words.
CONVERSATIONAL: Give a high-level summary. Do NOT list multiple events.
NO MARKDOWN, NO URLs, NO BULLET POINTS.

Example good answer: "Recently, the college announced new exam timetables and a workshop on IoT. You can check the website for full details."

If nothing relevant is found, say exactly: "I couldn't find recent updates on that. Please check the college website directly."
"""

    print(f"[WEB] User Query: '{user_query}'", flush=True)

    try:
        client = get_openai()
        response = client.responses.create(
            model=LLM_MODEL, # Ensure this is set to "gpt-4o" or a compatible model
            tools=[{
                "type": "web_search_preview",
                "search_context_size": "high" # Helps prevent hallucinations by reading more of the site
            }],
            instructions=prompt,
            input=user_query # Pass the raw, natural query here
        )

        answer = response.output_text.strip()

        if len(answer) > 300:
            answer = answer[:300].rsplit(".", 1)[0].strip() + "."

        print(f"[WEB] Answer: {len(answer)} chars", flush=True)
        return answer

    except Exception as e:
        print(f"[WEB] Error: {e}", flush=True)
        return "I couldn't search for that right now. You can check the college website directly."
# =========================================================
# LLM: GENERATE OPTIMIZED QUERY (with strong context)
# =========================================================
def generate_query(user_text: str, mem: list) -> str:

    history_context = ""
    if mem:
        lines = [f"  {i+1}. {entry}" for i, entry in enumerate(mem)]
        history_context = "\n".join(lines)

    prompt = f"""You are an RCEE database query optimizer with advanced context reasoning.
Your job: Convert user queries into precise search keywords, with expert context resolution.

STEP 1: ANALYZE PREVIOUS CONTEXT (CRITICAL)
Previous context from conversation:
{history_context if history_context else "None — this is the first question."}

KEY PRINCIPLE: When user says "he", "she", "that person", "him", "her", "the same", etc.,
IMMEDIATELY resolve to the actual person/entity from context.

STEP 2: RESOLVE PRONOUNS & REFERENCES
CRITICAL EXAMPLES:

Example 1: User previously asked about CSE fees (1.2 lakhs)
  New question: "What about hostel fees for the same?"
  → "the same" = CSE branch
  → Output: "CSE hostel fees accommodation charges"

Example 2: Context mentions "CSE HOD: Dr G Chamundeswari"
  New question: "What is his phone number?"
  → "his" = Dr G Chamundeswari (CSE HOD)
  → Output: "Dr G Chamundeswari CSE HOD phone contact"

Example 3: Context mentions "Principal: Dr. K. Subba Rao"
  New question: "Who is the principal?"
  → Clear pronoun: principal
  → Output: "Principal Dr K Subba Rao contact"

STEP 3: GENERATE KEYWORDS

OUTPUT RULES:
- Keywords ONLY. No sentences. No JSON. No quotes. No explanations.
- After resolving pronouns, use the RESOLVED name in keywords.
- Include department/role when mentioning a person.
- Never put numbers in output (numbers confuse search).
- Be specific: "CSE branch" not just "CSE", "HOD contact" not just "contact"

ABBREVIATION MAPPING (use these expansions):
- CSE → CSE Computer Science | ECE → ECE Electronics Communication
- EEE → EEE Electrical Electronics | AIML → AIML Artificial Intelligence Machine Learning
- AI&DS → AI&DS Data Science | HOD → HOD head department | M.Tech → M.Tech postgraduate
- EAPCET → AP EAPCET entrance exam | ECET → AP ECET lateral entry
- MBA → MBA business | Mech → Mechanical Engineering | Civil → Civil Engineering

SYNONYM EXPANSION (think laterally):
- Salary/package → CTC LPA lakhs | Placements → recruiters companies jobs
- Fees → tuition hostel mess charges | Cutoff/rank → eligibility last rank
- Bus → transport route | Scholarship → waiver reimbursement
- Contact → phone email office address | Infrastructure → labs facilities

NOW PROCESS THE USER QUERY:
User said: "{user_text}"

Think through:
1. What entity is the user asking about? (who, what branch, what topic)
2. Are there pronouns (he, she, his, her, it, that, the same)?
3. Does context resolve these pronouns?
4. What are the key search terms after resolution?

Output ONLY the final keywords (no thinking process shown):"""

    client = get_openai()
    response = client.chat.completions.create(
        model=LLM_REASONING_MODEL,  # Use GPT-4 for better reasoning
        messages=[
            {"role": "system", "content": "You are a query optimization expert. Output ONLY clean search keywords, nothing else."},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        max_tokens=200
    )

    result = response.choices[0].message.content.strip()
    print(f"[QUERY] Input: '{user_text}'", flush=True)
    print(f"[QUERY] Context: '{history_context[:80] if history_context else 'None'}'...", flush=True)
    print(f"[QUERY] Output: '{result}'", flush=True)
    return result

# =========================================================
# LLM: GENERATE ANSWER
# =========================================================
def generate_answer(user_query: str, chunks: list, mem: list, target_language: str = "en-IN") -> str:

    history_context = ""
    if mem:
        lines = [f"  {i+1}. {entry}" for i, entry in enumerate(mem)]
        history_context = "Previous conversation context:\n" + "\n".join(lines)

    chunks_text = "\n\n".join(chunks) if chunks else "No results found."

    # Adjust language instruction
    language_instruction = ""
    if target_language == "te-IN":
        language_instruction = "\nIMPORTANT: Generate your response in Telugu (తెలుగు). Translate all information into natural Telugu that a native speaker would understand."
    else:
        language_instruction = "\nIMPORTANT: Generate your response in English. No translation needed."

    prompt = f"""Role: Official Voice Call Assistant for RCE Ramachandra College of Engineering (Autonomous).
You are speaking to a caller on a LIVE PHONE CALL. Generate responses as NATURAL SPOKEN SENTENCES — 
exactly how a helpful human receptionist would speak on a phone. No written formatting whatsoever.

Task: Answer the user's query using ONLY the DB Results below.
If the user refers to someone or something from previous context (like "his", "that", "the same"), 
use the previous context to understand what they mean.

{language_instruction}

{history_context if history_context else "Previous conversation context:\nNone"}

DB Results:
{chunks_text}

VOICE CALL STYLE RULES (HIGHEST PRIORITY)
- You are on a PHONE CALL. Speak like a warm, helpful human receptionist.
- Keep responses SHORT — maximum 2 to 3 spoken sentences.
- Use natural conversational fillers occasionally: "So", "Well", "Actually", "Sure".
- Add natural pauses with commas where a human would breathe.
- For longer info like fees or steps, give KEY highlights and say "Would you like me to go into more detail?"
- NEVER use markdown, bullet points, numbered lists, URLs, asterisks, dashes, or any written formatting.
- NEVER say "according to the data", "based on the results", or "as per the database".

PHONE NUMBER RULES (CRITICAL):
- ALWAYS speak phone numbers as grouped digits, NEVER convert to words like "thousand".
- Format: "9 4 9 2 9, 3 6 2 2 2" or "94929, 36222" — NOT "9 thousand" or "94.9 thousand".
- Example: The number is 94929, 36222. Say it exactly like that.

NUMBER RULES:
- For fees/money: say "around one lakh twenty thousand" or "about 1.2 lakhs".
- For ranks: say "around 35 thousand" or "about 4 thousand".
- For phone numbers: NEVER convert — speak digits as-is with natural grouping.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GENERAL ANSWER RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Be concise, professional, and friendly. Use NO external knowledge.
2. Think from a student's or parent's perspective — they may ask in broken English, Telugu-English mix, or use slang. Interpret the INTENT behind the query, not the literal words.
3. If the query is unclear but DB Results have related info, answer with what you have.
4. If DB Results contain ANY information related to the people, topics, or entities in the query — even partially — use it. Never refuse when relevant data exists.
5. ONLY if DB Results have absolutely NO relevant information, say:
   "I don't have that specific information right now. You can contact the college helpdesk at 94929, 36222, between 9 AM and 5 PM, or email helpdesk at rcee dot ac dot in."
   Then give a brief helpful suggestion based on what you do know — no hallucinations.
6. Do not mention database, results, data sources, or chunks. Answer as if you naturally know it.
7. Don't answer queries not related to RCEE. Politely say you only help with RCEE queries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RANK & ELIGIBILITY RULES (CRITICAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
These apply to EAMCET, EAPCET (first year B Tech) and AP ECET (lateral entry second year B Tech).

- Lower rank number means better rank. Rank 1 is the best.
- "Last Rank" means closing rank or cutoff. Compare student rank against Last Rank ONLY.
- "First Rank" means topper's rank. NEVER use it for eligibility.
- ELIGIBLE if student's rank is less than or equal to Last Rank.
- NOT ELIGIBLE if student's rank is greater than Last Rank.

Speak eligibility naturally:
"With your rank of 35 thousand, you're eligible for Mechanical since the cutoff was about one lakh seventy seven thousand. But CSE AIML might be difficult, the cutoff was around 4 thousand."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPECIFIC QUERY TYPES (adapt for voice)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- BUS: Mention bus numbers naturally. "Bus number 5 and 12 cover that route."
- FEES: Speak amounts naturally. "It's about one lakh fifty thousand total, including tuition, hostel, and mess."
- CONTACTS: Speak name, role, and phone number with digit grouping. "You can reach Doctor Sharma, the HOD of CSE, at 98765, 43210."
- ADMISSION STEPS: Summarize briefly. "There are about 5 steps, starting with online registration, then document verification. Want me to walk you through each one?"
"""

    client = get_openai()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_query}
        ],
        temperature=0.4,
        max_tokens=200
    )

    answer = response.choices[0].message.content.strip()

    if len(answer) > 400:
        answer = answer[:400].rsplit(".", 1)[0].strip() + "."

    print(f"[ANSWER] {len(answer)} chars", flush=True)
    return answer

# =========================================================
# LLM: COMPRESS TURN FOR MEMORY
# =========================================================
def compress_turn(query: str, answer: str) -> str:

    client = get_openai()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": """Compress this Q&A into one short line for context memory.
Format: "topic/person → key facts"
Max 20 words. Include names, numbers, and key details.
Examples:
- "HR: Dr. B. Prasad Babu, phone 9492923222"
- "CSE fees → 1.2 lakhs/year tuition + hostel"
- "Principal: Dr. K. Subba Rao, email principal@rcee.ac.in"
Keep entity names intact — they are needed for follow-up questions."""},
            {"role": "user", "content": f"Q: {query}\nA: {answer}"}
        ],
        temperature=0,
        max_tokens=50
    )

    compressed = response.choices[0].message.content.strip()
    print(f"[MEMORY] {compressed}", flush=True)
    return compressed

# =========================================================
# MAIN PROCESS FUNCTION (with cancellation support)
# =========================================================
class CallCancelled(Exception):
    """Raised when user ends the call mid-processing."""
    pass

def _check_cancelled(session_id: str):
    """Raise CallCancelled if this session was ended by the user."""
    if is_session_cancelled(session_id):
        print(f"[CANCELLED] Session {session_id} — stopping pipeline", flush=True)
        raise CallCancelled(f"Session {session_id} cancelled by user")

async def process_voice(audio_bytes: bytes, search_fn, session_id: str = "default") -> bytes:

    mem = get_memory(session_id)

    # Clear any previous cancellation for this session (fresh request)
    clear_cancellation(session_id)

    try:
        # ── Step 1: STT ──
        print(f"\n[STEP 1] Speech to Text | Session: {session_id}", flush=True)
        _check_cancelled(session_id)
        stt_result = await stt(audio_bytes)
        _check_cancelled(session_id)

        transcript = stt_result.get("transcript", "").strip()
        language = stt_result.get("language", "en-IN")

        if not transcript:
            print("[CALL] Empty transcript", flush=True)
            _check_cancelled(session_id)
            error_msg = "Sorry, I didn't catch that."
            if language != "en-IN":
                error_msg = await translate(error_msg, "en-IN", language)
            _check_cancelled(session_id)
            return await tts(error_msg, language)

        # ── Step 1.5: Translate to English if needed ──
        english_text = transcript
        if language != "en-IN":
            print(f"\n[STEP 1.5] Translating {language} → English", flush=True)
            _check_cancelled(session_id)
            english_text = await translate(transcript, language, "en-IN")
            _check_cancelled(session_id)

        # ── Step 2: Casual reply ──
        if is_casual_message(english_text):
            print("\n[STEP 2] Casual message — replying directly", flush=True)
            _check_cancelled(session_id)
            answer = generate_casual_reply(english_text, mem, language)
            _check_cancelled(session_id)
            audio = await tts(answer, language)
            _check_cancelled(session_id)
            return audio

        # ── Step 3: Web search ──
        if needs_web_search(english_text):
            print("\n[STEP 3] Web search — searching rcee.ac.in", flush=True)
            _check_cancelled(session_id)
            answer = generate_web_answer(english_text, mem)
            _check_cancelled(session_id)

            if language != "en-IN":
                answer = await translate(answer, "en-IN", language)
                _check_cancelled(session_id)

            audio = await tts(answer, language)
            _check_cancelled(session_id)

            try:
                compressed = compress_turn(english_text, answer)
                mem.append(compressed)
                if len(mem) > MAX_MEMORY:
                    sessions[session_id] = mem[-MAX_MEMORY:]
                print(f"[MEMORY] Session {session_id}: {len(mem)} turns", flush=True)
            except Exception as e:
                print(f"[MEMORY] Failed: {e}", flush=True)

            return audio

        # ── Step 4: Query generation ──
        print("\n[STEP 4] Query Generation", flush=True)
        _check_cancelled(session_id)
        db_query = generate_query(english_text, mem)
        _check_cancelled(session_id)

        if not db_query.strip():
            print("[CALL] Empty query — sending to answer generator", flush=True)
            _check_cancelled(session_id)
            answer = generate_answer(english_text, [], mem, language)
            _check_cancelled(session_id)
            audio = await tts(answer, language)
            _check_cancelled(session_id)
            return audio

        # ── Step 4.5: Check if answer is already in context ──
        print("\n[STEP 4.5] Checking previous context for answer", flush=True)
        _check_cancelled(session_id)
        found_in_context, context_answer = await check_answer_in_context(english_text, mem)
        _check_cancelled(session_id)
        
        if found_in_context and context_answer:
            print(f"[CONTEXT_HIT] Found answer in memory - skipping DB search", flush=True)
            answer = context_answer
            
            # Translate to user's language if needed
            if language != "en-IN":
                print(f"\n[STEP 5.5] Translating context answer → {language}", flush=True)
                _check_cancelled(session_id)
                answer = await translate(answer, "en-IN", language)
                _check_cancelled(session_id)
            
            # TTS and return
            print("\n[STEP 6] Text to Speech (from context)", flush=True)
            _check_cancelled(session_id)
            audio = await tts(answer, language)
            _check_cancelled(session_id)
            
            # Save to memory
            try:
                compressed = compress_turn(english_text, context_answer)
                mem.append(compressed)
                if len(mem) > MAX_MEMORY:
                    sessions[session_id] = mem[-MAX_MEMORY:]
                print(f"[MEMORY] Session {session_id}: {len(mem)} turns", flush=True)
            except Exception as e:
                print(f"[MEMORY] Failed: {e}", flush=True)
            
            return audio

        # ── Step 5: DB search ──
        print("\n[STEP 5] DB Search", flush=True)
        _check_cancelled(session_id)
        chunks = search_fn(db_query)
        _check_cancelled(session_id)

        # ── Step 6: Answer generation (in user's language) ──
        print("\n[STEP 6] Answer Generation", flush=True)
        _check_cancelled(session_id)
        answer = generate_answer(english_text, chunks, mem, language)
        _check_cancelled(session_id)

        # ── Step 6.5: Translate answer if needed ──
        if language != "en-IN":
            print(f"\n[STEP 6.5] Translating answer → {language}", flush=True)
            _check_cancelled(session_id)
            answer = await translate(answer, "en-IN", language)
            _check_cancelled(session_id)

        # ── Step 7: TTS ──
        print("\n[STEP 7] Text to Speech", flush=True)
        _check_cancelled(session_id)
        audio = await tts(answer, language)
        _check_cancelled(session_id)

        # ── Step 8: Save memory ──
        if chunks:
            print("\n[STEP 8] Saving to memory", flush=True)
            try:
                compressed = compress_turn(english_text, answer)
                mem.append(compressed)
                if len(mem) > MAX_MEMORY:
                    sessions[session_id] = mem[-MAX_MEMORY:]
                print(f"[MEMORY] Session {session_id}: {len(mem)} turns", flush=True)
            except Exception as e:
                print(f"[MEMORY] Failed: {e}", flush=True)
        else:
            print("\n[STEP 8] No chunks — skipping memory", flush=True)

        return audio

    except CallCancelled:
        print(f"[PIPELINE] Stopped — session {session_id} cancelled", flush=True)
        clear_cancellation(session_id)
        return b""

# =========================================================
# WARMUP & STATUS
# =========================================================
_ready = False

def warmup():
    global _ready

    print("[VOICE] Warming up...", flush=True)

    try:
        client = get_openai()
        client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1
        )
        print("[VOICE] OpenAI client ready", flush=True)

        if os.getenv("SARVAM_API_KEY"):
            print("[VOICE] Sarvam API key found", flush=True)
        else:
            print("[VOICE] WARNING: SARVAM_API_KEY missing", flush=True)

        _ready = True
        print("[VOICE] Warmup complete", flush=True)

    except Exception as e:
        print(f"[VOICE] Warmup failed: {e}", flush=True)
        _ready = False

def is_ready() -> bool:
    return _ready

# =========================================================
# END SESSION (with cancellation)
# =========================================================
def end_session(session_id: str = "default"):
    cancel_session(session_id)

    if session_id in sessions:
        turns = len(sessions[session_id])
        del sessions[session_id]
        print(f"[SESSION] {session_id} ended. {turns} turns cleared.", flush=True)
    else:
        print(f"[SESSION] {session_id} not found.", flush=True)