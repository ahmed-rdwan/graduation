import re
import uuid
import numpy as np
from datetime import datetime, timedelta, timezone
from bson.objectid import ObjectId
from pymongo import MongoClient
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from ai_prophet_predictor import predict_stock_with_meta

import os
from dotenv import load_dotenv

router = APIRouter()
load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
db = client["test"]

# ====================================================
# 🛠️ Helper: Production-Grade Custom ID Generator
# ====================================================
def generate_custom_id(prefix: str) -> str:
    """يولد ID مستحيل يتكرر باستخدام الوقت وكود عشوائي"""
    timestamp = int(datetime.now(timezone.utc).timestamp())
    short_uuid = uuid.uuid4().hex[:6]
    return f"{prefix}_{timestamp}_{short_uuid}"

# ====================================================
# 🧠 الخوارزمية الأساسية (The Brain) - Dynamic Load Balancing
# ====================================================

def _get_best_candidate(text_to_match: str, team_id: str = None, allowed_types: list = None) -> str:
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    profiles = list(db.ai_employee_profile.find())
    candidates = []
    
    for profile in profiles:
        user_id = profile["user_id"]
        user_info = db.users.find_one({"_id": user_id})
        
        # تجاهل الموظف لو مش موجود أو حسابه مقفول
        if not user_info or not user_info.get("active", True):
            continue

        if team_id and str(user_info.get("team_id")) != str(team_id):
            continue
            
        if allowed_types and user_info.get("role") not in allowed_types:
            continue
            
        is_present = db.schedules.find_one({
            "user_id": user_id,
            "entries": {
                "$elemMatch": {
                    "date": {"$gte": today_start, "$lt": today_end},
                    "shift_type": "arrived"
                }
            }
        })
        
        if not is_present:
            continue 
            
        active_tasks = db.workingtasks.count_documents({"user_id": user_id})
            
        candidates.append({
            "user_id": user_id,
            "solved_history": profile.get("solved_history_text", "general support"),
            "active_tasks": active_tasks
        })

    if not candidates:
        return None

    history_texts = [c["solved_history"] for c in candidates]
    corpus = history_texts + [text_to_match]
    
    vectorizer = TfidfVectorizer(stop_words='english')
    try:
        tfidf_matrix = vectorizer.fit_transform(corpus)
        item_vector = tfidf_matrix[-1]
        history_vectors = tfidf_matrix[:-1]
        similarities = cosine_similarity(item_vector, history_vectors).flatten()
    except ValueError:
        similarities = np.zeros(len(candidates))

    final_scores = []
    for idx, candidate in enumerate(candidates):
        sim_score = similarities[idx]
        load_penalty = candidate["active_tasks"] * 0.20 
        final_scores.append(sim_score - load_penalty)

    best_idx = np.argmax(final_scores)
    return candidates[best_idx]["user_id"]

def allocate_task_to_best_employee(task_id: str, team_id: str) -> dict:
    try:
        task = db.tasks.find_one({"_id": ObjectId(task_id)})
        if not task or task.get("assigned"):
            return {"success": False, "msg": "Task not found or already assigned."}

        task_text = f"{task.get('name', '')} {task.get('description', '')}".lower()
        
        best_user_id = _get_best_candidate(task_text, team_id=team_id)
        
        if not best_user_id:
            return {"success": False, "msg": "No available and present employees found in this team."}

        now_utc = datetime.now(timezone.utc)
        
        # إضافة التاسك بـ ID برودكشن
        db.workingtasks.insert_one({
            "custom_id": generate_custom_id("wt_ai"), 
            "task_id": task["_id"],
            "user_id": best_user_id,
            "company_id": task.get("company_id"),
            "start_date": now_utc,
            "end_date": now_utc 
        })
        db.tasks.update_one({"_id": task["_id"]}, {"$set": {"assigned": True, "status": "in_progress"}})
        
        return {"success": True, "msg": "Task successfully assigned.", "assigned_to": str(best_user_id)}
    except Exception as e:
        print(f"❌ Server Error during task allocation: {e}")
        return {"success": False, "msg": "Internal Server Error during allocation."}

