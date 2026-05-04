import sys
import os
import time
import re
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi import UploadFile, File
from fastapi.responses import Response
from voice_handler import process_voice, end_session, warmup, is_ready

from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma



# =========================================================
# ENV
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
RETRIEVER_K = 12
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")
FUZZY_THRESHOLD = 0.75


# =========================================================
# SQLITE FIX (cloud compatibility)
# =========================================================

try:
    __import__("pysqlite3")
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
    print("[STARTUP] Using pysqlite3 (cloud mode)")
except ImportError:
    print("[STARTUP] Using default sqlite3 (local mode)")


# =========================================================
# ALIAS MAP (abbreviation <-> full form)
# =========================================================

ALIAS_MAP = {
    "cse":    ["cse", "computer science", "computer science engineering"],
    "ece":    ["ece", "electronics", "electronics communication", "electronics communication engineering"],
    "eee":    ["eee", "electrical", "electrical electronics", "electrical electronics engineering"],
    "aids":   ["aids", "ai&ds", "ai ds", "artificial intelligence", "artificial intelligence data science"],
    "aiml":   ["aiml", "ai&ml", "ai ml", "artificial intelligence machine learning"],
    "iot":    ["iot", "internet of things"],
    "cs":     ["cs", "cyber security"],
    "mba":    ["mba", "business administration", "management studies"],
    "mtech":  ["mtech", "m.tech", "postgraduate"],
    "civil":  ["civil", "civil engineering"],
    "mech":   ["mech", "mechanical", "mechanical engineering"],
    "hod":    ["hod", "head of department", "department head"],
    "eapcet": ["eapcet", "eamcet", "ap eapcet", "ap eamcet"],
    "ecet":   ["ecet", "ap ecet", "lateral entry"],
    "icet":   ["icet", "ap icet"],
    "pgecet": ["pgecet", "pg ecet"],
    "nba":    ["nba", "accreditation"],
    "naac":   ["naac", "accreditation"],
    "nss":    ["nss", "national service scheme"],
    "sih":    ["sih", "smart india hackathon"],
    "rcee":   ["rcee", "rce", "ramachandra", "ramachandra college"],
    "ao":     ["ao", "administrative officer"],
    "md":     ["md", "managing director"],
}


# =========================================================
# GLOBAL RETRIEVER
# =========================================================

chroma_retriever = None


def get_retriever():

    global chroma_retriever

    if chroma_retriever:
        return chroma_retriever

    print("[STARTUP] Initializing semantic retriever...")

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("[ERROR] OPENAI_API_KEY missing in .env")
        return None

    if not os.path.exists(CHROMA_PATH):
        print(f"[ERROR] ChromaDB not found: {CHROMA_PATH}")
        return None

    embeddings = OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        openai_api_key=api_key
    )

    db = Chroma(
        persist_directory=CHROMA_PATH,
        embedding_function=embeddings
    )

    chroma_retriever = db.as_retriever(search_kwargs={"k": RETRIEVER_K})

    print(f"[STARTUP] ChromaDB loaded with {db._collection.count()} vectors")
    print("[STARTUP] Retriever ready")

    return chroma_retriever


# =========================================================
# SMART KEYWORD FILTER
# =========================================================

def get_aliases(word: str) -> list:
    word_lower = word.lower()

    if word_lower in ALIAS_MAP:
        return ALIAS_MAP[word_lower]

    for _, aliases in ALIAS_MAP.items():
        if word_lower in aliases:
            return aliases

    return [word_lower]


def word_matches_content(word: str, content_lower: str, content_words: set) -> bool:

    # Exact token match
    if word in content_words:
        return True

    # Direct substring match
    if word in content_lower:
        return True

    # Substring either direction
    for cw in content_words:
        if len(cw) >= 4 and len(word) >= 4:
            if cw in word or word in cw:
                return True

    # Fuzzy match
    if len(word) >= 4:
        for cw in content_words:
            if len(cw) >= 4:
                if SequenceMatcher(None, word, cw).ratio() >= FUZZY_THRESHOLD:
                    return True

    # Stem fallback
    if len(word) >= 4 and word[:4] in content_lower:
        return True

    return False


