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
            async with session.post(
                SARVAM_STT_HTTP_URL, data=data, headers=headers
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    print(f"[STT] HTTP Error {response.status}: {error_text}", flush=True)
                    return {"transcript": "", "language": "en-IN", "error": error_text}

                result = await response.json()
                transcript = result.get("transcript", "").strip()
                language   = result.get("language_code", "") or detect_language(transcript)

                print(f"[STT] Transcript: '{transcript}'", flush=True)
                print(f"[STT] Language  : {language}", flush=True)

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
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'[*_#`]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # ── ENGLISH-ONLY PREPROCESSING ──
    text = re.sub(r'[*_#`]', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # ── Step 1: Protect phone numbers FIRST ──
    def phone_to_digits(match):
        digits = re.sub(r'[^0-9]', '', match.group())
        if 7 <= len(digits) <= 12:
            if len(digits) == 10:
                return f"{digits[:5]}, {digits[5:]}"
            elif len(digits) == 11:
                return f"{digits[:3]}, {digits[3:7]}, {digits[7:]}"
            elif len(digits) == 12:
                return f"{digits[:2]}, {digits[2:7]}, {digits[7:]}"
            else:
                return ' '.join(digits)
        return match.group()

    text = re.sub(r'\+?91[-\s]?\d{5}[-\s,]?\d{5}', phone_to_digits, text)
    text = re.sub(r'\b\d{10}\b', phone_to_digits, text)
    text = re.sub(r'\b\d{5}[-\s,]\s?\d{5}\b', phone_to_digits, text)
    text = text.replace("+91-", "").replace("+91 ", "").replace("+91", "")

    # ── Step 2: Abbreviation expansion ──
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

    # ── Step 3: Number conversion ──
    def speak_number(match):
        raw = match.group()
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

    text = re.sub(r'(?<!\d[\s,])\b\d{1,3}(?:,\d{3})+\b', speak_number, text)
    text = re.sub(r'(?<!\d[\s,])\b\d{4,6}\b(?![\s,]\d)', speak_number, text)

    # ── Step 4: Clean up extra spaces ──
    text = re.sub(r'\s+', ' ', text).strip()

    return text

# =========================================================
# SARVAM TTS (text → audio)
# =========================================================
async def tts(text: str, language: str = "en-IN") -> bytes:

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        return b""

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
                        print(
                            f"[TTS] Error: {data.get('data', {}).get('message', 'Unknown')}",
                            flush=True
                        )
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
            async with session.post(
                SARVAM_TRANSLATE_URL, json=payload, headers=headers
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    print(f"[TRANSLATE] Error {response.status}: {error_text}", flush=True)
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
# CALL CONVERSATION HISTORY (for follow-up context)
# =========================================================
# Each entry: {"role": "user"|"assistant", "text": str}
# We store the ENGLISH version of both user queries and assistant answers
# so that generate_query() and generate_answer() always work in English.
# =========================================================
_call_history: list[dict] = []
MAX_CALL_HISTORY = 10   # keep last 10 turns (5 user + 5 assistant pairs)

def get_call_history_text() -> str:
    """Return formatted conversation history (always in English)."""
    if not _call_history:
        return ""
    return "\n".join(
        f"{'Student' if h['role'] == 'user' else 'Assistant'}: {h['text']}"
        for h in _call_history[-MAX_CALL_HISTORY:]
    )

def add_to_call_history(role: str, text: str):
    """
    Add a turn to history.
    ALWAYS store English text here — translations happen only at TTS time.
    """
    global _call_history
    _call_history.append({"role": role, "text": text.strip()})
    if len(_call_history) > MAX_CALL_HISTORY:
        _call_history = _call_history[-MAX_CALL_HISTORY:]
    print(f"[HISTORY] Added ({role}): '{text[:80]}'", flush=True)

def clear_call_history():
    global _call_history
    _call_history = []
    print("[HISTORY] Cleared", flush=True)

# =========================================================
# CANCELLATION TRACKING
# =========================================================
_cancelled = False

def cancel_call():
    global _cancelled
    _cancelled = True
    print("[CANCEL] Call marked for cancellation", flush=True)

def is_call_cancelled() -> bool:
    return _cancelled

def clear_cancellation():
    global _cancelled
    _cancelled = False

class CallCancelled(Exception):
    """Raised when user ends the call mid-processing."""
    pass

def _check_cancelled():
    if is_call_cancelled():
        print("[CANCELLED] Stopping pipeline", flush=True)
        raise CallCancelled("Call cancelled by user")

# =========================================================
# LLM: INTENT DETECTION
# =========================================================
def detect_intent(user_text: str, history_text: str) -> str:
    """
    Classify the user's intent so we route correctly.
    Returns one of: CASUAL | WEB_SEARCH | DB_QUERY
    
    This runs AFTER we already know the English text, so all
    context (pronouns, follow-ups) is resolved before routing.
    """
    history_block = ""
    if history_text:
        history_block = f"""
CONVERSATION SO FAR:
{history_text}

The current message may be a follow-up to the above conversation.
Even if the message is short or uses pronouns, classify based on the FULL context.
"""

    prompt = f"""You are an intent classifier for a college voice assistant.

{history_block}

CURRENT MESSAGE: "{user_text}"

Classify the intent as exactly ONE of:
- CASUAL     : greetings, thanks, bye, yes/no, very short social phrases (ignore context for these)
- WEB_SEARCH : asks about latest news, recent events, current updates, announcements, today's info
- DB_QUERY   : any question about the college — admissions, fees, courses, faculty, placements,
               ranks, hostel, bus, contact, eligibility, scholarships, departments, etc.
               This includes follow-up questions that reference previous topics via pronouns.

RULES:
1. If the message is a clear greeting/farewell (hello, hi, bye, thanks) → CASUAL
2. If the message asks about something recent/latest/current → WEB_SEARCH
3. Everything else about the college → DB_QUERY
4. Short follow-ups like "what about fees?", "and his number?", "tell me more" → DB_QUERY
5. Reply with ONLY the label: CASUAL, WEB_SEARCH, or DB_QUERY"""

    client = get_openai()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "You are an intent classifier. Reply with ONLY: CASUAL, WEB_SEARCH, or DB_QUERY"},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        max_tokens=10
    )
    intent = response.choices[0].message.content.strip().upper()
    # Sanitize — make sure we only return valid labels
    if intent not in ("CASUAL", "WEB_SEARCH", "DB_QUERY"):
        intent = "DB_QUERY"

    print(f"[INTENT] '{user_text[:60]}' → {intent}", flush=True)
    return intent

# =========================================================
# LLM: CASUAL REPLY
# =========================================================
def generate_casual_reply(user_text: str, language: str = "en-IN") -> str:

    language_instruction = (
        "Generate your response in Telugu (తెలుగు)."
        if language == "te-IN"
        else "Always reply in English."
    )

    prompt = f"""You are a warm, friendly voice receptionist at RCE Ramachandra College of Engineering.
You are on a LIVE PHONE CALL. The caller said something casual.

{language_instruction}

Respond exactly like a real human receptionist on a phone:
- Natural warm tone with occasional fillers like "Sure!", "Of course!", "Hey there!"
- ONE short sentence maximum.
- If they say thank you → "You're welcome! Is there anything else about the college I can help with?"
- If they say bye → "Alright, have a great day! Feel free to call back anytime."
- If they say hello/hi → "Hey there! Welcome to RCEE. How can I help you today?"
- No markdown, no special characters."""

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
# LLM: WEB SEARCH ANSWER
# =========================================================
def generate_web_answer(user_query: str, history_text: str = "") -> str:

    history_block = ""
    if history_text:
        history_block = f"""
CONVERSATION CONTEXT:
{history_text}
Use this context to understand what "it", "that", "this" refers to in the query.
"""

    prompt = f"""You are a friendly voice assistant for RCE Ramachandra College of Engineering on a phone call.
{history_block}
Your job is to search the web to answer the user's query.

CRITICAL SEARCH INSTRUCTION:
When you use the web search tool, restrict your search to the college website by appending "site:rcee.ac.in".

CRITICAL RULES FOR SPOKEN OUTPUT:
- EXTREMELY SHORT: Maximum 1 or 2 short sentences. Do NOT exceed 30 words.
- CONVERSATIONAL: High-level summary only. Do NOT list multiple events.
- NO MARKDOWN, NO URLs, NO BULLET POINTS.

Example: "Recently, the college announced new exam timetables and a workshop on IoT. Check the website for full details."

If nothing found: "I couldn't find recent updates on that. Please check the college website directly."
"""

    print(f"[WEB] Query: '{user_query}'", flush=True)

    try:
        client = get_openai()
        response = client.responses.create(
            model=LLM_MODEL,
            tools=[{"type": "web_search_preview", "search_context_size": "high"}],
            instructions=prompt,
            input=user_query
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
# LLM: GENERATE OPTIMIZED DB QUERY
# =========================================================
def generate_query(user_text: str, history_text: str) -> str:
    """
    Convert user query + conversation history into precise DB search keywords.
    History is passed explicitly so it reflects the state BEFORE this turn is added.
    """

    context_block = ""
    if history_text:
        print(f"[QUERY] Using history:\n{history_text}", flush=True)
        context_block = f"""
RECENT CONVERSATION (last {MAX_CALL_HISTORY} turns):
{history_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXT RESOLUTION — CRITICAL RULES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The current query may be a follow-up. It may:
  • Use pronouns  : "his", "her", "that", "it", "this", "those", "them", "there"
  • Be incomplete : "what about fees?", "and the rank?", "tell me more", "how about hostel?"
  • Reference same topic without repeating it

In ALL follow-up cases, you MUST inject the missing subject/topic/branch from the conversation.

EXAMPLES:
  History: "Student: CSE HOD contact  Assistant: Dr. XYZ is HOD of CSE"
  Current: "what is his phone number?"
  → Keywords: CSE HOD phone contact number

  History: "Student: ECE fees  Assistant: ECE tuition fee is 1.2 lakhs"
  Current: "what about hostel?"
  → Keywords: ECE hostel fees accommodation

  History: "Student: placement package CSE  Assistant: average package is 4 LPA"
  Current: "what about ECE?"
  → Keywords: ECE placement package salary

Only IGNORE context if the user clearly changes to a completely unrelated topic.
"""
    else:
        print("[QUERY] No history — treating as fresh query", flush=True)

    prompt = f"""You are a database query optimizer for RCEE college information system.
Convert the user query into precise search keywords for the college database.
{context_block}
CURRENT QUERY: "{user_text}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT RULES (STRICT):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Output ONLY keywords. No sentences, no JSON, no quotes, no explanations, no punctuation.
- 2 to 6 meaningful keywords only.
- Remove: RCEE, RCE, college, engineering, tell, me, what, is, are, the, a, an, please
- Never output single letters or initials (no "Dr. M." → use full surname + role)
- For person queries: full surname + role (example: "Prasad principal contact")
- For follow-ups: ALWAYS include the topic/branch/subject from conversation history

BRANCH MAPPING:
  cse → CSE Computer Science
  ece → ECE Electronics Communication
  eee → EEE Electrical Electronics
  aiml → AIML Artificial Intelligence Machine Learning
  aids → AI&DS Data Science
  mech → Mechanical Engineering
  civil → Civil Engineering
  mba → MBA Business Administration
  mtech → M.Tech

SYNONYM EXPANSION (use relevant synonyms):
  salary/pay → package CTC LPA
  placements → recruiters companies jobs hiring
  fees/cost → tuition hostel mess fee
  cutoff/rank/eligibility → closing last rank EAMCET EAPCET
  bus/transport → route bus number
  scholarship → waiver reimbursement
  hod/head → HOD head department
  contact/phone/number → contact phone email

Output ONLY the final keywords on one line:"""

    client = get_openai()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a query optimization expert. "
                    "Output ONLY clean search keywords on one line, nothing else."
                )
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        max_tokens=60
    )

    result = response.choices[0].message.content.strip()
    # Safety: remove any accidental quotes or newlines
    result = result.replace('"', '').replace("'", '').replace('\n', ' ').strip()

    print(f"[QUERY] Input  : '{user_text}'", flush=True)
    print(f"[QUERY] Output : '{result}'", flush=True)
    return result

