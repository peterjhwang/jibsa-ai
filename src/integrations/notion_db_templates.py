"""
Database templates — sensible default schemas for common Notion databases.

Used by NotionSecondBrain._ensure_db() when auto-creating databases under
the parent page. Templates are suggestions, not requirements — the LLM can
also propose custom schemas via the create_database action.

Each template maps to Notion API `databases.create()` property format.
"""

DB_TEMPLATES: dict[str, dict] = {
    "Tasks": {
        "properties": {
            "Name": {"title": {}},
            "Status": {
                "status": {
                    "options": [
                        {"name": "To Do", "color": "default"},
                        {"name": "In Progress", "color": "blue"},
                        {"name": "Done", "color": "green"},
                    ],
                    "groups": [
                        {"name": "To-do", "option_names": ["To Do"]},
                        {"name": "In progress", "option_names": ["In Progress"]},
                        {"name": "Complete", "option_names": ["Done"]},
                    ],
                },
            },
            "Priority": {
                "select": {
                    "options": [
                        {"name": "High", "color": "red"},
                        {"name": "Medium", "color": "yellow"},
                        {"name": "Low", "color": "green"},
                    ],
                },
            },
            "Due Date": {"date": {}},
        },
        "keywords": ["task", "todo", "action", "do", "to-do"],
    },
    "Projects": {
        "properties": {
            "Name": {"title": {}},
            "Status": {
                "status": {
                    "options": [
                        {"name": "Not Started", "color": "default"},
                        {"name": "In Progress", "color": "blue"},
                        {"name": "Done", "color": "green"},
                    ],
                    "groups": [
                        {"name": "To-do", "option_names": ["Not Started"]},
                        {"name": "In progress", "option_names": ["In Progress"]},
                        {"name": "Complete", "option_names": ["Done"]},
                    ],
                },
            },
            "End Date": {"date": {}},
        },
        "keywords": ["project", "build", "launch", "initiative"],
    },
    "Notes": {
        "properties": {
            "Name": {"title": {}},
            "Tags": {
                "multi_select": {
                    "options": [],
                },
            },
        },
        "keywords": ["note", "knowledge", "idea", "resource", "info"],
    },
    "Journal Entries": {
        "properties": {
            "Name": {"title": {}},
            "Date": {"date": {}},
        },
        "keywords": ["journal entry", "diary", "entry", "reflection"],
    },
    "Expense Record": {
        "properties": {
            "Name": {"title": {}},
            "Amount": {"number": {"format": "number"}},
            "Date": {"date": {}},
            "Note": {"rich_text": {}},
        },
        "keywords": ["expense", "spending", "spent", "cost", "purchase"],
    },
    "Workouts": {
        "properties": {
            "Name": {"title": {}},
            "Date": {"date": {}},
            "Duration (min)": {"number": {"format": "number"}},
            "Note": {"rich_text": {}},
        },
        "keywords": ["workout", "gym", "training", "exercise", "fitness"],
    },
    "Interns": {
        "properties": {
            "Name": {"title": {}},
            "Role": {"rich_text": {}},
            "Responsibilities": {"rich_text": {}},
            "Tone": {"rich_text": {}},
            "Tools Allowed": {
                "multi_select": {
                    "options": [
                        {"name": "notion"},
                        {"name": "web_search"},
                        {"name": "code_exec"},
                    ],
                },
            },
            "Autonomy Rules": {"rich_text": {}},
            "Created By": {"rich_text": {}},
            "Active": {"checkbox": {}},
        },
        "keywords": ["intern", "hire", "jd"],
    },
    "Contacts": {
        "properties": {
            "Name": {"title": {}},
            "Email": {"email": {}},
            "Company": {"rich_text": {}},
            "Note": {"rich_text": {}},
        },
        "keywords": ["contact", "person", "people", "crm"],
    },
}
