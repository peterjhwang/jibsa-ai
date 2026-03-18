# Google OAuth Setup (Per-User Credentials)

Unlike team-shared integrations (Notion, Jira), Google Workspace (Calendar, Gmail, Drive) requires **per-user** authorization. One OAuth flow connects all three. Each team member connects their own Google account via Slack DMs.

---

## How It Works

```
1. User says:  @jibsa connect google
2. Jibsa DMs the user a Google authorization link
3. User clicks the link, authorizes in their browser
4. Google redirects to http://localhost (page won't load — that's expected)
5. User copies the "code" value from the browser's URL bar and pastes it in the DM
6. Jibsa exchanges the code for tokens and stores them encrypted
7. Calendar, Gmail, and Drive are all connected in one step
```

Tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and stored in a local SQLite database. Only the user's own tokens are used for their requests.

---

## 1. Create Google OAuth Credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Go to **APIs & Services** > **Credentials**
4. Click **Create Credentials** > **OAuth 2.0 Client ID**
5. Application type: **Desktop app** (this enables the localhost redirect flow)
6. Name it `Jibsa`
7. Copy the **Client ID** and **Client Secret**

### Enable Required APIs

> **Important:** You must enable these APIs or you'll get `403 accessNotConfigured` errors.

In the same project, go to **APIs & Services** > **Library** and enable:

| API | Direct link |
|-----|------------|
| **Google Calendar API** | `console.cloud.google.com/apis/api/calendar-json.googleapis.com` |
| **Gmail API** | `console.cloud.google.com/apis/api/gmail.googleapis.com` |
| **Google Drive API** | `console.cloud.google.com/apis/api/drive.googleapis.com` |

Click **Enable** for each one. Wait a minute or two for propagation.

---

## 2. Add to `.env`

```
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
```

### Encryption Key (recommended for production)

```
# Generate a Fernet key:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Add to .env:
CREDENTIAL_ENCRYPTION_KEY=your-generated-key
```

If not set, Jibsa auto-generates a temporary key at startup. Credentials will be **unreadable after a restart** — users would need to reconnect.

---

## 3. User Commands

Each team member manages their own connection:

| Command | What it does |
|---------|-------------|
| `@jibsa connect google` | Start the OAuth flow (sends DM with auth link) |
| `@jibsa google token <JSON>` | Store tokens from `scripts/google_auth.py` (admin shortcut) |
| `@jibsa disconnect google` | Revoke tokens and delete stored credentials |
| `@jibsa my connections` | List your connected services |

**Admin shortcut:** If you have local access to the repo, you can also run `python3 scripts/google_auth.py` on your machine. It opens the consent page in a browser and prints a token JSON — paste it in Slack as `@jibsa google token <JSON>`.

---

## 4. Slack App Permissions

The OAuth flow uses Slack DMs. Make sure your Slack app has these scopes and events:

**Bot Token Scopes:**
- `im:write` — open DMs to send the auth link
- `im:history` — read the DM where user pastes the auth code

**Event Subscriptions:**
- `message.im` — receive DM messages (for the auth code)

See [Slack App Setup](slack-setup.md) for full details.

---

## Security

- **Encryption at rest**: All tokens are Fernet-encrypted before storage. The SQLite file (`data/credentials.db`) contains only ciphertext.
- **Master key**: The `CREDENTIAL_ENCRYPTION_KEY` env var is the only secret needed to decrypt. Keep it safe — treat it like a database password.
- **Token revocation**: `disconnect google` revokes the token at Google's endpoint before deleting locally.
- **No shared access**: Each user's tokens are keyed by their Slack user ID. One user cannot access another's credentials.
- **Refresh tokens only**: Short-lived access tokens are refreshed automatically; only the refresh token persists.

---

## Troubleshooting

**"Google OAuth is not configured"**
- Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`

**"Couldn't send you a DM"**
- Ensure the Slack app has `im:write` scope
- The user may need to open a DM with the bot first (Slack requires this for some workspaces)

**"Authorization failed: invalid_grant" / "Malformed auth code"**
- The auth code expires quickly. Try `connect google` again and paste the code within 60 seconds.
- You can paste the full redirect URL (`http://localhost/?code=...&scope=...`) or just the code value — Jibsa extracts it either way.

**"403 accessNotConfigured"**
- The Google API hasn't been enabled in your Cloud project. Go to **APIs & Services > Library** and enable Google Calendar API, Gmail API, and Google Drive API. See the table in step 1 above for direct links.

**Credentials lost after restart**
- Set `CREDENTIAL_ENCRYPTION_KEY` in `.env` so the key persists across restarts.

**"Failed to decrypt credentials"**
- The encryption key changed. Users need to `disconnect google` and `connect google` again.