# =========================================================
# LLM: GENERATE ANSWER
# =========================================================
def generate_answer(
    user_query: str,
    chunks: list,
    history_text: str,
    target_language: str = "en-IN"
) -> str:
    """
    Generate a spoken answer in English.
    Translation (if needed) happens AFTER this, in the pipeline.
    history_text is the conversation so far (in English).
    """

    chunks_text = "\n\n".join(chunks) if chunks else "No results found."

    context_block = ""
    if history_text:
        context_block = f"""
CONVERSATION SO FAR (English, use for context — avoid repeating already-given info):
{history_text}
"""

    prompt = f"""Role: Official Voice Call Assistant for RCE Ramachandra College of Engineering (Autonomous).
You are on a LIVE PHONE CALL. Speak like a warm, helpful human receptionist.
Generate responses as NATURAL SPOKEN SENTENCES — no written formatting whatsoever.

Task: Answer the user's current query using ONLY the DB Results below.
{context_block}
DB Results:
{chunks_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VOICE CALL STYLE (HIGHEST PRIORITY):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- SHORT: Maximum 2 to 3 spoken sentences.
- NATURAL: Use "So", "Well", "Actually", "Sure" occasionally.
- Add commas where a human would pause/breathe.
- For complex info (fees, steps): give KEY highlights then ask "Would you like more details?"
- NEVER use: markdown, bullet points, numbered lists, URLs, asterisks, dashes, or any symbol.
- NEVER say: "according to the data", "based on the results", "as per the database".
- ALWAYS answer in ENGLISH (translation is handled separately).

PHONE NUMBER RULES (CRITICAL):
- Speak phone numbers as grouped digits ONLY.
- Format: "94929, 36222" — NEVER "9 thousand" or "ninety-four thousand".

NUMBER RULES:
- Fees/money   : "around one lakh twenty thousand" or "about 1.2 lakhs"
- Ranks        : "around 35 thousand" or "about 4 thousand"
- Phone numbers: NEVER convert — speak digits as-is with natural grouping.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANSWER RULES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Be concise, professional, and friendly. Use NO external knowledge.
2. Think from a student's or parent's perspective.
3. Use DB Results even if only partially relevant.
4. ONLY if DB Results have absolutely NO relevant info, say:
   "I don't have that specific information right now. You can contact the college helpdesk
    at 94929, 36222, between 9 AM and 5 PM, or email helpdesk at rcee dot ac dot in."
5. Do not reveal database, results, or data sources. Answer as if you naturally know it.
6. Don't answer queries unrelated to RCEE. Politely say you only help with RCEE queries.
7. If this is a follow-up question, directly answer the new sub-topic — don't repeat
   information already given in the conversation history.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RANK & ELIGIBILITY RULES (CRITICAL):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Lower rank number = better rank. Rank 1 is the best.
- "Last Rank" = closing rank / cutoff. Use ONLY this for eligibility.
- "First Rank" = topper's rank. NEVER use for eligibility checks.
- ELIGIBLE   : student rank ≤ Last Rank
- NOT ELIGIBLE: student rank > Last Rank

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPECIFIC QUERY HANDLING:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- BUS      : Mention bus numbers naturally in speech.
- FEES     : Speak amounts naturally ("about one lakh" etc.).
- CONTACTS : Speak name, role, then phone number with digit grouping.
- ADMISSION: Summarize key steps briefly.
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

    print(f"[ANSWER] {len(answer)} chars: '{answer[:100]}'", flush=True)
    return answer

# =========================================================
# MAIN PROCESS FUNCTION
# =========================================================
async def process_voice(audio_bytes: bytes, search_fn) -> bytes:
    """
    Full pipeline:
      STT → (translate to English) → intent detection →
      route (casual / web / db) → generate answer (English) →
      add to history → (translate answer if needed) → TTS → return audio
    
    KEY DESIGN DECISIONS:
    1. History is read BEFORE adding current query (so query gen sees prior turns only)
    2. History is stored in ENGLISH always
    3. Intent detection uses LLM (not keyword matching) so it understands follow-ups
    4. generate_query receives history explicitly, not via global read
    5. generate_answer receives history explicitly for context-aware answers
    """

    clear_cancellation()

    try:
        # ── Step 1: STT ──────────────────────────────────────────────────────
        print(f"\n{'='*60}", flush=True)
        print("[STEP 1] Speech to Text", flush=True)
        _check_cancelled()

        stt_result  = await stt(audio_bytes)
        _check_cancelled()

        transcript = stt_result.get("transcript", "").strip()
        language   = stt_result.get("language", "en-IN")

        if not transcript:
            print("[CALL] Empty transcript", flush=True)
            error_msg = "Sorry, I didn't catch that. Could you please repeat?"
            _check_cancelled()
            if language != "en-IN":
                error_msg = await translate(error_msg, "en-IN", language)
                _check_cancelled()
            return await tts(error_msg, language)

        # ── Step 2: Translate to English if needed ───────────────────────────
        english_text = transcript
        if language != "en-IN":
            print(f"\n[STEP 2] Translate {language} → English", flush=True)
            _check_cancelled()
            english_text = await translate(transcript, language, "en-IN")
            _check_cancelled()
            print(f"[STEP 2] English: '{english_text}'", flush=True)
        else:
            print(f"\n[STEP 2] Language is English — no translation needed", flush=True)

        # ── Step 3: Read history BEFORE this turn ────────────────────────────
        # IMPORTANT: We capture history here so generate_query and generate_answer
        # see ONLY the PREVIOUS turns, not the current one.
        print(f"\n[STEP 3] Reading conversation history", flush=True)
        history_text = get_call_history_text()
        if history_text:
            print(f"[STEP 3] History:\n{history_text}", flush=True)
        else:
            print("[STEP 3] No prior history", flush=True)

        # ── Step 4: Intent Detection (LLM-based, context-aware) ──────────────
        print(f"\n[STEP 4] Intent Detection", flush=True)
        _check_cancelled()
        intent = detect_intent(english_text, history_text)
        _check_cancelled()

        # ── Step 5: Route by Intent ───────────────────────────────────────────
        print(f"\n[STEP 5] Routing → {intent}", flush=True)

        # ── CASUAL ────────────────────────────────────────────────────────────
        if intent == "CASUAL":
            print("[ROUTE] Casual message", flush=True)
            _check_cancelled()
            answer_en = generate_casual_reply(english_text, language)
            _check_cancelled()

            # Add both turns to history (English)
            add_to_call_history("user", english_text)
            add_to_call_history("assistant", answer_en)

            # Translate answer if needed
            final_answer = answer_en
            if language != "en-IN":
                _check_cancelled()
                final_answer = await translate(answer_en, "en-IN", language)
                _check_cancelled()

            audio = await tts(final_answer, language)
            _check_cancelled()
            return audio

        # ── WEB SEARCH ────────────────────────────────────────────────────────
        if intent == "WEB_SEARCH":
            print("[ROUTE] Web search", flush=True)
            _check_cancelled()
            # Pass history so web search understands pronouns/follow-ups
            answer_en = generate_web_answer(english_text, history_text)
            _check_cancelled()

            # Add both turns to history (English)
            add_to_call_history("user", english_text)
            add_to_call_history("assistant", answer_en)

            # Translate answer if needed
            final_answer = answer_en
            if language != "en-IN":
                _check_cancelled()
                final_answer = await translate(answer_en, "en-IN", language)
                _check_cancelled()

            audio = await tts(final_answer, language)
            _check_cancelled()
            return audio

        # ── DB QUERY ──────────────────────────────────────────────────────────
        print("[ROUTE] DB query", flush=True)

        # Step 5a: Generate optimized DB query keywords
        # Pass history_text explicitly (captured BEFORE this turn)
        print(f"\n[STEP 5a] Query Generation", flush=True)
        _check_cancelled()
        db_query = generate_query(english_text, history_text)
        _check_cancelled()

        if not db_query.strip():
            print("[CALL] Empty query generated — using raw English text", flush=True)
            db_query = english_text

        # Step 5b: Search the database
        print(f"\n[STEP 5b] DB Search: '{db_query}'", flush=True)
        _check_cancelled()
        chunks = search_fn(db_query)
        _check_cancelled()
        print(f"[STEP 5b] Got {len(chunks)} chunk(s)", flush=True)

        # Step 5c: Generate answer in English
        print(f"\n[STEP 5c] Answer Generation", flush=True)
        _check_cancelled()
        # Pass history_text so answer avoids repeating prior info
        answer_en = generate_answer(english_text, chunks, history_text, language)
        _check_cancelled()

        # Add both turns to history (always in English)
        add_to_call_history("user", english_text)
        add_to_call_history("assistant", answer_en)

        # Step 5d: Translate answer if caller is using a non-English language
        final_answer = answer_en
        if language != "en-IN":
            print(f"\n[STEP 5d] Translating answer → {language}", flush=True)
            _check_cancelled()
            final_answer = await translate(answer_en, "en-IN", language)
            _check_cancelled()

        # Step 5e: TTS
        print(f"\n[STEP 5e] Text to Speech", flush=True)
        _check_cancelled()
        audio = await tts(final_answer, language)
        _check_cancelled()

        print(f"[PIPELINE] Complete ✓", flush=True)
        return audio

    except CallCancelled:
        print("[PIPELINE] Stopped — call cancelled", flush=True)
        clear_cancellation()
        return b""

    except Exception as e:
        print(f"[PIPELINE] Unexpected error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        # Attempt to return a graceful error message
        try:
            error_msg = "Sorry, I ran into a technical issue. Please try again."
            return await tts(error_msg, "en-IN")
        except Exception:
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
# END SESSION
# =========================================================
def end_session(session_id: str = "default"):
    cancel_call()
    clear_call_history()
    print(f"[SESSION] Ended. History cleared.", flush=True)