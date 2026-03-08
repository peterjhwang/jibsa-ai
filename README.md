# 집사 · Jibsa

**Your AI Steward** — an open-source AI secretary that lives in your Slack workspace.

Jibsa (집사, Korean for "steward") acts as the central hub for task management, scheduling, information retrieval, and team coordination — all from a single `#jibsa` Slack channel.

Built on [Claude](https://claude.ai) via the `claude -p` headless CLI.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/peterjhwang/jibsa-ai.git
cd jibsa-ai

# 2. Configure
cp .env.example .env
# Edit .env with your Slack tokens

# 3. Run
docker-compose up -d

# 4. Talk to Jibsa
# Go to #jibsa in Slack and say hello
```

## Configuration

All behaviour is controlled via YAML files in `config/`:

| File | Purpose |
|------|---------|
| `settings.yaml` | Channel, timezone, schedules, approval keywords |
| `persona.yaml` | Jibsa's name, tone, and personality |
| `notion_databases.yaml` | Notion DB ID mappings (Phase 2) |
| `prompts/system.txt` | Claude system prompt template |

Secrets go in `.env` (never committed).

## Build Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **1** | Core loop: Slack bot + Claude + propose-approve flow | ✅ Done |
| **2** | Notion Second Brain (PARA: tasks, projects, meeting notes, CRM) | ✅ Done |
| **3** | Jira + Google Calendar + scheduled jobs (morning briefing, EOD review) | 🔜 |
| **4** | Gmail + weekly digest + team interactions | 🔜 |
| **5** | Setup wizard, Slack Block Kit, audit logging, open-source polish | 🔜 |

## Requirements

- Docker + Docker Compose
- A [Slack app](https://api.slack.com/apps) with Socket Mode enabled
- Claude CLI authenticated (`claude` in PATH, Max plan OAuth)
- (Phase 2+) Notion integration token
- (Phase 3+) Google OAuth credentials

## Slack App Setup

Your Slack app needs:
- **Socket Mode** enabled
- **Event Subscriptions**: `message.channels`
- **Bot Token Scopes**: `chat:write`, `channels:history`, `channels:read`, `users:read`, `im:write`
- Bot invited to `#jibsa` channel

## How It Works

1. You send a message in `#jibsa`
2. Jibsa routes it through Claude
3. For **action requests** → Jibsa proposes a plan and waits for approval
4. You reply ✅ to execute or ❌ to cancel/revise
5. For **questions** → Jibsa answers directly

## License

MIT — see [LICENSE](LICENSE).

