import sys
import os
import uvicorn
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_classic.retrievers import EnsembleRetriever

# --- 1. CONFIGURATION ---
OPENAI_EMBEDDING_MODEL = "text-embedding-ada-002"  # 1536-dim, matches existing ChromaDB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")

# --- 2. CLOUD COMPATIBILITY (Safety Check for Render/Streamlit) ---
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
    print("✅ Cloud Environment: Swapped to pysqlite3")
except ImportError:
    print("💻 Local Environment: Using default sqlite3")

# --- 3. RETRIEVER SETUP ---
# Global variable to cache the retriever so we don't reload it on every request
ensemble_retriever = None

def get_retriever():
    global ensemble_retriever
    if ensemble_retriever is not None:
        return ensemble_retriever
    
    print("⏳ [STARTUP] Initializing retrieval system...", flush=True)
    
    # Lazy imports to prevent startup crashes if libs are missing
    try:
        import glob
        import re
        import time
        from langchain_openai import OpenAIEmbeddings
        from langchain_community.vectorstores import Chroma
        from langchain_community.retrievers import BM25Retriever
        from langchain_community.document_loaders import TextLoader
        from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document
        print("✅ [STARTUP] All imports successful.", flush=True)

    except ImportError as e:
        print(f"❌ [STARTUP] Critical Import Error: {e}", flush=True)
        return None

    # Custom BM25 preprocessor: strips markdown punctuation AND underscores so
    # 'Principal,' → 'principal' and 'CUTOFF_RANKS' → 'cutoff ranks' match plain query terms.
    def markdown_preprocess(text: str):
        text = text.lower()
        text = re.sub(r'[^a-z0-9 ]', ' ', text)  # remove all non-alphanumeric (incl. underscores)
        text = re.sub(r' +', ' ', text)           # collapse multiple spaces
        return text.split()

    # 1. Setup Embeddings
    print(f"🔑 [STARTUP] Checking OPENAI_API_KEY from .env...", flush=True)
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("❌ [STARTUP] OPENAI_API_KEY not found in .env file!", flush=True)
        return None
    print(f"✅ [STARTUP] API key found (ends with: ...{openai_api_key[-6:]})", flush=True)
    print(f"🧠 [STARTUP] Loading OpenAI embedding model: {OPENAI_EMBEDDING_MODEL}", flush=True)
    embeddings = OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL, openai_api_key=openai_api_key)
    print("✅ [STARTUP] OpenAI embeddings ready.", flush=True)

    # 2. Check Database
    print(f"💾 [STARTUP] Checking ChromaDB at: {CHROMA_PATH}", flush=True)
    if not os.path.exists(CHROMA_PATH):
        print(f"❌ [STARTUP] ChromaDB not found! Run 'traning.ipynb' first.", flush=True)
        return None
    print("✅ [STARTUP] ChromaDB directory exists.", flush=True)

    # 3. Load ChromaDB (for semantic search)
    print("📂 [STARTUP] Loading ChromaDB collection...", flush=True)
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
    chroma_count = db._collection.count()
    print(f"✅ [STARTUP] ChromaDB loaded — {chroma_count} vectors in collection.", flush=True)

    # 4. Build BM25 from FRESH markdown files on disk
    DATA_DIR = os.path.join(BASE_DIR, "Data")
    print(f"📁 [STARTUP] Scanning markdown files in: {DATA_DIR}", flush=True)
    md_files = glob.glob(os.path.join(DATA_DIR, "**", "*.md"), recursive=True)
    print(f"   Found {len(md_files)} .md files:", flush=True)
    for f in md_files:
        print(f"     - {os.path.relpath(f, DATA_DIR)}", flush=True)

    if not md_files:
        print(f"❌ [STARTUP] No markdown files found in {DATA_DIR}", flush=True)
        return None

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    fresh_docs = []
    for filepath in md_files:
        try:
            loader = TextLoader(filepath, encoding="utf-8")
            raw = loader.load()
            chunks = splitter.split_documents(raw)
            fresh_docs.extend(chunks)
            print(f"   ✅ {os.path.basename(filepath)} → {len(chunks)} chunks", flush=True)
        except Exception as e:
            print(f"   ⚠️  Skipping {os.path.basename(filepath)}: {e}", flush=True)

    print(f"📊 [STARTUP] BM25 total: {len(fresh_docs)} chunks from {len(md_files)} files.", flush=True)

    # 5. Initialize Retrievers
    print("⚙️  [STARTUP] Building BM25 index from fresh chunks...", flush=True)
    bm25_retriever = BM25Retriever.from_documents(
        fresh_docs,
        preprocess_func=markdown_preprocess  # strips commas/asterisks so 'Principal,' matches 'principal'
    )
    bm25_retriever.k = 6
    print("✅ [STARTUP] BM25 index ready (k=6, punctuation-stripped tokenizer).", flush=True)

    print("⚙️  [STARTUP] Configuring ChromaDB semantic retriever (k=6)...", flush=True)
    chroma_retriever = db.as_retriever(search_kwargs={"k": 6})
    print("✅ [STARTUP] Semantic retriever ready.", flush=True)

    print("⚙️  [STARTUP] Building EnsembleRetriever (BM25: 0.5, Semantic: 0.5)...", flush=True)
    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5]  # Balanced — ChromaDB re-ingested with fresh optimized embeddings
    )
    print("✅ [STARTUP] Hybrid Retrieval System ONLINE — ready for queries.", flush=True)
    return ensemble_retriever

