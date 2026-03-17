import sys
import os
import time
import re
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
# KEYWORD FILTER
# =========================================================

def has_keyword_overlap(query: str, content: str):

    query_words = set(re.findall(r"\w+", query.lower()))

    if not query_words:
        return True

    content_lower = content.lower()

    for word in query_words:
        stem = word[:4] if len(word) >= 4 else word
        if stem in content_lower:
            return True

    return False


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

        if not has_keyword_overlap(query, doc.page_content):
            print(f"  [SKIPPED] {source} | {section}")
            skipped += 1
            continue

        final.append(doc.page_content)
        sources_used.add(source)

        preview = doc.page_content[:180].replace("\n", " ")

        print(f"\n  [CHUNK {len(final)}]")
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

    await asyncio.to_thread(get_retriever)

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
# RUN SERVER
# =========================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 8000))

    print(f"[SERVER] Starting on port {port}")

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)