# Google OAuth Setup (Per-User Credentials)

Unlike team-shared integrations (Notion, Jira), Google Calendar and Gmail require **per-user** authorization. Each team member connects their own Google account via an OAuth flow managed through Slack DMs.

---

## How It Works

```
1. User says:  @jibsa connect google
2. Jibsa DMs the user a Google authorization link
3. User clicks the link, authorizes in their browser
4. Google shows an authorization code
5. User pastes the code back in the Jibsa DM
6. Jibsa exchanges the code for tokens and stores them encrypted
```

Tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and stored in a local SQLite database. Only the user's own tokens are used for their requests.

---

## 1. Create Google OAuth Credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Go to **APIs & Services** > **Credentials**
4. Click **Create Credentials** > **OAuth 2.0 Client ID**
5. Application type: **Desktop app** (this enables the OOB/installed flow)
6. Name it `Jibsa`
7. Copy the **Client ID** and **Client Secret**

### Enable Required APIs

In the same project, go to **APIs & Services** > **Library** and enable:
- **Google Calendar API**
- **Gmail API**

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
| `@jibsa disconnect google` | Revoke tokens and delete stored credentials |
| `@jibsa my connections` | List your connected services |

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

**"Authorization failed: invalid_grant"**
- The auth code expires quickly. Try `connect google` again and paste the code within 60 seconds.

**Credentials lost after restart**
- Set `CREDENTIAL_ENCRYPTION_KEY` in `.env` so the key persists across restarts.

**"Failed to decrypt credentials"**
- The encryption key changed. Users need to `disconnect google` and `connect google` again.
