# Notion Setup

You need two things to connect Jibsa to Notion:

1. A **Notion integration token** → `NOTION_TOKEN` in `.env`
2. **Database IDs** for your 6 PARA databases → `config/notion_databases.yaml`

---

## 1. Create a Notion Integration

> ⚠️ Make sure to create an **Internal** integration, not a Public one.
> Internal integrations give you a simple `secret_` token — no OAuth needed.

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **+ New integration**
3. On the creation screen, set **Integration type** to **Internal**
4. Name it `Jibsa`, select your workspace
5. Under **Capabilities**, check:
   - ✅ Read content
   - ✅ Update content
   - ✅ Insert content
6. Click **Save**
7. Go to the **Secrets** tab on the integration page
8. Copy the **Internal Integration Secret** (starts with `secret_`) → this is your **`NOTION_TOKEN`**

Add it to `.env`:
```
NOTION_TOKEN=secret_your-token-here
```

> If you only see OAuth credentials (Client ID / Client Secret), you created a Public integration by mistake. Go back and create a new one with type **Internal**.

---

## 2. Create the 6 PARA Databases

Jibsa expects these 6 databases in your Notion workspace. Create any that don't exist yet.

### Tasks Database
Properties:
| Property | Type | Options |
|----------|------|---------|
| Name | Title | — |
| Status | Select | `To Do`, `In Progress`, `Done` |
| Priority | Select | `High`, `Medium`, `Low` |
| Due Date | Date | — |
| Project | Relation | → Projects database |

### Projects Database
Properties:
| Property | Type | Options |
|----------|------|---------|
| Name | Title | — |
| Status | Select | `Planning`, `In Progress`, `Done` |
| Owner | Rich Text | — |
| Deadline | Date | — |

### Meeting Notes Database
Properties:
| Property | Type |
|----------|------|
| Name | Title |
| Date | Date |
| Attendees | Rich Text |
| Project | Relation → Projects |

### Journal Database
Properties:
| Property | Type |
|----------|------|
| Name | Title |
| Date | Date |

### Knowledge Base Database
Properties:
| Property | Type |
|----------|------|
| Name | Title |
| Tags | Multi-select |

### CRM / Contacts Database
Properties:
| Property | Type |
|----------|------|
| Name | Title |
| Company | Rich Text |
| Role | Rich Text |
| Last Contacted | Date |
| Notes | Rich Text |

---

## 3. Connect Jibsa to Each Database

For **each** of the 6 databases:

1. Open the database in Notion
2. Click **...** (top right) → **Connections**
3. Find `Jibsa` and click **Confirm**

Without this step, the integration token cannot access the database.

---

## 4. Get the Database IDs

For **each** database:

1. Open the database as a full page (not inline)
2. Copy the URL from your browser — it looks like:
   ```
   https://www.notion.so/your-workspace/abc123def456...?v=...
   ```
3. The database ID is the 32-character string **before** the `?v=` part:
   ```
   abc123de-f456-7890-abcd-ef1234567890
   ```
   (Notion sometimes shows it without hyphens — both formats work)

---

## 5. Fill in `config/notion_databases.yaml`

This file is in `.gitignore` — your DB IDs won't be committed. Copy the example first:

```bash
cp config/notion_databases.yaml.example config/notion_databases.yaml
```

Then fill in your IDs:

```yaml
notion:
  tasks_db: "abc123de-f456-7890-abcd-ef1234567890"
  projects_db: "..."
  meeting_notes_db: "..."
  journal_db: "..."
  knowledge_base_db: "..."
  crm_db: "..."
  archive_db: ""   # optional, leave empty if not using
```

---

## 6. Enable Notion in Settings

In `config/settings.yaml`, set:

```yaml
integrations:
  notion:
    enabled: true
```

---

## 7. Restart Jibsa

```bash
# local
python -m src.app

# or Docker
docker-compose restart
```

You should see in the logs:
```
INFO  — Notion Second Brain connected (6 DBs configured)
```

---

## Testing It

In `#jibsa`, try:

- `"what tasks do I have?"` — Jibsa should list tasks from your Tasks DB
- `"show me active projects"` — lists projects with status and deadline
- `"create a task: write the Q2 plan, due Friday, high priority"` — Jibsa proposes a plan → approve → Notion page created with a link

---

## Troubleshooting

**"Notion Second Brain connected (0 DBs configured)"**
→ DB IDs in `notion_databases.yaml` are empty. Fill them in.

**"Notion query_database failed: Could not find database"**
→ The database ID is wrong, or you forgot to connect the integration to that database (Step 3).

**"NOTION_TOKEN is not set"**
→ Add `NOTION_TOKEN=secret_...` to your `.env` file.

**Property name errors**
→ Jibsa expects exact property names as listed in Step 2. If your database uses different names (e.g. `"due date"` instead of `"Due Date"`), rename them in Notion to match.
