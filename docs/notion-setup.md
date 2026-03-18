# Notion Setup

Jibsa supports two ways to connect to Notion:

| Mode | Best for | Setup |
|------|----------|-------|
| **Global token** (`NOTION_TOKEN`) | Single workspace shared by all users | Internal integration — simple token |
| **Per-user OAuth** (`NOTION_OAUTH_CLIENT_ID/SECRET`) | Each user connects their own workspace | Public integration — OAuth flow via Slack |

Both modes can coexist. If a user has connected their own Notion workspace via OAuth, that takes priority. Otherwise, Jibsa falls back to the global token.

No field mapping required — Jibsa auto-discovers property names from each database's schema at runtime.

---

## Option A: Global Token (single workspace)

### 1. Create a Notion Integration

> Make sure to create an **Internal** integration, not a Public one.
> Internal integrations give you a simple token — no OAuth needed.

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **+ New integration**
3. Set **Integration type** to **Internal**
4. Name it `Jibsa`, select your workspace
5. Under **Capabilities**, check:
   - Read content
   - Update content
   - Insert content
6. Click **Save**
7. Go to the **Secrets** tab → copy the token → this is your **`NOTION_TOKEN`**

Add it to `.env`:
```
NOTION_TOKEN=your-token-here
```

> If you only see OAuth credentials (Client ID / Client Secret), you created a Public integration by mistake. Go back and create a new one with type **Internal**.

### 2. Grant Access to Your Workspace

The easiest way is to grant access at the top level:

1. Open your main Notion page (e.g. your workspace root or Second Brain page)
2. Click **...** (top right) → **Connections**
3. Find `Jibsa` and click **Confirm**

This gives Jibsa access to all child pages and databases automatically — no need to connect each database individually.

### 3. Fill in `config/notion_databases.yaml`

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

### 4. Enable Notion in Settings

In `config/settings.yaml`, set:

```yaml
integrations:
  notion:
    enabled: true
```

### 5. Restart Jibsa

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

## Option B: Per-User OAuth (each user connects their own workspace)

With per-user OAuth, each Slack user connects their own Notion workspace via `connect notion`. Databases are auto-discovered — no `notion_databases.yaml` needed.

### 1. Create a Public Notion Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **+ New integration**
3. Set **Integration type** to **Public**
4. Name it `Jibsa`
5. Set the **Redirect URI** to `http://localhost`
6. Under **Capabilities**, check:
   - Read content
   - Update content
   - Insert content
7. Click **Save**
8. Go to the **Secrets** tab → copy the **Client ID** and **Client Secret**

Add them to `.env`:
```
NOTION_OAUTH_CLIENT_ID=your-client-id
NOTION_OAUTH_CLIENT_SECRET=your-client-secret
```

### 2. Enable Notion in Settings

In `config/settings.yaml`, set:

```yaml
integrations:
  notion:
    enabled: true
```

### 3. User Flow

Each user connects their own Notion workspace in Slack:

```
@jibsa connect notion    → Jibsa DMs you an OAuth link
                         → you click the link and select pages to share
                         → browser redirects to localhost (page won't load — that's expected)
                         → copy the "code" from the URL bar and paste it in the DM
                         → Jibsa exchanges the code, discovers your databases
                         → "Connected to Notion workspace My Workspace! Found 5 database(s)."
```

Other commands:
```
@jibsa my connections       → see your connected services (including Notion workspace name)
@jibsa disconnect notion    → delete your Notion credentials and database registry
```

### How It Works

- When a user says `connect notion`, Jibsa DMs them an authorization URL
- The user clicks the link, selects which Notion pages to share, and clicks **Allow access**
- The browser redirects to `http://localhost?code=...` (nothing is listening — that's expected)
- The user copies the `code` value and pastes it back in the DM
- Jibsa exchanges the code for an access token (stored encrypted in SQLite)
- Jibsa runs database discovery via Notion's search API and saves a per-user database registry
- All subsequent Notion reads and writes use the user's personal token

### Backward Compatibility

- If a user has connected their own Notion via OAuth, that takes priority
- If not, Jibsa falls back to the global `NOTION_TOKEN` (if configured)
- Both modes can coexist — some users connect personally, others use the shared workspace
- No breaking changes to existing setups

---

## Testing It

In `#jibsa`, try:

- `"what tasks do I have?"` — queries your Tasks database
- `"show me active projects"` — queries your Projects database
- `"how are my habits going?"` — queries Habit Tracker (if configured)
- `"what did I spend this month?"` — queries Expense Record (if configured)
- `"create a task: write the Q2 plan, due Friday, high priority"` — proposes a plan → approve → Notion page created

---

## Adding a New Database (Global Token mode)

Just add an entry to `notion_databases.yaml`:

```yaml
- name: Book Notes
  id: "your-db-id-or-url"
  keywords: [book, highlight, annotation]
```

No code changes needed. Jibsa will automatically query it when a matching keyword appears in a message, and auto-discover its property names for write operations.

In per-user OAuth mode, databases are auto-discovered from the pages the user shared during authorization. To make additional databases available, the user can revoke and reconnect (`disconnect notion` then `connect notion`), selecting more pages during authorization.

---

## Troubleshooting

**"Notion Second Brain connected (0 DBs configured)"**
→ All `id` fields in `notion_databases.yaml` are empty. Fill them in.

**"Could not find database"**
→ The ID is wrong, or the integration doesn't have access. Re-check the Grant Access step.

**"NOTION_TOKEN is not set"**
→ Add the token to your `.env` file, or use per-user OAuth instead.

**"Notion OAuth is not configured"**
→ Set `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET` in `.env`.

**"You're already connected to Notion"**
→ Say `disconnect notion` first, then `connect notion` again.

**Per-user OAuth: "Authorization failed"**
→ The code may have expired. Say `connect notion` to start a fresh flow.

**Per-user OAuth: databases not showing up**
→ Make sure you selected the relevant pages during the Notion authorization step. Notion OAuth only grants access to pages the user explicitly shares.

**Queries return 0 results**
→ Check that the keywords match what you're typing. Try a broader keyword or add a new one to the relevant database entry.
