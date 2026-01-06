import uvicorn
import os
import httpx
import json
import time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- 1. CONFIGURATION ---
# We do NOT import langchain/chroma here. That kills the server startup speed.
MODEL_NAME = "all-MiniLM-L6-v2"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/chat")

# Global cache for the AI Brain
ensemble_retriever = None

# --- 2. THE LAZY LOADER (The Magic Fix) ---
def get_retriever():
    """
    Imports and loads heavy libraries ONLY when needed.
    This prevents the 'Port Scan Timeout' error on Render.
    """
    global ensemble_retriever
    
    # If we already loaded it, return it immediately
    if ensemble_retriever is not None:
        return ensemble_retriever
    
    print("⏳ First request received. Starting Heavy Imports now...")
    
    # --- HEAVY IMPORTS MOVED INSIDE HERE ---
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_classic.retrievers import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document
    # ---------------------------------------
    
    print("✅ Imports Done. Connecting to Database...")

    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME)

    if not os.path.exists(CHROMA_PATH):
        print("❌ Error: 'chroma_db' folder not found on server.")
        return None

    db = Chroma(
        persist_directory=CHROMA_PATH,
        embedding_function=embeddings
    )

    existing_data = db.get()
    docs_text = existing_data['documents']
    docs_metadata = existing_data['metadatas']
    
    if not docs_text:
        return None

    doc_objects = []
    for text, meta in zip(docs_text, docs_metadata):
        searchable_text = text + " " + " ".join(str(v) for v in meta.values() if v)
        doc_objects.append(Document(page_content=searchable_text, metadata=meta))

    bm25_retriever = BM25Retriever.from_documents(doc_objects)
    bm25_retriever.k = 3
    chroma_retriever = db.as_retriever(search_kwargs={"k": 3})

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5]
    )
    print("✅ System Fully Loaded and Ready!")
    return ensemble_retriever

# --- 3. FAST STARTUP ---
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

def extract_n8n_answer(data):
    if isinstance(data, list):
        if len(data) > 0: data = data[0]
        else: return "Error: n8n returned an empty list."
    if isinstance(data, dict):
        for key in ["output", "text", "response", "answer", "content", "result"]:
            if key in data and isinstance(data[key], str):
                return data[key]
        return json.dumps(data)
    return str(data)

# --- 4. ENDPOINTS ---

@app.get("/")
def health_check():
    # Render checks this to know we are alive
    return {"status": "online"}

@app.post("/search")
async def search_endpoint(request: QueryRequest):
    print(f"\n🔎 Search Query: {request.query}")
    
    # This triggers the loading (might take 10s on the very first try)
    retriever = get_retriever()
    
    if not retriever:
        return {"response": "Database not initialized or empty."}

    try:
        results = retriever.invoke(request.query)
        top_results = results[:3]
        context_text = "\n\n".join([f"Source {i+1}: {d.page_content}" for i, d in enumerate(top_results)])
        
        if not context_text:
            context_text = "No relevant documents found."

        return {"response": context_text}

    except Exception as e:
        print(f"❌ Search Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat")
async def chat_endpoint(request: QueryRequest):
    print(f"\n📩 Chat Query: {request.query}")
    retriever = get_retriever()
    
    if retriever:
        results = retriever.invoke(request.query)
        context_text = "\n\n".join([d.page_content for d in results[:3]])
    else:
        context_text = "Database unavailable."

    payload = {
        "query": request.query,
        "context": context_text
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            n8n_response = await client.post(N8N_WEBHOOK_URL, json=payload)
        
        try:
            n8n_data = n8n_response.json()
        except:
            return {"response": n8n_response.text}

        final_answer = extract_n8n_answer(n8n_data)
        return {"response": final_answer}

    except Exception as e:
        return {"response": f"Error: {e}"}

if __name__ == "__main__":
    # This matches Render's expectation for port 10000
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)