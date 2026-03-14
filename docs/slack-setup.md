# Slack App Setup

You need two tokens to run Jibsa:

| Token | Env var | Starts with |
|-------|---------|-------------|
| Bot Token | `SLACK_BOT_TOKEN` | `xoxb-` |
| App-Level Token | `SLACK_APP_TOKEN` | `xapp-` |

---

## 1. Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From scratch**
3. Name it `Jibsa` and pick your workspace
4. Click **Create App**

---

## 2. Enable Socket Mode

Socket Mode lets Jibsa connect without a public URL.

1. In the left sidebar, go to **Socket Mode**
2. Toggle **Enable Socket Mode** ON
3. You'll be asked to create an App-Level Token:
   - Name it `jibsa-socket`
   - Add scope: `connections:write`
   - Click **Generate**
4. Copy the token (starts with `xapp-`) → this is your **`SLACK_APP_TOKEN`**

---

## 3. Add Bot Token Scopes

1. In the left sidebar, go to **OAuth & Permissions**
2. Scroll to **Scopes** → **Bot Token Scopes**
3. Add these scopes:

| Scope | Purpose |
|-------|---------|
| `chat:write` | Post messages |
| `channels:history` | Read messages in public channels |
| `channels:read` | Look up channel info |
| `users:read` | Resolve user IDs to names |
| `im:history` | Read DMs (for OAuth code flow) |
| `im:write` | Open DMs (for OAuth link delivery) |
| `files:write` | Upload generated files and images |

---

## 4. Subscribe to Events

1. In the left sidebar, go to **Event Subscriptions**
2. Toggle **Enable Events** ON
3. Under **Subscribe to bot events**, click **Add Bot User Event**
4. Add: `message.channels`, `message.im`
5. Click **Save Changes**

---

## 5. Install the App to Your Workspace

1. In the left sidebar, go to **OAuth & Permissions**
2. Click **Install to Workspace** → **Allow**
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`) → this is your **`SLACK_BOT_TOKEN`**

---

## 6. Invite Jibsa to #jibsa

In Slack, create a `#jibsa` channel (if it doesn't exist), then invite the bot:

```
/invite @Jibsa
```

---

## 7. Add Tokens to .env

```bash
cp .env.example .env
```

Edit `.env`:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

---

## Done

Start Jibsa:

```bash
./scripts/run.sh
```

You should see:

```
INFO  — Starting Jibsa (Socket Mode)...
INFO  — Jibsa will listen in #jibsa
```

Go to `#jibsa` in Slack and say hello.
