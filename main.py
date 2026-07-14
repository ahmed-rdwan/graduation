import os
import random
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict

from pymongo import MongoClient

# LangChain Imports
from langchain_community.vectorstores import Chroma
from langchain_core.messages import ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

def get_random_api_key():
    keys = os.getenv("GOOGLE_API_KEYS")
    if keys:
        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        if key_list:
            selected_key = random.choice(key_list)
            # Override env variable so other modules like embeddings use it implicitly if needed
            os.environ["GOOGLE_API_KEY"] = selected_key
            return selected_key
    return os.getenv("GOOGLE_API_KEY")

from allocation_engine import router as allocation_router

# Tools - مسارات صحيحة
from agent_tools import (
    create_ticket, manage_stock, update_task_status,
    get_inventory, search_employee, get_my_tasks,
    get_sprint_status, get_my_tickets,
    get_team_report, update_ticket_status,
    create_project, get_projects, send_notification,
    get_my_notifications, check_attendance, get_company_stats,
    create_team, get_teams, create_backlog,
    get_backlogs, create_task, create_sprint
)

load_dotenv()
app = FastAPI(title="IT Management Agentic RAG API")

MAX_HISTORY_MESSAGES = 4

client = MongoClient(os.getenv("MONGO_URI"))
db = client["test"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------
# 2. RAG Setup & Claude's Auto-Build
# -----------------------------------------------
embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")

def load_vector_db():
    db_path = "./chroma_db"
    doc_count = 0
    try:
        vdb = Chroma(persist_directory=db_path, embedding_function=embeddings)
        doc_count = vdb._collection.count()
        print(f"✅ Chroma loaded — {doc_count} documents in vector DB.")
    except Exception as e:
        print(f"❌ Chroma load error: {e}")
        vdb = None
    return vdb, doc_count

vector_db, doc_count = load_vector_db()

import threading

def background_setup():
    print("⚠️ Vector DB is EMPTY — running setup in background...")
    try:
        from setup_vector_db import setup_database
        setup_database()
        global vector_db
        vector_db, _ = load_vector_db()
        print("✅ Background setup complete.")
    except Exception as e:
        print(f"❌ Background setup failed: {e}")

if doc_count == 0:
    threading.Thread(target=background_setup, daemon=True).start()

# -----------------------------------------------
# 3. The Silent Watcher (Auto-Sync)
# -----------------------------------------------
async def silent_db_watcher():
    last_count = -1
    while True:
        try:
            current_count = (
                db.users.count_documents({}) + 
                db.projects.count_documents({}) + 
                db.tasks.count_documents({}) +
                db.tickets.count_documents({})
            )

            if last_count != -1 and current_count != last_count:
                print(f"🔄 AI Noticed DB changes! Count: {current_count}. Auto-syncing...")
                from setup_vector_db import setup_database
                setup_database()
                global vector_db
                vector_db, _ = load_vector_db()
                print("✅ AI Memory updated successfully!")

            last_count = current_count
        except Exception as e:
            print(f"❌ Watcher Error: {e}")

        await asyncio.sleep(900)

@app.on_event("startup")
async def start_watcher():
    asyncio.create_task(silent_db_watcher())
    asyncio.create_task(silent_stock_watcher())

async def silent_stock_watcher():
    last_stock_state = {}
    while True:
        try:
            from bson.objectid import ObjectId
            import datetime
            stocks = list(db.stocks.find({}, {"_id": 1, "quantity": 1}))
            current_state = {str(s["_id"]): s.get("quantity", 0) for s in stocks}
            
            if last_stock_state:
                for stock_id, current_qty in current_state.items():
                    last_qty = last_stock_state.get(stock_id)
                    if last_qty is not None and current_qty < last_qty:
                        diff = last_qty - current_qty
                        print(f"📦 AI Noticed stock {stock_id} reduced by {diff}! Logging history...")
                        db.ai_stock_history.insert_one({
                            "stock_id": ObjectId(stock_id),
                            "quantity": diff,
                            "action": "remove",
                            "transaction_date": datetime.datetime.utcnow()
                        })
            
            last_stock_state = current_state
        except Exception as e:
            print(f"❌ Stock Watcher Error: {e}")
            
        await asyncio.sleep(60)

# -----------------------------------------------
# 4. Debug & Admin Endpoints
# -----------------------------------------------
@app.get("/health")
async def health_check():
    count = vector_db._collection.count() if vector_db else 0
    return {"status": "ok", "vector_db_docs": count}

@app.post("/api/rebuild-db")
async def rebuild_vector_db():
    global vector_db
    try:
        from setup_vector_db import setup_database
        setup_database()
        vector_db, count = load_vector_db()
        return {"message": f"✅ Vector DB rebuilt with {count} documents."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------
# 5. LLM & Tools Setup
# -----------------------------------------------
# القائمة المحدثة للأدوات
tools = [
    create_ticket, manage_stock, update_task_status,
    get_inventory, search_employee, get_my_tasks,
    get_sprint_status, get_my_tickets,
    get_team_report, update_ticket_status,
    create_project, get_projects, send_notification,
    get_my_notifications, check_attendance, get_company_stats,
    create_team, get_teams, create_backlog,
    get_backlogs, create_task, create_sprint
]

# -----------------------------------------------
# 6. Main Endpoint (تحديث Multi-Tenancy)
# -----------------------------------------------
class ChatRequest(BaseModel):
    query: str
    user_role: str
    user_id: str
    company_id: str  
    chat_history: List[Dict[str, str]] = []

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        # Load balancing across API keys
        current_api_key = get_random_api_key()
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0, google_api_key=current_api_key)
        agent_llm = llm.bind_tools(tools)

        context = "Database is currently syncing, context is temporarily unavailable."
        if vector_db is not None:
            retriever = vector_db.as_retriever(search_kwargs={"k": 4, "filter": {"company_id": request.company_id}})
            docs = await retriever.ainvoke(request.query)
            context = format_docs(docs)

        system_prompt = f"""You are an intelligent IT Management assistant.
Current user: role='{request.user_role}', id='{request.user_id}', company_id='{request.company_id}'.

TOOL RULES:
1. ALWAYS pass '{request.user_role}' as `user_role`, '{request.user_id}' as `user_id`, and '{request.company_id}' as `company_id` to EVERY tool exactly as provided.
2. For ticket status, use ONLY: 'open', 'in_progress', 'resolved', 'closed'.
3. For task status, use ONLY: 'todo', 'in_progress', 'completed'.
4. For ticket categories, infer one of: 'Network issues', 'Hardware', 'Software', 'Account access'.
5. Tools give LIVE data — prefer them over the Context snapshot for anything real-time.

Context from system (background knowledge — may be outdated):
{context}

ANSWER RULES:
1. For live/personal/action requests → use the appropriate tool.
2. For general IT/Project questions → answer from Context.
3. If not in Context and not related to the project → politely decline.
4. Keep responses concise and professional in Arabic or English based on the user's language."""

        limited_history = request.chat_history[-MAX_HISTORY_MESSAGES:]
        history_messages = []
        for msg in limited_history:
            if msg["role"] == "user":
                history_messages.append(("human", msg["content"]))
            elif msg["role"] == "assistant":
                history_messages.append(("ai", msg["content"]))

        messages = [("system", system_prompt)] + history_messages + [("human", request.query)]

        response = await agent_llm.ainvoke(messages)

        if response.tool_calls:
            messages.append(response)

            for tool_call in response.tool_calls:
                selected_tool = next(t for t in tools if t.name == tool_call["name"])
                result = selected_tool.invoke(tool_call["args"])
                messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))

            final_response = await agent_llm.ainvoke(messages)
            final_text = final_response.content

            if isinstance(final_text, list):
                final_text = " ".join([i.get("text", "") for i in final_text if "text" in i])

            return {"response": final_text, "role_used": request.user_role, "action_taken": True}

        else:
            final_text = response.content
            if isinstance(final_text, list):
                final_text = " ".join([i.get("text", "") for i in final_text if "text" in i])

            return {"response": final_text, "role_used": request.user_role, "action_taken": False}

    except Exception as e:
        import traceback
        print("\n❌ Internal Error:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(allocation_router)