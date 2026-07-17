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















def _get_team_member_ids(team_id: str) -> set:
    """FIX: membership lives in the team document's `members` array,
    not in a `team_id` field on the user. Returns member ids as strings."""
    ids = set()
    if not team_id:
        return ids
    try:
        team = db.teams.find_one({"_id": ObjectId(team_id)})
    except Exception:
        team = None
    if team:
        for m in team.get("members", []):
            if isinstance(m, dict):
                m = m.get("user") or m.get("user_id") or m.get("_id")
            if m:
                ids.add(str(m))
    return ids


def _get_best_candidate(text_to_match: str, team_id: str = None, company_id: str = None, allowed_types: list = None, excluded_types: list = None, target_dep: str = None) -> str:
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    team_member_ids = _get_team_member_ids(team_id)

    users = list(db.users.find())
    candidates = []
    present_candidates = []
    all_candidates = []
    
    for user_info in users:
        user_id = user_info["_id"]
        
        if not user_info.get("active", True):
            continue

        if company_id and str(user_info.get("company_id")) != str(company_id):
            continue

        # FIX: a user belongs to the team if their id is in the team's
        # members array OR (legacy) they carry a matching team_id field.
        if team_id:
            in_team = (
                str(user_id) in team_member_ids
                or str(user_info.get("team_id")) == str(team_id)
            )
            if not in_team:
                continue
            
        # ✅ قراءة القسم صح (dept أو dep)
        user_dept = str(user_info.get("dept", user_info.get("dep", ""))).lower().strip()
        if target_dep and user_dept != target_dep.lower():
            continue
            
        if allowed_types and user_info.get("role") not in allowed_types:
            continue
            
        if excluded_types and user_info.get("role") in excluded_types:
            continue

        # ✅ حساب الشغل الحالي بدقة
        active_tasks = db.tasks.count_documents({
            "assigned_to": ObjectId(user_id),
            "status": {"$in": ["todo", "in_progress", "in-progress"]}
        })

        active_tickets = db.tickets.count_documents({
            "assign_to": ObjectId(user_id),
            "status": {"$in": ["open", "in_progress", "in-progress"]}
        })

        profile = db.ai_employee_profile.find_one({"user_id": user_id})
        history_text = profile.get("solved_history_text", "general support") if profile else "general support"
            
        candidate_data = {
            "user_id": user_id,
            "solved_history": history_text,
            "active_tasks": active_tasks + active_tickets
        }
        
        all_candidates.append(candidate_data)
        
        is_present = db.schedules.find_one({
            "user_id": user_id,
            "entries": {
                "$elemMatch": {
                    "date": {"$gte": today_start, "$lt": today_end},
                    "shift_type": {"$in": ["morning", "afternoon", "night", "arrived"]}
                }
            }
        })
        
        if is_present:
            present_candidates.append(candidate_data)

    candidates = present_candidates if present_candidates else all_candidates

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
        
        # ✅ استدعاء العقل المدبر صح للتاسكات (مفيش target_dep هنا)
        best_user_id = _get_best_candidate(task_text, team_id=team_id, company_id=task.get("company_id"))

        if not best_user_id:
            return {"success": False, "msg": "No available and present employees found in this team."}

        now_utc = datetime.now(timezone.utc)
        
        db.workingtasks.insert_one({
            "custom_id": generate_custom_id("wt_ai"), 
            "task_id": task["_id"],
            "user_id": best_user_id,
            "company_id": task.get("company_id"),
            "start_date": now_utc,
            "end_date": None,
            "status": "active"
        })
        db.tasks.update_one({"_id": task["_id"]}, {"$set": {"assigned": True, "status": "todo", "assigned_to": [ObjectId(best_user_id)]}})
        
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
        
        # 🔥 تمرير الشركة واستثناء المانجر وتحديد القسم
        best_user_id = _get_best_candidate(ticket_text, company_id=ticket.get("company_id"), excluded_types=["manager"], target_dep="it")
        

        if not best_user_id:
            return {"success": False, "msg": "No available staff found to handle this ticket."}

        now_utc = datetime.now(timezone.utc)
        db.workingtasks.insert_one({
            "custom_id": generate_custom_id("wt_ai"), 
            "ticket_id": ticket["_id"],
            "user_id": best_user_id,
            "company_id": ticket.get("company_id"),
            "start_date": now_utc,
            "end_date": None,
            "status": "active"
        })
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

