from langchain_core.tools import tool
from pymongo import MongoClient
from bson.objectid import ObjectId
import datetime
import os
from dotenv import load_dotenv

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
client["test"]


client = MongoClient(os.getenv("MONGO_URI"))
db = client["test"]

# -----------------------------------------
# Tool 1: Create a Ticket (متاحة للكل)
# -----------------------------------------
@tool
def create_ticket(title: str, description: str, priority: str, category: str, user_role: str, user_id: str, company_id: str, assign_to_name: str = None) -> str:
    """Creates a new IT support ticket."""
    valid_priorities = ["low", "medium", "high", "critical"]
    db_priority = priority.lower() if priority.lower() in valid_priorities else "low"

    valid_categories = ["Network issues", "Hardware", "Software", "Account access"]
    db_category = category if category in valid_categories else "Hardware"

    new_ticket = {
        "name": title,
        "description": description,
        "priority": db_priority,
        "category": db_category,
        "status": "open", 
        "created_by": ObjectId(user_id),
        "company_id": ObjectId(company_id),
        "attachments": [],
        "history": [],
        "status_changed_at": None,
        "createdAt": datetime.datetime.utcnow(), 
        "updatedAt": datetime.datetime.utcnow(), 
        "__v": 0
    }

    assigned_msg = ""
    # 🔒 حماية: الإدمن والمانيجر بس اللي يحددوا التيكت تروح لمين، اليوزر العادي التيكت بتاعته تتفتح وتتساب للسيستم يوزعها
    if assign_to_name and user_role in ["admin", "manager"]:
        assignee = db.users.find_one({"name": {"$regex": assign_to_name, "$options": "i"}, "company_id": ObjectId(company_id)})
        if assignee:
            new_ticket["assign_to"] = assignee["_id"]
            assigned_msg = f" and assigned to {assignee.get('name', 'Unknown')}"
        else:
            assigned_msg = f" (Warning: employee '{assign_to_name}' not found)"

    db.tickets.insert_one(new_ticket)
    return f"✅ Ticket '{title}' | Priority: {db_priority} | Category: {db_category} | Status: open{assigned_msg}."

# -----------------------------------------
# Tool 2: Manage Stock (حساسة - للإدمن والمدير فقط)
# -----------------------------------------
@tool
def manage_stock(item_name: str, quantity: int, action: str, user_role: str, user_id: str, company_id: str) -> str:
    """Adds or removes items from the inventory stock."""
    # 🔒 حماية الصلاحيات
    if user_role not in ["admin", "manager"]:
        return "❌ Access Denied: Only Admins and Managers can add or remove stock. You can only view it."

    if action not in ["add", "remove"]:
        return "❌ Error: Action must be 'add' or 'remove'."

    item = db.stocks.find_one({"name": {"$regex": item_name, "$options": "i"}, "company_id": ObjectId(company_id)})
    
    if not item:
        if action == "add":
            db.stocks.insert_one({
                "name": item_name,
                "quantity": quantity,
                "category": "General",
                "company_id": ObjectId(company_id),
                "createdAt": datetime.datetime.utcnow(),
                "updatedAt": datetime.datetime.utcnow()
            })
            return f"✅ Added new item '{item_name}' with quantity {quantity}."
        return f"❌ Error: Item '{item_name}' not found in stock."

    if action == "add":
        db.stocks.update_one({"_id": item["_id"]}, {"$inc": {"quantity": quantity}})
    elif action == "remove":
        if item.get("quantity", 0) < quantity:
            return f"❌ Error: Not enough '{item.get('name', item_name)}' in stock."
        db.stocks.update_one({"_id": item["_id"]}, {"$inc": {"quantity": -quantity}})

    new_qty = item.get("quantity", 0) + quantity if action == "add" else item.get("quantity", 0) - quantity
    return f"✅ {action.capitalize()}ed {quantity} unit(s) of '{item.get('name', item_name)}'. New quantity: {new_qty}."

