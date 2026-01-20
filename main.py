import sys
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_classic.retrievers import EnsembleRetriever

# --- 1. CONFIGURATION ---
MODEL_NAME = "all-MiniLM-L6-v2"
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
    
    print("⏳ Loading AI Models & Database...", flush=True)
    
    # Lazy imports to prevent startup crashes if libs are missing
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_community.vectorstores import Chroma
        from langchain_community.retrievers import BM25Retriever
        from langchain_core.documents import Document
        
        # Try standard import first, fallback to classic if needed

    except ImportError as e:
        print(f"❌ Critical Import Error: {e}")
        return None

    # 1. Setup Embeddings
    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME)
    
    # 2. Check Database
    if not os.path.exists(CHROMA_PATH):
        print(f"❌ Error: Database not found at {CHROMA_PATH}")
        print("   Run 'app.ipynb' first to ingest data.")
        return None

    # 3. Load ChromaDB
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
    
    # 4. Reconstruct Documents for BM25 (Keyword Search)
    # BM25 needs all documents in memory to build its index.
    # We fetch them from Chroma's internal storage.
    existing_data = db.get() # Fetch all docs
    
    if not existing_data['documents']: 
        print("❌ Error: Database is empty.")
        return None

    doc_objects = []
    for text, meta in zip(existing_data['documents'], existing_data['metadatas']):
        # Safety check: ensure metadata is a dict
        meta = meta if meta else {}
        # We use the text directly (it already has headers injected from ingestion)
        doc_objects.append(Document(page_content=text, metadata=meta))

    print(f"   📊 Indexed {len(doc_objects)} documents for Hybrid Search.")

    # 5. Initialize Retrievers
    # A. Keyword Retriever (BM25)
    bm25_retriever = BM25Retriever.from_documents(doc_objects)
    bm25_retriever.k = 6  # Fetch top 6 keyword matches

    # B. Semantic Retriever (Vector)
    chroma_retriever = db.as_retriever(search_kwargs={"k": 6}) # Fetch top 6 semantic matches

    # C. Ensemble (Hybrid)
    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5] # Equal weighting
    )
    
    print("✅ Hybrid Retrieval System Online")
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

@app.post("/search")
def search_database_only(request: QueryRequest):
    print(f"📥 Received Query: {request.query}", flush=True)
    
    retriever = get_retriever()
    if not retriever:
        return {"results": ["Error: Database unavailable. Check server logs."]}
    
    # Perform Hybrid Search
    results = retriever.invoke(request.query)
    
    # Format Output: Return top 6 distinct results
    # (Ensemble might return duplicates if both retrievers find the same doc, so we dedup)
    seen_content = set()
    final_output = []
    
    for doc in results:
        if doc.page_content not in seen_content:
            final_output.append(doc.page_content)
            seen_content.add(doc.page_content)
            
        if len(final_output) >= 6: # Limit to top 6 unique results
            break
            
    return {"results": final_output}

# --- 5. SERVER EXECUTION ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting Server on Port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)