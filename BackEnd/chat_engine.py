import os
import json
from typing import Dict, List
from openai import AsyncOpenAI
from dotenv import load_dotenv

# 1. Load the environment variables BEFORE initializing the client
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# 2. Initialize OpenAI Client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =========================================================
# MEMORY MANAGEMENT (WITH ROUTE TRACKING)
# =========================================================
chat_memory: Dict[str, List[Dict[str, str]]] = {}

def update_memory(session_id: str, role: str, content: str, route: str = None):
    if session_id not in chat_memory:
        chat_memory[session_id] = []
    
    entry = {"role": role, "content": content}
    if route:
        entry["route"] = route # Save the route if it exists
        
    chat_memory[session_id].append(entry)
    
    # Keep only the last 10 items (5 User + 5 Assistant turns)
    if len(chat_memory[session_id]) > 10:
        chat_memory[session_id] = chat_memory[session_id][-10:]

def get_history(session_id: str) -> str:
    if session_id not in chat_memory:
        return "No previous conversation history."
    
    history_str = ""
    for msg in chat_memory[session_id]:
        if msg['role'] == 'assistant' and 'route' in msg:
            # Tell the LLM exactly where this answer came from
            history_str += f"Assistant (Answered via {msg['route'].upper()} search): {msg['content']}\n"
        else:
            history_str += f"{msg['role'].capitalize()}: {msg['content']}\n"
    return history_str

# =========================================================
# 1. QUERY OPTIMIZER 
# =========================================================
async def analyze_query(query: str, session_id: str) -> dict:
    history = get_history(session_id)
    
    system_prompt = """
    RCEE DB query optimizer. Analyze the user query and return a JSON object.

    Rules:
    1. Output ONLY valid JSON. Nothing else. No markdown. No backticks.
    2. CRITICAL CONTEXT & ROUTE RESOLUTION: Read the Chat History carefully. Pay attention to how previous questions were answered (e.g., "Answered via WEB search").
       - If the user asks a follow-up to a WEB search, you MUST route to "web" and include the context in your keywords.
       - If the user asks a follow-up to a DB search, inject the missing person/location from the history into your keywords and route to "db".
       - If the user changes the subject completely, ignore the previous route and route based on the new topic.
    3. Strip filler words (what is, tell me, please) but DO NOT strip the contextual names/locations you resolved.
    4. Route to "casual" if the query is a basic greeting or small talk.
    5. Route to "web" if query is about: latest news, events, current affairs, rankings, recent updates, OR follow-ups to recent web searches.
    6. Route to "db" for everything else (fees, cutoff, hostel, placements, eligibility, departments, contact, etc.).
    7. Include categories/exams ONLY for eligibility queries.
    8. don't mention RCE or RamaChandra College of Engineering or the word 'college' in your output

    Output format:
    {"keywords": "<search keywords here or empty if casual>", "route": "<db | web | casual>"}

    Branch mapping: aids->AI&DS | cse->CSE | aiml->CSE AIML | cs->CSE Cyber Security | iot->CSE IOT | ece->ECE | eee->EEE | mech->Mechanical | civil->Civil | mba->MBA | mtech->M.Tech
    Exam mapping: eamcet/emcet/eapcet->AP EAPCET | ecet->AP ECET | icet->AP ICET | pgecet->PGECET | gate->GATE
    Category mapping: general/open->OC | bc-a->BC-A | sc->SC | st->ST | ews->EWS
    Synonyms to add: salary->package CTC | placements->recruiters | fees->tuition hostel | cutoff->rank eligibility | hostel->mess accommodation | bus->transport route | scholarship->fee reimbursement | hod->head department contact | labs->infrastructure
    """

    user_prompt = f"Chat History:\n{history}\n\nCurrent Query: {query}"

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0,
        response_format={ "type": "json_object" }
    )
    
    try:
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"[OPTIMIZER ERROR] Failed to parse JSON: {e}")
        return {"keywords": query, "route": "db"}

# =========================================================
# 2. GENERATORS (DB, Web, Casual)
# =========================================================
async def generate_casual_answer(query: str, session_id: str) -> str:
    system_prompt = "You are the friendly Official Assistant for RCE Ramachandra College of Engineering (Autonomous). Respond politely and concisely to casual greetings. Guide them to ask about the college."
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]
    )
    return response.choices[0].message.content

