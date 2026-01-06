import uvicorn
import os
import httpx  # ✅ FASTER: Async HTTP client
import json
import time   # ✅ NEW: To measure speed
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

# --- LangChain & Chroma Imports ---
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

# --- CONFIGURATION ---
MODEL_NAME = "all-MiniLM-L6-v2"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")

# ✅ DEPLOYMENT UPDATE: Read URL from Environment Variable
# If running on Render, it uses the "N8N_WEBHOOK_URL" variable you set in the dashboard.
# If running locally, it falls back to "http://localhost:5678..."
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/chat")

# ---------------------

ensemble_retriever = None

def initialize_retrievers():
    global ensemble_retriever
    print(f"📂 Connecting to Database at: {CHROMA_PATH}")

    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME)

    if not os.path.exists(CHROMA_PATH):
        print("❌ Error: 'chroma_db' folder not found.")
        return

    db = Chroma(
        persist_directory=CHROMA_PATH,
        embedding_function=embeddings
    )

    print("🔍 Loading documents for Keyword Search...")
    existing_data = db.get()
    docs_text = existing_data['documents']
    docs_metadata = existing_data['metadatas']
    
    if not docs_text:
        print("⚠️ Warning: Database is empty!")
        return
    else:
        print(f"📊 Loaded {len(docs_text)} chunks.")

    doc_objects = []
    for text, meta in zip(docs_text, docs_metadata):
        # Combine text and metadata values for better keyword matching
        searchable_text = text + " " + " ".join(str(v) for v in meta.values() if v)
        doc_objects.append(Document(page_content=searchable_text, metadata=meta))

    bm25_retriever = BM25Retriever.from_documents(doc_objects)
    bm25_retriever.k = 3  # ✅ OPTIMIZATION: Only fetch top 3 (Faster)
    chroma_retriever = db.as_retriever(search_kwargs={"k": 3})

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5]
    )
    print("✅ Hybrid Search System Online!")

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_retrievers()
    yield
    print("🛑 Server Shutting Down...")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str

def extract_n8n_answer(data):
    # 1. Handle List
    if isinstance(data, list):
        if len(data) > 0: data = data[0]
        else: return "Error: n8n returned an empty list."

    # 2. Handle Dict
    if isinstance(data, dict):
        for key in ["output", "text", "response", "answer", "content", "result"]:
            if key in data and isinstance(data[key], str):
                return data[key]
        return json.dumps(data) # Fallback
        
    return str(data)

# --- 🆕 NEW ENDPOINT: SEARCH ---
# This fixes the "404 Not Found" error when n8n tries to call the tool
@app.post("/search")
async def search_endpoint(request: QueryRequest):
    """
    Endpoint specifically for the n8n 'search_college_db' tool.
    It returns the raw text context so the AI can read it.
    """
    print(f"\n🔎 Tool Query received: {request.query}")
    
    if not ensemble_retriever:
        raise HTTPException(status_code=500, detail="Database not initialized yet.")

    try:
        results = ensemble_retriever.invoke(request.query)
        
        # Take the top 3 results
        top_results = results[:3]
        
        # Format them into a single string for the AI to read
        context_text = "\n\n".join([f"Source {i+1}: {d.page_content}" for i, d in enumerate(top_results)])
        
        if not context_text:
            context_text = "No relevant documents found in the college database."

        return {"response": context_text}

    except Exception as e:
        print(f"❌ Search Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- EXISTING ENDPOINT: CHAT ---
@app.post("/chat")
async def chat_endpoint(request: QueryRequest):
    if not ensemble_retriever:
        raise HTTPException(status_code=500, detail="System initializing...")
    
    print(f"\n📩 Query: {request.query}")
    start_total = time.time()
    
    # 1. SEARCH PHASE (Pre-fetch context to send to n8n initially)
    t0 = time.time()
    results = ensemble_retriever.invoke(request.query)
    # Reduced to top 3 for speed
    context_text = "\n\n".join([d.page_content for d in results[:3]])
    t1 = time.time()
    print(f"⏱️ Database Search Time: {round(t1 - t0, 2)}s")

    payload = {
        "query": request.query,
        "context": context_text
    }

    # 2. AI GENERATION PHASE (n8n)
    try:
        print(f"🚀 Sending to n8n at: {N8N_WEBHOOK_URL}")
        async with httpx.AsyncClient(timeout=30.0) as client: # 30s timeout
            n8n_response = await client.post(N8N_WEBHOOK_URL, json=payload)
        
        t2 = time.time()
        print(f"⏱️ n8n Response Time: {round(t2 - t1, 2)}s") 

        try:
            n8n_data = n8n_response.json()
        except:
            return {"response": n8n_response.text}

        final_answer = extract_n8n_answer(n8n_data)
        
        print(f"✅ Total Request Time: {round(time.time() - start_total, 2)}s")
        return {"response": final_answer}

    except httpx.ReadTimeout:
        print("❌ Error: n8n took too long to answer (Timeout).")
        return {"response": "Error: The AI is taking too long to respond. Please try again."}
    except Exception as e:
        print(f"❌ Error connecting to n8n: {e}")
        return {"response": f"Error: Could not connect to AI logic. (Details: {e})"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)