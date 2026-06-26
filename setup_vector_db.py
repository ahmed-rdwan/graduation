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

# تجهيز موديل تحويل النصوص لأرقام بتاع جوجل
embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")

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
    for item in db["stocks"].find():
        content = f"Inventory Item: {item.get('name', 'Unknown')}, Category: {item.get('category', 'General')}, Quantity available: {item.get('quantity', 0)}"
        documents.append(Document(page_content=content, metadata={"source_id": str(item["_id"]), "type": "stock"}))

    print(f"Successfully extracted {len(documents)} documents.")
    
    # حماية من الداتا بيز الفاضية
    if len(documents) == 0:
        print("⚠️ Warning: MongoDB is completely empty. Adding a dummy document to initialize ChromaDB.")
        documents.append(Document(
            page_content="This is a system initialized state. No data is available yet.", 
            metadata={"source_id": "dummy_0", "type": "system"}
        ))

    print("Embedding in small batches to bypass Google limits...")
    
    # تعريف الذاكرة
    vector_db = Chroma(embedding_function=embeddings, persist_directory="./chroma_db")
    
    # تقسيم الملفات عشان جوجل (Batching)
    batch_size = 15 
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i+batch_size]
        vector_db.add_documents(batch)
        print(f"✅ Embedded {min(i + batch_size, len(documents))} / {len(documents)}...")
        
        # وقت راحة للـ API عشان ميضربش Rate Limit
        if i + batch_size < len(documents):
            time.sleep(15)

    print("🎉 Database setup complete and embedded successfully with Google!")

if __name__ == "__main__":
    setup_database()
