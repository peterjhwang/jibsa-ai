# Notion Setup (Per-User OAuth)

Each Slack user connects their own Notion workspace via `connect notion`. Databases are auto-discovered — no config files needed.

No field mapping required — Jibsa auto-discovers property names from each database's schema at runtime.

---

## 1. Create a Public Notion Integration

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

---

## 2. Enable Notion in Settings

In `config/settings.yaml`, set:

```yaml
integrations:
  notion:
    enabled: true
```

---

## 3. Restart Jibsa

```bash
# local
python -m src.app

# or Docker
docker-compose restart
```

---

## 4. Connect Your Workspace

In Slack, say:

```
@jibsa connect notion
```

Jibsa will DM you with an authorization link. Here's what happens:

1. Click the link — it opens Notion's authorization page
2. **Select the pages** you want to share with Jibsa, then click **Allow access**
3. Your browser redirects to a page that won't load — that's expected
4. Copy the `code` value from the URL bar. It looks like:
   ```
   http://localhost?code=abc123def...&state=...
   ```
5. **Paste just the code** back in the DM
6. Jibsa exchanges the code, discovers your databases, and shows what it found
7. **Optional: set a parent page** — paste a Notion page URL so Jibsa can auto-create new databases (Tasks, Projects, etc.) under it when needed. Or say `skip`.
8. Done! Your Notion data is now available to your interns.

### Post-Connect Setup

After connecting, Jibsa will:
- Show you all databases it discovered
- Ask if you want to set a **parent page** — this is a Notion page where Jibsa can auto-create new databases when needed (e.g., if an intern needs a Tasks database that doesn't exist yet)
- If you paste a parent page URL, Jibsa also discovers any child databases under it

You can always reconnect later (`disconnect notion` then `connect notion`) to update which pages are shared.

---

## Commands

| Command | What it does |
|---------|-------------|
| `@jibsa connect notion` | Start per-user Notion OAuth flow |
| `@jibsa disconnect notion` | Delete your Notion credentials and database registry |
| `@jibsa my connections` | See your connected services (shows Notion workspace name) |

---

## Testing It

In `#jibsa`, try:

- `"what tasks do I have?"` — queries your Tasks database
- `"show me active projects"` — queries your Projects database
- `"how are my habits going?"` — queries Habit Tracker (if configured)
- `"what did I spend this month?"` — queries Expense Record (if configured)
- `"create a task: write the Q2 plan, due Friday, high priority"` — proposes a plan → approve → Notion page created

---

## How It Works

- **Token storage**: encrypted at rest with Fernet (AES-128-CBC) in SQLite, keyed by Slack user ID
- **Database discovery**: uses Notion's search API to find all databases accessible to the integration
- **Parent page**: stored per-user, enables auto-creation of new databases via templates
- **Per-user registry**: each user's database list is stored in the credential store — no shared config files
- **Notion tokens don't expire**: no refresh flow needed (unlike Google OAuth)

---

## Troubleshooting

**"Notion OAuth is not configured"**
→ Set `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET` in `.env`.

**"You're already connected to Notion"**
→ Say `disconnect notion` first, then `connect notion` again.

**"Authorization failed"**
→ The code may have expired. Say `connect notion` to start a fresh flow.

**Databases not showing up**
→ Make sure you selected the relevant pages during the Notion authorization step. Notion OAuth only grants access to pages the user explicitly shares.

**"Couldn't access that page" (during parent page setup)**
→ The page wasn't shared with the integration during authorization. Reconnect and share it, or say `skip`.

**Queries return 0 results**
→ The database keywords may not match. Jibsa uses template-based keywords (e.g. "task", "project") for routing. If your database names differ, the intern will still find them via Notion's schema.

**"Notion is not connected"**
→ The user hasn't run `connect notion` yet. Each user must connect individually.