# -----------------------------------------
# Tool 3: Update Task Status (ذكية - حسب الملكية)
# -----------------------------------------
@tool
def update_task_status(task_name: str, new_status: str, user_role: str, user_id: str, company_id: str) -> str:
    """Updates task status. Must be 'todo', 'in_progress', or 'completed'."""
    valid_statuses = ["todo", "in_progress", "completed"]
    if new_status not in valid_statuses:
        return f"❌ Error: Invalid status '{new_status}'."

    task = db.tasks.find_one({"name": {"$regex": task_name, "$options": "i"}, "company_id": ObjectId(company_id)})
    if not task:
        return f"❌ Error: Could not find a task matching '{task_name}'."

    # 🔒 حماية الصلاحيات: اليوزر العادي ميقدرش يعدل تاسك مش بتاعته
    if user_role == "user" and ObjectId(user_id) not in task.get("assigned_to", []):
        return "❌ Access Denied: You cannot update a task that is not assigned to you."

    old_status = task.get("status", "todo")
    db.tasks.update_one(
        {"_id": task["_id"]}, 
        {"$set": {"status": new_status, "status_changed_at": datetime.datetime.utcnow(), "updatedAt": datetime.datetime.utcnow()}}
    )
    return f"✅ Task '{task.get('name', task_name)}' updated: '{old_status}' → '{new_status}'."

# -----------------------------------------
# Tool 4: View Stock Inventory (متاحة للكل للرؤية)
# -----------------------------------------
@tool
def get_inventory(user_role: str, user_id: str, company_id: str) -> str:
    """Retrieves the current stock inventory."""
    stock_items = list(db.stocks.find({"company_id": ObjectId(company_id)}))
    if not stock_items:
        return "The inventory is currently empty."

    report_lines = ["📦 Current Inventory Stock:"]
    for item in stock_items:
        report_lines.append(f"  - {item.get('name', 'Unknown')} | Qty: {item.get('quantity', 0)}")
    return "\n".join(report_lines)

# -----------------------------------------
# Tool 5: Search Employee Info (تحجيم البيانات لليوزر العادي)
# -----------------------------------------
@tool
def search_employee(name_query: str, user_role: str, user_id: str, company_id: str) -> str:
    """Searches for an employee by name."""
    employees = list(db.users.find({"name": {"$regex": name_query, "$options": "i"}, "company_id": ObjectId(company_id)}))
    if not employees:
        return f"No employee found matching '{name_query}'."

    report = ["👥 Found employees:"]
    for emp in employees:
        # 🔒 حماية: الإدمن يشوف الإيميل والرول، اليوزر يشوف الاسم والقسم بس
        if user_role in ["admin", "manager"]:
            report.append(f"  - {emp.get('name')} | Role: {emp.get('role', 'user')} | Email: {emp.get('email')} | Dept: {emp.get('dept', 'N/A')}")
        else:
            report.append(f"  - {emp.get('name')} | Dept: {emp.get('dept', 'N/A')}")
            
    return "\n".join(report)

# -----------------------------------------
# Tool 6: Get My Tasks (خاصة باليوزر نفسه)
# -----------------------------------------
@tool
def get_my_tasks(user_role: str, user_id: str, company_id: str) -> str:
    """Retrieves tasks assigned to the user."""
    uid = ObjectId(user_id)
    tasks = list(db.tasks.find({"assigned_to": uid, "company_id": ObjectId(company_id)}))
    
    if not tasks:
        return "You currently have no tasks assigned to you."

    report = ["📋 Your assigned tasks:"]
    for t in tasks:
        report.append(f"  - '{t.get('name', 'Unknown')}' | Status: {t.get('status', 'todo')} | Priority: {t.get('priority', 'medium')}")
    return "\n".join(report)

