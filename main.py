import uvicorn
import os
import httpx
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- CONFIGURATION ---
MODEL_NAME = "all-MiniLM-L6-v2"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/chat")

# Global Cache
ensemble_retriever = None

def get_retriever():
    """
    Lazy loads the model. Imports happen HERE to speed up server start.
    """
    global ensemble_retriever
    
    if ensemble_retriever is not None:
        return ensemble_retriever
    
    print("⏳ Starting Heavy Imports (Torch/LangChain)...", flush=True)
    
    # --- LAZY IMPORTS ---
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_classic.retrievers import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document
    # --------------------
    
    print("✅ Imports Done. Loading Model...", flush=True)

    # This uses the CPU-only torch we defined in requirements.txt
    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME)

    if not os.path.exists(CHROMA_PATH):
        print("❌ Error: 'chroma_db' folder not found.", flush=True)
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
        meta = meta if meta else {}
        searchable_text = text + " " + " ".join(str(v) for v in meta.values() if v)
        doc_objects.append(Document(page_content=searchable_text, metadata=meta))

    bm25_retriever = BM25Retriever.from_documents(doc_objects)
    bm25_retriever.k = 3
    chroma_retriever = db.as_retriever(search_kwargs={"k": 3})

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5]
    )
    print("✅ System Fully Loaded!", flush=True)
    return ensemble_retriever

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
    # Render hits this immediately to verify the app is live
    return {"status": "online", "model": "MiniLM-CPU"}

@app.post("/search")
async def search_endpoint(request: QueryRequest):
    # The flush=True ensures this prints to logs IMMEDIATELY
    print(f"\n🔎 Request Received: {request.query}", flush=True)
    
    try:
        retriever = get_retriever()
        
        if not retriever:
            return {"response": "Database Error: Folder missing or empty."}

        results = retriever.invoke(request.query)
        top_results = results[:3]
        context_text = "\n\n".join([f"Source {i+1}: {d.page_content}" for i, d in enumerate(top_results)])
        
        return {"response": context_text if context_text else "No results found."}

    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
        return {"response": f"Server Error: {str(e)}"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)