# --- 4. FASTAPI APP SETUP ---
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str

@app.get("/")
def health_check():
    return {"status": "Database API Online", "type": "Hybrid RAG"}

@app.on_event("startup")
async def startup_event():
    """Pre-warm the retriever AND the OpenAI embedding TCP connection at server startup."""
    import asyncio
    print("🔥 [BOOT] Pre-warming retrieval system...", flush=True)
    r = get_retriever()
    # Warm up the OpenAI embedding API connection so the first real query is instant
    if r:
        try:
            r.retrievers[1].vectorstore.similarity_search("warmup", k=1)
            print("🔌 [BOOT] OpenAI embedding connection established.", flush=True)
        except Exception as e:
            print(f"⚠️  [BOOT] Warmup ping failed (non-critical): {e}", flush=True)
    print("🚀 [BOOT] Server fully ready — all queries will be fast.", flush=True)

@app.post("/debug")
def debug_search(request: QueryRequest):
    """Debug endpoint: tests BM25 and semantic search independently."""
    import time
    q = request.query
    print(f"\n{'#'*60}", flush=True)
    print(f"🛠️  [DEBUG] Query: '{q}'", flush=True)
    print(f"{'#'*60}", flush=True)

    retriever = get_retriever()
    if not retriever:
        return {"error": "Retriever not available"}

    bm25_r   = retriever.retrievers[0]   # BM25
    chroma_r = retriever.retrievers[1]   # Semantic

    # --- BM25 alone ---
    print("\n🔤 [DEBUG] BM25 results (keyword-only):", flush=True)
    bm25_docs = bm25_r.invoke(q)
    bm25_out = []
    for i, d in enumerate(bm25_docs[:6]):
        src = os.path.basename(d.metadata.get("source", "?"))
        snip = d.page_content[:100].replace("\n", " ")
        print(f"   BM25[{i+1}] {src} → {snip}", flush=True)
        bm25_out.append({"source": src, "snippet": snip})

    # --- Semantic alone ---
    print("\n🧠 [DEBUG] Semantic results (chromadb-only):", flush=True)
    semantic_docs = chroma_r.invoke(q)
    semantic_out = []
    for i, d in enumerate(semantic_docs[:6]):
        src = os.path.basename(d.metadata.get("source", "?"))
        snip = d.page_content[:100].replace("\n", " ")
        print(f"   SEM[{i+1}] {src} → {snip}", flush=True)
        semantic_out.append({"source": src, "snippet": snip})

    # --- Ensemble ---
    print("\n🔀 [DEBUG] Ensemble (fused) results:", flush=True)
    ensemble_docs = retriever.invoke(q)
    ensemble_out = []
    seen = set()
    for i, d in enumerate(ensemble_docs):
        if d.page_content in seen: continue
        seen.add(d.page_content)
        src = os.path.basename(d.metadata.get("source", "?"))
        snip = d.page_content[:100].replace("\n", " ")
        print(f"   ENS[{len(ensemble_out)+1}] {src} → {snip}", flush=True)
        ensemble_out.append({"source": src, "snippet": snip})
        if len(ensemble_out) >= 6: break

    return {"bm25": bm25_out, "semantic": semantic_out, "ensemble": ensemble_out}

@app.post("/search")
def search_database_only(request: QueryRequest):
    import time
    print("\n" + "="*60, flush=True)
    print(f"📥 [QUERY] Received: '{request.query}'", flush=True)
    print("="*60, flush=True)
    
    retriever = get_retriever()
    if not retriever:
        print("❌ [QUERY] Retriever unavailable — aborting.", flush=True)
        return {"results": ["Error: Database unavailable. Check server logs."]}
    
    # Perform Hybrid Search
    print(f"🔎 [QUERY] Running hybrid search (BM25 + Semantic)...", flush=True)
    t0 = time.time()
    results = retriever.invoke(request.query)
    elapsed = round(time.time() - t0, 2)
    print(f"⏱️  [QUERY] Search completed in {elapsed}s — got {len(results)} raw results.", flush=True)
    
    # Dedup and format
    seen_content = set()
    final_output = []
    
    print(f"📋 [QUERY] Deduplicating and selecting top 6...", flush=True)
    for i, doc in enumerate(results):
        source = doc.metadata.get('source', 'Unknown')
        snippet = doc.page_content[:120].replace('\n', ' ')
        print(f"   [{i+1}] source={os.path.basename(source)} | snippet: {snippet}...", flush=True)
        
        if doc.page_content not in seen_content:
            final_output.append(doc.page_content)
            seen_content.add(doc.page_content)
            
        if len(final_output) >= 6:
            break
    
    print(f"✅ [QUERY] Returning {len(final_output)} unique results to client.", flush=True)
    return {"results": final_output}

# --- 5. SERVER EXECUTION ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Server on Port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)