def has_keyword_overlap(query: str, content: str) -> tuple:
    """
    Keep chunk if ANY query keyword matches content.
    Returns:
    (keep: bool, matched_count: int, total_words: int, matched_words: list)
    """

    query_words = re.findall(r"\w+", query.lower())

    if not query_words:
        return True, 0, 0, []

    content_lower = content.lower()
    content_words = set(re.findall(r"\w+", content_lower))

    matched_words = []

    for qword in query_words:
        aliases = get_aliases(qword)
        word_matched = False

        for alias in aliases:
            if " " in alias:
                if alias in content_lower:
                    word_matched = True
                    break
            else:
                if word_matches_content(alias, content_lower, content_words):
                    word_matched = True
                    break

        if word_matched:
            matched_words.append(qword)

    matched = len(matched_words)
    total = len(query_words)

    # For your chunked DB: 1 match is enough
    keep = matched >= 1

    return keep, matched, total, matched_words


# =========================================================
# SEARCH FUNCTION
# =========================================================

def search_db(query: str):

    retriever = get_retriever()

    if not retriever:
        return []

    start = time.time()
    results = retriever.invoke(query)
    latency = round(time.time() - start, 2)

    print(f"\n{'='*70}")
    print(f"SEARCH QUERY: {query}")
    print(f"Latency: {latency}s | Raw chunks: {len(results)}")
    print(f"{'='*70}")

    seen = set()
    final = []
    skipped = 0
    sources_used = set()

    for doc in results:

        if doc.page_content in seen:
            continue

        seen.add(doc.page_content)

        source = os.path.basename(doc.metadata.get("source", "Unknown"))
        doc_name = doc.metadata.get("DocName", "")
        section = doc.metadata.get("Section", "")
        subsection = doc.metadata.get("SubSection", "")

        keep, matched, total, matched_words = has_keyword_overlap(query, doc.page_content)

        if not keep:
            print(f"  [SKIPPED] {source} | {section} | matched {matched}/{total}: {matched_words}")
            skipped += 1
            continue

        final.append(doc.page_content)
        sources_used.add(source)

        preview = doc.page_content[:180].replace("\n", " ")

        print(f"\n  [CHUNK {len(final)}] matched {matched}/{total}: {matched_words}")
        print(f"    Source:     {source}")
        if doc_name:
            print(f"    Document:   {doc_name}")
        if section:
            print(f"    Section:    {section}")
        if subsection:
            print(f"    SubSection: {subsection}")
        print(f"    Preview:    {preview}...")

    print(f"\n{'─'*70}")
    print(f"Chunks: {len(final)} used | {skipped} filtered")
    print(f"Sources: {', '.join(sources_used) if sources_used else 'none'}")
    print(f"{'='*70}\n")

    return final


# =========================================================
# FASTAPI LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app):

    # Warmup retriever
    await asyncio.to_thread(get_retriever)
    print("[SERVER] Retriever ready")

    # Warmup voice handler
    await asyncio.to_thread(warmup)
    print("[SERVER] Voice handler ready")

    print("[SERVER] Startup complete")

    yield

    print("[SERVER] Shutdown")


# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# REQUEST MODEL
# =========================================================

class QueryRequest(BaseModel):
    query: str


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/")
def health():
    return {"status": "online"}


# =========================================================
# SEARCH API
# =========================================================

@app.post("/search")
def search(request: QueryRequest):

    results = search_db(request.query)

    return {
        "query": request.query,
        "chunks": len(results),
        "results": results
    }
# =========================================================
# VOICE CALL API
# =========================================================
@app.get("/voice-call/status")
def voice_call_status():
    return {"ready": is_ready()}

@app.post("/voice-call")
async def voice_call(file: UploadFile = File(...)):
    print(f"\n{'='*70}")
    print(f"[CALL] Received audio")
    print(f"{'='*70}")
    
    audio_bytes = await file.read()
    print(f"[CALL] Size: {len(audio_bytes)} bytes")
    
    response_audio = await process_voice(audio_bytes, search_db)
    
    print(f"[CALL] Response audio: {len(response_audio)} bytes")
    print(f"{'='*70}\n")
    return Response(content=response_audio, media_type="audio/wav")

class EndCallRequest(BaseModel):
    session_id: str

@app.post("/voice-call/end")
def voice_call_end(request: EndCallRequest):
    end_session(request.session_id) 
    return {"status": "ended"}
# =========================================================
# RUN SERVER
# =========================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 8000))

    print(f"[SERVER] Starting on port {port}")

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)