class BulkTaskAssignRequest(BaseModel):
    task_ids: list[str]
    team_id: str 

@router.post("/api/ai/assign-bulk-tasks")
async def api_assign_bulk_tasks(req: BulkTaskAssignRequest):
    results = []
    assigned_count = 0
    for tid in req.task_ids:
        res = allocate_task_to_best_employee(tid, req.team_id)
        results.append({"task_id": tid, "result": res})
        if res.get("success"):
            assigned_count += 1

    # FIX: previously this returned HTTP 200 "Processed N tasks." even when
    # every allocation failed, so the frontend showed success with 0 assigned.
    if req.task_ids and assigned_count == 0:
        first_msg = results[0]["result"].get("msg", "Assignment failed.")
        raise HTTPException(
            status_code=400,
            detail=f"No tasks were assigned. Reason: {first_msg}",
        )

    return {
        "message": f"Assigned {assigned_count} of {len(req.task_ids)} tasks.",
        "assigned_count": assigned_count,
        "details": results,
    }

class TicketAssignRequest(BaseModel):
    ticket_id: str

@router.post("/api/ai/assign-ticket")
async def api_assign_ticket(req: TicketAssignRequest):
    result = allocate_ticket_to_it(req.ticket_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["msg"])
    return {"message": result["msg"], "assigned_user_id": result["assigned_to"]}

class AutoAssignRequest(BaseModel):
    company_id: str

@router.post("/api/ai/auto-assign")
async def api_auto_assign(req: AutoAssignRequest):
    # Find all open tickets for this company that do not have an assignee
    unassigned_tickets = list(db.tickets.find({
        "company_id": ObjectId(req.company_id),
        "status": "open",
        "$or": [
            {"assign_to": {"$exists": False}},
            {"assign_to": None}
        ]
    }))

    results = []
    assigned_count = 0
    for t in unassigned_tickets:
        tid = str(t["_id"])
        res = allocate_ticket_to_it(tid)
        results.append({"ticket_id": tid, "result": res})
        if res.get("success"):
            assigned_count += 1
            
    return {
        "message": "Auto-assignment complete.", 
        "assigned_count": assigned_count,
        "details": results
    }

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
    now_utc = datetime.now(timezone.utc)
    
    if req.work_type == "task":
        db.tasks.update_one({"_id": ObjectId(req.work_id)}, {"$set": {"status": "completed"}})
        task = db.tasks.find_one({"_id": ObjectId(req.work_id)})
        if task:
            text_content = f"{task.get('name','')} {task.get('description', '')}"
            
        db.workingtasks.update_many(
            {"task_id": ObjectId(req.work_id)},
            {"$set": {"status": "completed", "end_date": now_utc}}
        )
            
    elif req.work_type == "ticket":
        db.tickets.update_one({"_id": ObjectId(req.work_id)}, {"$set": {"status": "closed"}})
        ticket = db.tickets.find_one({"_id": ObjectId(req.work_id)})
        if ticket:
            text_content = f"{ticket.get('name','')} {ticket.get('description', '')}"
            
        db.workingtasks.update_many(
            {"ticket_id": ObjectId(req.work_id)},
            {"$set": {"status": "completed", "end_date": now_utc}}
        )

    if text_content:
        background_tasks.add_task(learn_from_completion, req.user_id, text_content)

    return {"message": "Work marked as completed. AI profile updated!"}

@router.post("/api/ai/trigger-stock-check")
async def api_trigger_stock(background_tasks: BackgroundTasks):
    background_tasks.add_task(predict_stock_with_meta)
    return {"message": "Meta Prophet AI started checking stock in the background."}

