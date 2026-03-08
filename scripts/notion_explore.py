"""Quick diagnostic: explore the Second Brain page and list all accessible databases."""
import os
import sys
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()
token = os.environ.get("NOTION_TOKEN")
if not token:
    print("ERROR: NOTION_TOKEN not found in .env")
    sys.exit(1)

print(f"Token: {token[:12]}...")
c = Client(auth=token)

# 1. Whoami
try:
    me = c.users.me()
    print(f"Bot: {me.get('name')} ({me.get('id')})")
except Exception as e:
    print(f"users.me error: {e}")

# 2. Retrieve the Second Brain page
page_id = "65cb8ff62682404ba9c256518e288ce9"
try:
    page = c.pages.retrieve(page_id=page_id)
    print(f"\nSecond Brain page found: {page.get('url')}")
except Exception as e:
    print(f"\nSecond Brain page error: {e}")

# 3. List child blocks (sub-pages / databases inside)
try:
    blocks = c.blocks.children.list(block_id=page_id)
    print("\nChildren:")
    for b in blocks.get("results", []):
        btype = b.get("type", "")
        content = b.get(btype, {})
        title = ""
        if isinstance(content, dict):
            for key in ("title", "rich_text"):
                if key in content:
                    title = "".join(t.get("plain_text", "") for t in content[key])
                    break
        bid = b.get("id", "")
        print(f"  [{btype}] {repr(title)} | id={bid}")
except Exception as e:
    print(f"blocks.children error: {e}")

# 4. Search for all databases the integration can see
try:
    result = c.search(query="", filter={"property": "object", "value": "data_source"})
    dbs = result.get("results", [])
    print(f"\nDatabases accessible to this integration ({len(dbs)}):")
    for db in dbs:
        db_id = db.get("id", "")
        title_arr = db.get("title", [])
        name = "".join(t.get("plain_text", "") for t in title_arr)
        print(f"  {name!r} | id={db_id}")
except Exception as e:
    print(f"search databases error: {e}")