# -----------------------------------------
# Tool 7: Get Sprint Status (متاحة للكل)
# -----------------------------------------
@tool
def get_sprint_status(sprint_name: str, user_role: str, user_id: str, company_id: str) -> str:
    """Provides a summary of sprint progress."""
    sprint = db.sprints.find_one({"name": {"$regex": sprint_name, "$options": "i"}, "company_id": ObjectId(company_id)})
    if not sprint:
        return f"Could not find a sprint named '{sprint_name}'."

    tasks = list(db.tasks.find({"sprint_id": sprint["_id"]}))
    if not tasks:
        return f"No tasks found for sprint '{sprint.get('name', sprint_name)}'."

    stats = {"completed": 0, "in_progress": 0, "todo": 0}
    for t in tasks:
        status = t.get("status", "todo")
        if status in stats: stats[status] += 1

    total = len(tasks)
    done_pct = int((stats.get("completed", 0) / total) * 100) if total else 0

    return f"📊 Sprint '{sprint.get('name', sprint_name)}' Progress: {done_pct}% complete.\n  - To Do: {stats['todo']}\n  - In Progress: {stats['in_progress']}\n  - Completed: {stats['completed']}"

# -----------------------------------------
# Tool 8: Get My Tickets (خاصة باليوزر)
# -----------------------------------------
@tool
def get_my_tickets(user_role: str, user_id: str, company_id: str) -> str:
    """Retrieves tickets created by or assigned to the user."""
    uid = ObjectId(user_id)
    tickets = list(db.tickets.find({
        "company_id": ObjectId(company_id),
        "$or": [{"created_by": uid}, {"assign_to": uid}]
    }))
    if not tickets:
        return "You have no tickets created by or assigned to you."

    report = ["🎫 Your tickets:"]
    for t in tickets:
        label = "Created" if str(t.get("created_by")) == user_id else "Assigned"
        report.append(f"  - [{label}] '{t.get('name', 'Unknown')}' | Status: {t.get('status', 'open')}")
    return "\n".join(report)

# -----------------------------------------
# Tool 9: Get Team Report (حساسة - للإدمن والمدير فقط)
# -----------------------------------------
@tool
def get_team_report(user_role: str, user_id: str, company_id: str) -> str:
    """Full report of team members and their active tasks."""
    # 🔒 حماية الصلاحيات: منع اليوزر العادي من التجسس على زمايله
    if user_role not in ["admin", "manager"]:
        return "❌ Access Denied: Only Admins and Managers can view the full team report."

    members = list(db.users.find({"company_id": ObjectId(company_id)}))
    if not members:
        return "No team members found."

    report = ["👥 Team Workload Report:"]
    for member in members:
        tasks = list(db.tasks.find({"assigned_to": member["_id"], "status": "in_progress"}))
        tasks_str = ", ".join([f"'{t.get('name')}'" for t in tasks]) if tasks else "No active tasks"
        report.append(f"\n  👤 {member.get('name', 'Unknown')} ({member.get('role', 'user')})\n     Working on: {tasks_str}")

    return "\n".join(report)

# -----------------------------------------
# Tool 10: Update Ticket Status (حساسة)
# -----------------------------------------
@tool
def update_ticket_status(ticket_name: str, new_status: str, user_role: str, user_id: str, company_id: str) -> str:
    """Updates ticket status. MUST be 'open', 'in_progress', 'resolved', 'closed'."""
    # 🔒 حماية الصلاحيات: تعديل حالة التيكت للإدمن والمانيجر بس
    if user_role not in ["admin", "manager"]:
         return "❌ Access Denied: Only Admins and IT Managers can change a ticket's status."

    valid_statuses = ["open", "in_progress", "resolved", "closed"]
    if new_status not in valid_statuses:
         return f"❌ Error: Invalid status '{new_status}'."

    ticket = db.tickets.find_one({"name": {"$regex": ticket_name, "$options": "i"}, "company_id": ObjectId(company_id)})
    if not ticket:
        return f"❌ Error: Could not find a ticket matching '{ticket_name}'."

    old_status = ticket.get("status", "open")
    db.tickets.update_one(
        {"_id": ticket["_id"]}, 
        {"$set": {"status": new_status, "status_changed_at": datetime.datetime.utcnow(), "updatedAt": datetime.datetime.utcnow()}}
    )
    return f"✅ Success: Ticket '{ticket.get('name', ticket_name)}' status updated: '{old_status}' → '{new_status}'."