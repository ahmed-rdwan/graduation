import re

with open("agent_tools.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add the helper function
helper = """
def get_next_custom_id(name: str, prefix: str) -> str:
    counter = db.counters.find_one_and_update(
        {"name": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return f"{prefix}{counter['seq']}"

"""
if "get_next_custom_id" not in content:
    content = content.replace('db = client["test"]', 'db = client["test"]\n' + helper)

# Replace ticket
content = re.sub(
    r'(new_ticket\s*=\s*\{)',
    r'\1\n        "custom_id": get_next_custom_id("ticket", "tkt_"),',
    content
)

# Replace project
content = re.sub(
    r'(db\.projects\.insert_one\(\{)',
    r'\1\n        "custom_id": get_next_custom_id("project", "prj_"),',
    content
)

# Replace team
content = re.sub(
    r'(db\.teams\.insert_one\(\{)',
    r'\1\n        "custom_id": get_next_custom_id("team", "tm_"),',
    content
)

# Replace backlog
content = re.sub(
    r'(db\.backlogs\.insert_one\(\{)',
    r'\1\n        "custom_id": get_next_custom_id("backlog", "bkl_"),',
    content
)

# Replace task
content = re.sub(
    r'(db\.tasks\.insert_one\(\{)',
    r'\1\n        "custom_id": get_next_custom_id("task", "tsk_"),',
    content
)

# Replace sprint
content = re.sub(
    r'(db\.sprints\.insert_one\(\{)',
    r'\1\n        "custom_id": get_next_custom_id("sprint", "spr_"),',
    content
)

# Replace stock (let's check if stock needs one, in Node.js it doesn't seem to have a Counter. We will ignore stock for now, it didn't fail earlier anyway)

with open("agent_tools.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied!")
