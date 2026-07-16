import os
import time
from pymongo import MongoClient
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv

# استدعاء موديل جوجل فقط (لا يوجد موديل محلي)
from langchain_google_genai import GoogleGenerativeAIEmbeddings

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

# الاتصال بقاعدة البيانات
client = MongoClient(MONGO_URI)
db = client["test"]

import random

def get_random_embedding_key():
    keys = os.getenv("GOOGLE_EMBEDDING_API_KEYS") or os.getenv("GOOGLE_API_KEYS")
    if keys:
        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        if key_list:
            return random.choice(key_list)
    return os.getenv("GOOGLE_EMBEDDING_API_KEY") or os.getenv("GOOGLE_API_KEY")

def setup_database():
    documents = []
    print("Starting to extract data from MongoDB...")

    # --- 1. Users ---
    for user in db["users"].find():
        content = f"User Name: {user.get('name', 'Unknown')}, Role: {user.get('role', 'user')}, Email: {user.get('email', 'Unknown')}."
        documents.append(Document(page_content=content, metadata={"source_id": str(user["_id"]), "type": "user"}))

    # --- 2. Teams ---
    for team in db["teams"].find():
        content = f"Team Name: {team.get('name', 'Unknown')}, Description: {team.get('description', '')}"
        documents.append(Document(page_content=content, metadata={"source_id": str(team["_id"]), "type": "team"}))

    # --- 3. Projects ---
    for project in db["projects"].find():
        content = f"Project Name: {project.get('name', 'Unknown')}, Description: {project.get('description', '')}"
        documents.append(Document(page_content=content, metadata={"source_id": str(project["_id"]), "type": "project"}))

    # --- 4. Tasks ---
    for task in db["tasks"].find():
        content = f"Task Name: {task.get('name', 'Unknown')}, Priority: {task.get('priority', 'medium')}, Status: {task.get('status', 'todo')}, Description: {task.get('description', '')}"
        documents.append(Document(page_content=content, metadata={"source_id": str(task["_id"]), "type": "task"}))

    # --- 5. Tickets ---
    for ticket in db["tickets"].find():
        content = f"Ticket Name: {ticket.get('name', 'Unknown')}, Problem: {ticket.get('description', '')}, Priority: {ticket.get('priority', 'low')}, Status: {ticket.get('status', 'open')}"
        documents.append(Document(page_content=content, metadata={"source_id": str(ticket["_id"]), "type": "ticket"}))

    # --- 6. Stock Items ---
   
   # مثال لتعديل سطر الـ stocks في setup_vector_db.py
    for item in db["stocks"].find():
        content = f"Inventory Item: {item.get('name', '')}, Quantity: {item.get('quantity', 0)}"
        documents.append(Document(
            page_content=content, 
            metadata={
                "source_id": str(item["_id"]), 
                "type": "stock", 
                "company_id": str(item.get("company_id", "")) # 👈 ده الجزء الأهم
            }
        ))
   
    
# حماية من الداتا بيز الفاضية
    if len(documents) == 0:
        print("⚠️ Warning: MongoDB is completely empty. Adding a dummy document to initialize ChromaDB.")
        documents.append(Document(
            page_content="This is a system initialized state. No data is available yet.", 
            metadata={"source_id": "dummy_0", "type": "system"}
        ))

    print("🚀 Embedding all documents at once (Paid Tier Mode)...")
    
    # اختيار مفتاح (مابقاش في داعي نبدل المفاتيح كتير لأن الليمت بقى مفتوح)
    current_key = get_random_embedding_key()
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-2", google_api_key=current_key)
    
    # بناء الداتا بيز وحقن كل الملفات دفعة واحدة (بدون Loop ولا Sleep)
    vector_db = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory="./chroma_db"
    )
    
    print(f"✅ Successfully embedded all {len(documents)} documents in record time!")

if __name__ == "__main__":
    setup_database()