async def generate_web_answer(user_query: str, session_id: str) -> str:
    history = get_history(session_id)
    
    prompt = f"""Role: Official Assistant, RCE Ramachandra College of Engineering Autonomus.
Task: Answer the user's "Query" using ONLY the "Web Search Only". 

CRITICAL SEARCH INSTRUCTION:
When you use the web search tool, you MUST restrict your search to the college website by appending "site:rcee.ac.in" to your search queries. Ignore all other websites.

Chat History (for context):
{history}

Rules:
1. Be concise, professional, and use NO external knowledge.
2. If the answer is not related to the query or not found on the website, output EXACTLY: "I don't have this specific information. Please contact helpdesk@rcee.ac.in or call +91-9492936222 (9AM-5PM)." Do not apologize.
"""
    print(f"[WEB] User Query: '{user_query}'", flush=True)

    try:
        response = await client.responses.create(
            model="gpt-4o", 
            tools=[{
                "type": "web_search_preview",
                "search_context_size": "high"
            }],
            instructions=prompt,
            input=user_query 
        )

        answer = response.output_text.strip()
        
        # Enforce max length for voice/chat safety
        if len(answer) > 300:
            answer = answer[:300].rsplit(".", 1)[0].strip() + "."
            
        print(f"[WEB] Answer: {len(answer)} chars", flush=True)
        return answer

    except Exception as e:
        print(f"[WEB] Error: {e}", flush=True)
        return "I don't have this specific information. Please contact helpdesk@rcee.ac.in or call +91-9492936222 (9AM-5PM)."

async def generate_db_answer(query: str, chunks: list, session_id: str) -> str:
    history = get_history(session_id)
    db_context = "\n\n".join(chunks)
    
    system_prompt = f"""Role: Official Assistant, RCE Ramachandra College of Engineering (Autonomous).
Task: Answer the "Query" using ONLY the "DB Results".

Chat History (for context/pronouns):
{history}

DB Results:
{db_context}

Rules:
1. Be concise, professional, and friendly. Use NO external knowledge.
2. Think from a student's or parent's perspective.
3. ALWAYS look for connections. If the user asks for a "contact number" and you see an email or a department for that person, provide whatever information is available. Never say "I don't have information" if the person or topic is mentioned in the results.
4. SURGICAL EXTRACTION (CRITICAL): You are strictly forbidden from summarizing whole lists. If the user asks about a specific location (e.g., "Vijayawada"), a specific branch, or a specific person, you MUST scan the DB Results for that exact entity and extract ONLY the single sentence, route, or bullet point that matches it. Delete all other routes, branches, or names from your output.
5. ONLY if DB Results have absolutely NO relevant information matching the query, output EXACTLY:
   "I don't have this specific information. Please contact helpdesk@rcee.ac.in or call +91-9492936222 (9AM-5PM)." 
6. Do not mention DB, results, data sources, or chunks. Answer as if you naturally know it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RANK & ELIGIBILITY RULES (CRITICAL — READ CAREFULLY)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

7. These rules apply to ALL entrance exams: EAMCET/EAPCET (1st year B.Tech, for
   inter/12th students) and AP ECET (lateral entry 2nd year B.Tech, for polytechnic
   diploma holders and BSc Maths graduates). The eligibility logic is identical for both.

8. RANK DIRECTION: Lower rank number = better rank. Rank 1 is the best rank possible.

9. ELIGIBILITY CHECK — follow this logic exactly, no exceptions:

   - "Last Rank" = closing rank = cutoff. ONLY compare student rank against Last Rank.
   - "First Rank" = topper's rank. NEVER use First Rank for eligibility.
   - ELIGIBLE ✅ if: student's rank NUMBER <= Last Rank NUMBER
   - NOT ELIGIBLE ❌ if: student's rank NUMBER > Last Rank NUMBER

   EXAMPLE:
     Student rank = 35,000
     CSE (AI&ML) | Last Rank 4,047  → 35,000 > 4,047 → NOT ELIGIBLE ❌
     Mechanical  | Last Rank 1,77,317 → 35,000 < 1,77,317 → ELIGIBLE ✅

FORMATTING:
- Bus queries: include bus numbers.
- Fee queries: include all components.
- Contact queries: include name, designation, phone, and email if available.
- Processes: list ALL steps in numbered order based on the data.
"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Query: {query}"}
        ]
    )
    return response.choices[0].message.content