def allocate_ticket_to_it(ticket_id: str) -> dict:
    try:
        ticket = db.tickets.find_one({"_id": ObjectId(ticket_id)})
        if not ticket or ticket.get("assign_to"):
            return {"success": False, "msg": "Ticket not found or already assigned."}

        ticket_text = f"{ticket.get('name', '')} {ticket.get('description', '')}".lower()
        
        best_user_id = _get_best_candidate(ticket_text, allowed_types=["admin", "manager", "developer"])
        
        if not best_user_id:
            return {"success": False, "msg": "No available IT staff found to handle this ticket."}

        db.tickets.update_one(
            {"_id": ticket["_id"]}, 
            {"$set": {"assign_to": best_user_id, "status": "in_progress"}}
        )
        return {"success": True, "msg": "Ticket successfully assigned to IT.", "assigned_to": str(best_user_id)}
    except Exception as e:
        print(f"❌ Server Error during ticket allocation: {e}")
        return {"success": False, "msg": "Internal Server Error during allocation."}

def learn_from_completion(user_id: str, text_content: str):
    try:
        clean_words = re.findall(r'\b[a-z]{3,}\b', text_content.lower())
        new_experience = " ".join(clean_words)

        profile = db.ai_employee_profile.find_one({"user_id": ObjectId(user_id)})
        if profile:
            updated_history = f"{profile.get('solved_history_text', '')} {new_experience}"
            db.ai_employee_profile.update_one(
                {"user_id": ObjectId(user_id)},
                {"$set": {"solved_history_text": updated_history}}
            )
    except Exception as e:
        print(f"❌ Learning Error: {e}")

# ====================================================
# 🌐 واجهات الـ APIs
# ====================================================

class TaskAssignRequest(BaseModel):
    task_id: str
    team_id: str 

@router.post("/api/ai/assign-task")
async def api_assign_task(req: TaskAssignRequest):
    result = allocate_task_to_best_employee(req.task_id, req.team_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["msg"])
    return {"message": result["msg"], "assigned_user_id": result["assigned_to"]}

class TicketCreateRequest(BaseModel):
    title: str
    description: str
    priority: str
    created_by_id: str
    company_id: str

@router.post("/api/tickets/create")
async def api_create_ticket(ticket: TicketCreateRequest, background_tasks: BackgroundTasks):
    now_utc = datetime.now(timezone.utc)
    
    new_ticket = {
        "custom_id": generate_custom_id("tkt_ai"),
        "name": ticket.title,
        "description": ticket.description,
        "priority": ticket.priority,
        "status": "open",
        "category": "Software",
        "created_by": ObjectId(ticket.created_by_id),
        "company_id": ObjectId(ticket.company_id),
        "createdAt": now_utc
    }
    result = db.tickets.insert_one(new_ticket)
    ticket_id = str(result.inserted_id)

    background_tasks.add_task(allocate_ticket_to_it, ticket_id)

    return {"message": "Ticket created and is being routed dynamically.", "ticket_id": ticket_id}

class CompleteWorkRequest(BaseModel):
    work_id: str
    work_type: str 
    user_id: str

@router.post("/api/work/complete")
async def api_complete_work(req: CompleteWorkRequest, background_tasks: BackgroundTasks):
    text_content = ""
    
    if req.work_type == "task":
        db.tasks.update_one({"_id": ObjectId(req.work_id)}, {"$set": {"status": "completed"}})
        task = db.tasks.find_one({"_id": ObjectId(req.work_id)})
        if task:
            text_content = f"{task.get('name','')} {task.get('description', '')}"
            
    elif req.work_type == "ticket":
        db.tickets.update_one({"_id": ObjectId(req.work_id)}, {"$set": {"status": "closed"}})
        ticket = db.tickets.find_one({"_id": ObjectId(req.work_id)})
        if ticket:
            text_content = f"{ticket.get('name','')} {ticket.get('description', '')}"

    if text_content:
        background_tasks.add_task(learn_from_completion, req.user_id, text_content)

    return {"message": "Work marked as completed. AI profile updated!"}

@router.post("/api/ai/trigger-stock-check")
async def api_trigger_stock(background_tasks: BackgroundTasks):
    background_tasks.add_task(predict_stock_with_meta)
    return {"message": "Meta Prophet AI started checking stock in the background."}