@router.get("/api/ai/stock-predictions")
async def api_get_stock_predictions(company_id: str = None):
    try:
        results = predict_stock_with_meta(company_id)
        return {
            "success": True, 
            "message": "Stock predictions generated successfully.", 
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/ai/debug-stocks")
async def debug_stocks():
    """Temporary debug endpoint to inspect stock company_ids"""
    stocks = list(db.stocks.find({}, {"_id": 1, "name": 1, "company_id": 1, "quantity": 1}))
    result = []
    for s in stocks:
        result.append({
            "stock_id": str(s["_id"]),
            "name": s.get("name", "?"),
            "company_id": str(s.get("company_id", "MISSING")),
            "company_id_type": type(s.get("company_id")).__name__,
            "quantity": s.get("quantity", 0)
        })
    return {"total": len(result), "stocks": result}

class HelpSolveRequest(BaseModel):
    details: str = ""
    context: str = "project management task"
    item_id: str = None
    item_type: str = "task" # "task" or "ticket"

@router.post("/api/ai/help-solve")
async def api_help_solve(req: HelpSolveRequest):
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        import os, random

        keys = os.getenv("GOOGLE_API_KEYS")
        if keys:
            key_list = [k.strip() for k in keys.split(",") if k.strip()]
            if key_list:
                os.environ["GOOGLE_API_KEY"] = random.choice(key_list)

        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7)
        
        task_info = req.details
        
        # لو اليوزر باعت ID، هنسحب الداتا بتاعت التاسك أو التيكت من الداتا بيز عشان الـ AI يفهم السياق بالكامل
        if req.item_id:
            if req.item_type == "task":
                item = db.tasks.find_one({"_id": ObjectId(req.item_id)})
            elif req.item_type == "ticket":
                item = db.tickets.find_one({"_id": ObjectId(req.item_id)})
            else:
                item = None
                
            if item:
                task_info = f"Title: {item.get('name')}\nDescription: {item.get('description', 'No description')}\nPriority: {item.get('priority', 'Normal')}\nStatus: {item.get('status', 'Open')}"
                if not req.details:
                    req.details = "Please solve this database item."
            else:
                return {"success": False, "message": "Item not found in database."}

        if not task_info.strip():
            return {"success": False, "message": "Please provide details or a valid item_id."}

        prompt = f"""
        You are a highly experienced Senior Project Manager and Technical Lead.
        A team member has asked for help on the following task or problem:
        
        {task_info}
        
        Additional User Notes/Questions: "{req.details}"
        Context: {req.context}
        
        Please provide a clear, step-by-step actionable guide to solve this task. 
        Break down the problem, suggest tools or methodologies if applicable, and outline the exact steps they should take to complete it successfully. Keep your response professional, encouraging, and formatted in Markdown.
        """
        response = llm.invoke(prompt)
        return {
            "success": True,
            "solution": response.content
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ExtractStockRequest(BaseModel):
    task_id: str
    company_id: str

@router.post("/api/ai/extract-stock-usage")
async def api_extract_stock_usage(req: ExtractStockRequest):
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        import json
        import os, random

        keys = os.getenv("GOOGLE_API_KEYS")
        if keys:
            key_list = [k.strip() for k in keys.split(",") if k.strip()]
            if key_list:
                os.environ["GOOGLE_API_KEY"] = random.choice(key_list)

        # 1. Fetch Task/Ticket Comment
        task = db.tasks.find_one({"_id": ObjectId(req.task_id)})
        comment = ""
        if task:
            comment = task.get("comment", "")
        else:
            ticket = db.tickets.find_one({"_id": ObjectId(req.task_id)})
            if ticket:
                comment = ticket.get("resolution", "")
            else:
                return {"success": False, "message": "Task/Ticket not found."}
            
        if not comment:
            return {"success": False, "message": "No comment/resolution found.", "used_items": []}

        # 2. Fetch Company Stock
        stocks = list(db.stocks.find({"company_id": ObjectId(req.company_id)}, {"name": 1}))
        stock_names = [s["name"] for s in stocks]
        
        if not stock_names:
            return {"success": False, "message": "No stock available in company.", "used_items": []}

        # 3. Ask AI to extract usage based on the actual stock list
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
        prompt = f"""
        You are a smart inventory assistant.
        The employee wrote the following comment after completing a task:
        "{comment}"
        
        The company's available stock items are:
        {stock_names}
        
        Did the employee mention consuming or using any of these stock items? 
        If yes, extract the items and their quantities. If they mention an item that roughly matches our stock names, map it to our exact stock name.
        Return ONLY a valid JSON array of objects, like this:
        [
            {{"item_name": "exact stock name", "quantity": 1}}
        ]
        If no items were used, return []. Do not include markdown formatting or backticks around the JSON.
        """
        response = llm.invoke(prompt)
        
        try:
            # Clean up the response in case it has markdown ticks
            clean_json = response.content.strip().strip('```json').strip('```').strip()
            used_items = json.loads(clean_json)
        except:
            used_items = []

        return {
            "success": True,
            "used_items": used_items
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class BreakdownRequest(BaseModel):
    description: str

@router.post("/api/ai/breakdown-task")
async def api_breakdown_task(req: BreakdownRequest):
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        import json
        import os, random

        keys = os.getenv("GOOGLE_API_KEYS")
        if keys:
            key_list = [k.strip() for k in keys.split(",") if k.strip()]
            if key_list:
                os.environ["GOOGLE_API_KEY"] = random.choice(key_list)

        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7)
        prompt = f"""
        You are an expert Agile Scrum Master and Technical Lead. 
        A manager has provided the following high-level description for a project or large task:
        "{req.description}"
        
        Break this down into small, actionable tasks that can be assigned to team members.
        For each task, provide a "name" (short title), a "description", and a recommended "priority" (high, medium, low).
        
        Return ONLY a valid JSON array of objects, like this:
        [
            {{"name": "Task Title", "description": "Detailed description...", "priority": "high"}}
        ]
        Do not include markdown formatting or backticks around the JSON.
        """
        response = llm.invoke(prompt)
        
        try:
            clean_json = response.content.strip().strip('```json').strip('```').strip()
            tasks = json.loads(clean_json)
        except:
            return {"success": False, "message": "Failed to parse AI response.", "raw_response": response.content}

        return {
            "success": True,
            "tasks": tasks
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from typing import List, Optional

class TaskItem(BaseModel):
    name: str
    description: str
    priority: str
    assigned_to: Optional[str] = None
    backlog_id: str # ObjectId string






class BulkCreateTasksRequest(BaseModel):
    company_id: str
    created_by: str
    team_id: str = None  # ✅ ضفنا التيم عشان الفرونت إند يبعته
    auto_assign: bool = False
    tasks: List[TaskItem]

@router.post("/api/ai/bulk-create-tasks")
async def api_bulk_create_tasks(req: BulkCreateTasksRequest):
    try:
        from datetime import datetime
        new_tasks = []
        for t in req.tasks:
            assigned = bool(t.assigned_to)
            task_doc = {
                "custom_id": generate_custom_id("tsk"),
                "name": t.name,
                "description": t.description,
                "priority": t.priority,
                "status": "todo",
                "assigned_to": [ObjectId(t.assigned_to)] if assigned else [],
                "assigned": assigned,
                "backlog_id": ObjectId(t.backlog_id),
                "created_by": ObjectId(req.created_by),
                "company_id": ObjectId(req.company_id),
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow()
            }
            new_tasks.append(task_doc)
            
        if new_tasks:
            db.tasks.insert_many(new_tasks)
            if req.auto_assign:
                for doc in new_tasks:
                    if not doc.get("assigned"):
                        # ✅ التعديل هنا: نبعت التيم اللي اليوزر شغال فيه بدل None
                        allocate_task_to_best_employee(str(doc["_id"]), team_id=req.team_id)
            
        return {"success": True, "message": f"Successfully created {len(new_tasks)} tasks."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))