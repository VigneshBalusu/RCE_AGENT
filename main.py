import sys
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- 1. CONFIGURATION ---
MODEL_NAME = "all-MiniLM-L6-v2"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")

# --- 2. CLOUD COMPATIBILITY (Safety Check) ---
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
    print("✅ Cloud Environment: Swapped to pysqlite3")
except ImportError:
    print("💻 Local Environment: Using default sqlite3")

# --- 3. RETRIEVER SETUP ---
ensemble_retriever = None

def get_retriever():
    global ensemble_retriever
    if ensemble_retriever is not None: return ensemble_retriever
    
    print("⏳ Loading AI Models...", flush=True)
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_classic.retrievers import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document

    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME)
    
    # Check if DB exists
    if not os.path.exists(CHROMA_PATH):
        print(f"❌ Error: Database not found at {CHROMA_PATH}")
        return None

    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
    existing_data = db.get()
    
    if not existing_data['documents']: return None

    doc_objects = []
    for text, meta in zip(existing_data['documents'], existing_data['metadatas']):
        meta = meta if meta else {}
        search_text = text + " " + " ".join(str(v) for v in meta.values() if v)
        doc_objects.append(Document(page_content=search_text, metadata=meta))

    # --- THE FIX: k=10 (Reads more data) ---
    bm25_retriever = BM25Retriever.from_documents(doc_objects)
    bm25_retriever.k = 10
    chroma_retriever = db.as_retriever(search_kwargs={"k": 10})

    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, chroma_retriever],
        weights=[0.5, 0.5]
    )
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
    return {"status": "Database Online"}

@app.post("/search")
def search_database_only(request: QueryRequest):
    print(f"📥 DB Search: {request.query}", flush=True)
    retriever = get_retriever()
    if not retriever:
        return {"results": ["Database Error: Not found."]}
    
    results = retriever.invoke(request.query)
    # Return top 6 results to give the AI enough info
    return {"results": [doc.page_content for doc in results[:6]]}

import os
import uvicorn

# ... (rest of your code) ...

if __name__ == "__main__":
    # Get the port from the environment, default to 8000 if running locally
    port = int(os.environ.get("PORT", 8000))
    # host="0.0.0.0" is CRITICAL for Render
    uvicorn.run(app, host="0.0.0.0", port=port)