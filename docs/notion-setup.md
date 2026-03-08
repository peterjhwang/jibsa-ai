# Notion Setup

You need two things to connect Jibsa to Notion:

1. A **Notion integration token** → `NOTION_TOKEN` in `.env`
2. **Database IDs** for your databases → `config/notion_databases.yaml`

No field mapping required — Jibsa auto-discovers property names from each database's schema at runtime.

---

## 1. Create a Notion Integration

> ⚠️ Make sure to create an **Internal** integration, not a Public one.
> Internal integrations give you a simple token — no OAuth needed.

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **+ New integration**
3. Set **Integration type** to **Internal**
4. Name it `Jibsa`, select your workspace
5. Under **Capabilities**, check:
   - ✅ Read content
   - ✅ Update content
   - ✅ Insert content
6. Click **Save**
7. Go to the **Secrets** tab → copy the token → this is your **`NOTION_TOKEN`**

Add it to `.env`:
```
NOTION_TOKEN=your-token-here
```

> If you only see OAuth credentials (Client ID / Client Secret), you created a Public integration by mistake. Go back and create a new one with type **Internal**.

---

## 2. Grant Access to Your Workspace

The easiest way is to grant access at the top level:

1. Open your main Notion page (e.g. your workspace root or Second Brain page)
2. Click **...** (top right) → **Connections**
3. Find `Jibsa` and click **Confirm**

This gives Jibsa access to all child pages and databases automatically — no need to connect each database individually.

---

## 3. Fill in `config/notion_databases.yaml`

This file is in `.gitignore` — your IDs won't be committed. Copy the example first:

```bash
cp config/notion_databases.yaml.example config/notion_databases.yaml
```

Then fill in your database IDs. You can paste the **full Notion URL** or a bare UUID — both work:

```yaml
notion:
  databases:
    - name: Tasks
      id: "https://www.notion.so/your-workspace/Tasks-abc123..."
      keywords: [task, todo, action]

    - name: Projects
      id: "abc123de-f456-7890-abcd-ef1234567890"
      keywords: [project, build, launch]
```

**To find a database ID:**
1. Open the database as a full page in Notion
2. Copy the URL — it looks like:
   ```
   https://www.notion.so/your-workspace/Tasks-abc123def456?v=...
   ```
3. Paste the full URL — Jibsa extracts the ID automatically.

**keywords** control which database gets queried when a user message contains that word. Add, remove, or change them to suit your workflow.

Only include databases you actually want Jibsa to read. Leave out anything private — just don't add it to the list.

---

## 4. Enable Notion in Settings

In `config/settings.yaml`, set:

```yaml
integrations:
  notion:
    enabled: true
```

---

## 5. Restart Jibsa

```bash
# local
python -m src.app

# or Docker
docker-compose restart
```

You should see in the logs:
```
INFO  — Notion Second Brain connected (N DBs configured)
```

---

## Testing It

In `#jibsa`, try:

- `"what tasks do I have?"` — queries your Tasks database
- `"show me active projects"` — queries your Projects database
- `"how are my habits going?"` — queries Habit Tracker (if configured)
- `"what did I spend this month?"` — queries Expense Record (if configured)
- `"create a task: write the Q2 plan, due Friday, high priority"` — proposes a plan → approve → Notion page created

---

## Adding a New Database

Just add an entry to `notion_databases.yaml`:

```yaml
- name: Book Notes
  id: "your-db-id-or-url"
  keywords: [book, highlight, annotation]
```

No code changes needed. Jibsa will automatically query it when a matching keyword appears in a message, and auto-discover its property names for write operations.

---

## Troubleshooting

**"Notion Second Brain connected (0 DBs configured)"**
→ All `id` fields in `notion_databases.yaml` are empty. Fill them in.

**"Could not find database"**
→ The ID is wrong, or the integration doesn't have access. Re-check Step 2.

**"NOTION_TOKEN is not set"**
→ Add the token to your `.env` file.

**Queries return 0 results**
→ Check that the keywords match what you're typing. Try a broader keyword or add a new one to the relevant database entry.
