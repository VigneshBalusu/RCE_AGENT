import os
import aiohttp
from dotenv import load_dotenv
from fastapi import WebSocket

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams
)
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

load_dotenv()

# ---------------- TRANSCRIPT LOGGER ---------------- #

class TranscriptLogger(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            print(f"\n🗣️ [USER SAID]: {frame.text}\n")
        await self.push_frame(frame, direction)

# ---------------- QUERY REWRITER ---------------- #

async def rewrite_query(llm_service, user_query: str):
    prompt = f"""
Rewrite the user query into optimized vector database search terms.

REWRITE RULES:
- Strip noise: "what is/tell me/can you/please/how about/is there"
- NEVER include numbers (ranks/marks/fees/phones)
- Keep: categories (OC,BC-A,BC-B,BC-C,BC-D,BC-E,SC,ST,EWS), exams (EAPCET,ECET,ICET,PGECET,GATE,JEE), gender
- Expand branches: aids→AI&DS Artificial Intelligence Data Science | cse→CSE Computer Science Engineering | aiml→CSE AIML Artificial Intelligence Machine Learning | cs→CSE CS Cyber Security | iot→CSE IOT Internet of Things | ece→ECE Electronics Communication Engineering | eee→EEE Electrical Electronics Engineering | mech→Mechanical Engineering | civil→Civil Engineering | mba→MBA Business Administration | mtech→M.Tech
- Add synonyms: salary→salary package CTC LPA | placements→placement recruiters companies | fees→fee structure tuition hostel | cutoff/rank→cutoff closing last rank eligibility | hostel→hostel mess accommodation | bus→bus transport route number | scholarship→scholarship fee reimbursement waiver | hod→HOD head department contact email | labs→laboratories infrastructure | clubs→student clubs IEEE ISTE | accreditation→NBA NAAC affiliation | attendance→attendance minimum regulation | backlog→backlog promotion regulation
- If comparing branches, include BOTH names
- Output = ONLY space-separated search terms, NO sentences, NO quotes

User query:
{user_query}
"""
    try:
        response = await llm_service._client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        rewritten = response.choices[0].message.content.strip()
        rewritten = rewritten.replace('"', '').replace("'", "")
        print(f"🔄 Rewritten Query → {rewritten}")
        return rewritten
    except Exception as e:
        print(f"❌ LLM Rewrite Error: {e}")
        return user_query

# ---------------- MAIN VOICE AGENT ---------------- #

async def run_voice_agent(websocket_client: WebSocket, retriever=None):

    vad_analyzer = SileroVADAnalyzer()

    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    # ✅ FIX 3 (Telugu): use_saarika=True enables multilingual STT
    # Sarvam's saarika:v2 model handles Telugu, Hindi, English automatically
    stt = SarvamSTTService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="saarika:v2.5",          # ✅ multilingual model — detects Telugu/Hindi/English
        language_code="unknown",     # ✅ auto-detect language instead of locking to en-IN
    )

    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o")

    # ✅ FIX 4 (audio distortion): increase min_buffer_size to prevent choppy audio
    # on 3rd+ responses — small buffers cause underruns when context grows
    tts = SarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="bulbul:v2",
        language="en-IN",
        speaker="anushka",
        pace=1.05,                   # ✅ slightly faster = crisper, less distortion
        min_buffer_size=150,         # ✅ was 50 — larger buffer prevents audio cuts
        max_chunk_length=90,        # ✅ was 150 — shorter chunks = smoother streaming
    )

    # ---------------- TOOL FUNCTION ---------------- #

    async def search_college_info(params: FunctionCallParams):
        user_query = params.arguments.get("query", "")
        print(f"\n🔎 ORIGINAL QUERY → {user_query}")

        rewritten_query = await rewrite_query(params.llm, user_query)

        api_url = "http://localhost:8000/search"
        results = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url,
                    json={"query": rewritten_query},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get("results", [])
                    else:
                        print(f"❌ Search API Error: Status {response.status}")
        except Exception as e:
            print(f"❌ Search API Connection Error: {e}")

        if not results:
            result_text = "No information found in the college database."
        else:
            result_text = "\n\n".join([f"Document {i+1}:\n{doc}" for i, doc in enumerate(results[:4])])

        print("✅ Results returned from /search\n")
        await params.result_callback({"database_results": result_text})

    llm.register_function("search_college_info", search_college_info)

    # ---------------- TOOL SCHEMA ---------------- #

    search_schema = FunctionSchema(
        name="search_college_info",
        description="Search Ramachandra College database. Pass the exact user utterance to this function.",
        properties={
            "query": {
                "type": "string",
                "description": "The raw, exact question the user asked."
            }
        },
        required=["query"]
    )

    tools = ToolsSchema(standard_tools=[search_schema])

    # ---------------- SYSTEM PROMPT ---------------- #

    messages = [
        {
            "role": "system",
            "content": """
You are the official voice assistant for Ramachandra College of Engineering (RCEE).
You speak naturally like a helpful college advisor — warm, concise, human.

LANGUAGE RULES:
- If the user speaks Telugu, respond in Telugu.
- If the user speaks Hindi, respond in Hindi.
- If the user speaks English, respond in English.
- If the user mixes languages, match their mix naturally.

RESPONSE RULES:
1. Always call search_college_info for any college-related question.
2. Keep answers SHORT — max 2-3 sentences. One key fact + one supporting detail only.
3. Never list more than 2 bullet points verbally — this is a voice conversation.
4. If the search result has the answer, give it directly and confidently.
5. If the search result does NOT have the answer, say: "I don't have that specific detail right now. You can contact the college at admissions@rcee.ac.in for more info."

HANDLING UNCLEAR INPUT:
- If the user's question seems garbled, off-topic, or unclear (bad STT), respond with: "Sorry, I didn't catch that clearly. Could you please repeat your question?"
- Never make up answers. Never hallucinate facts about the college.

TONE: Friendly, brief, like a knowledgeable senior student helping a junior.
"""
        }
    ]

    context = LLMContext(messages=messages, tools=tools)

    context_aggregator = LLMContextAggregatorPair(
        context=context,
        user_params=LLMUserAggregatorParams(vad_analyzer=vad_analyzer)
    )

    # ---------------- PIPELINE ---------------- #

    pipeline = Pipeline([
        transport.input(),
        stt,
        TranscriptLogger(),
        context_aggregator.user(),
        llm,
        tts,                             # ✅ TTS before assistant aggregator
        context_aggregator.assistant(),  # ✅ saves context after speaking
        transport.output(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
        enable_rtvi=False
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        print("📞 Client connected", flush=True)

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)