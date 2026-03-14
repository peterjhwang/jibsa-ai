<p align="center">
  <img src="assets/icon.png" alt="Jibsa" width="120">
</p>

<h1 align="center">집사 · Jibsa</h1>

<p align="center">
  <strong>Your AI Intern Platform</strong> — create custom AI interns with job descriptions, tools, and approval rules, all inside Slack.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+"></a>
  <a href="https://www.crewai.com"><img src="https://img.shields.io/badge/powered%20by-CrewAI-purple" alt="Powered by CrewAI"></a>
  <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv"></a>
</p>

---

Jibsa (집사, Korean for "steward") is an open-source multi-AI-intern platform that lives in your Slack workspace. Create custom AI interns — each with their own job description, personality, tools, and approval rules — and delegate tasks via `@jibsa`.

Built on [CrewAI](https://www.crewai.com) with multi-provider LLM support (Claude, GPT-4, Gemini). All write operations go through a **propose-approve** gate with interactive Block Kit buttons — interns never act without your explicit approval.

## How It Works

```
You:  "@jibsa alex write 3 LinkedIn posts about our product launch"
                        │
                ┌───────▼────────┐
                │  🔀 Router     │  routes to Alex (content intern)
                └───────┬────────┘
                        │
                ┌───────▼────────┐
                │  🤖 CrewAI     │  Alex reasons with assigned tools
                └───────┬────────┘  (web search, Notion, etc.)
                        │
                ┌───────▼────────┐
                │  🤔 Clarify?   │  if ambiguous → asks a question first
                └───────┬────────┘  (you reply, conversation continues)
                        │
                ┌───────▼────────┐
                │  📋 Propose    │  posts plan with ✅/❌ buttons
                └───────┬────────┘
                        │  ← you click ✅ Approve
                ┌───────▼────────┐
                │  ⚡ Execute    │  Notion, Jira, Confluence, Slack, etc.
                └───────┬────────┘
                        │
                ✅ [Alex — Content Intern] confirms completion
```

### Commands (all via `@jibsa` mention)

| Command | What it does |
|---------|-------------|
| `@jibsa hire a marketing intern` | Start conversational hiring flow |
| `@jibsa alex write 3 blog posts` | Delegate a task to intern Alex |
| `@jibsa ask mia to research competitors` | Alternative routing syntax |
| `@jibsa alex, mia research and write a report` | Multi-intern team collaboration |
| `@jibsa list interns` or `@jibsa team` | Show all active interns (Block Kit cards) |
| `@jibsa show alex's jd` | View an intern's Job Description (rich Block Kit) |
| `@jibsa edit alex's jd` | Interactive JD editing session |
| `@jibsa fire alex` | Deactivate an intern |
| `@jibsa help` | Contextual help with all commands |
| `@jibsa help alex` | Intern-specific help (role, tools, usage) |
| `@jibsa stats` | Usage metrics dashboard with recent actions |
| `@jibsa history` | Approval history (approved/rejected plans) |
| `@jibsa reminders` | List pending scheduled reminders |
| `@jibsa connect google` | Start per-user Google OAuth flow (DM-based) |
| `@jibsa disconnect google` | Revoke and delete stored Google tokens |
| `@jibsa my connections` | List your connected services |

### Approval

Plans can be approved via **Block Kit buttons** (✅ Approve / ❌ Reject) or text replies:

| Approve | Reject / Revise |
|---------|-----------------|
| `✅`, `yes`, `approved`, `go`, `go ahead`, `do it`, `proceed` | `❌`, `no`, `cancel`, `stop`, `revise`, `change` |

## Features

### Multi-Intern System
- **Conversational hiring** — describe what you need, Jibsa helps you write a complete Job Description
- **Ambiguity detection** — interns ask clarifying questions when a request is vague or missing critical details before proposing an action
- **JD validation** — enforces name, role, responsibilities, tool assignments
- **Interactive JD editing** — `edit alex's jd` starts a session to modify any field via natural language or direct commands
- **Per-intern tools** — each intern only sees their assigned tools
- **Channel-scoped memory** — interns remember past interactions, isolated per Slack channel (capped at 20 entries each)
- **Smart routing** — `@jibsa alex do X`, `@jibsa ask alex to X`, name prefix, etc.
- **Team collaboration** — `@jibsa alex, mia do X` spins up a multi-agent CrewAI crew

### Reliability & Observability
- **Startup validation** — validates required API keys and config at boot; clear error messages for missing tokens
- **Graceful shutdown** — SIGTERM/SIGINT handlers cleanly stop Socket Mode, APScheduler, and temp file cleanup
- **Health check CLI** — `python -m src.doctor` (or `./scripts/doctor.sh`) validates config, env vars, Slack/Notion/LLM connectivity, and dependencies
- **Config validation** — Pydantic-validated `settings.yaml` catches typos at startup
- **Circuit breaker** — Notion, Jira, and Confluence API calls use three-state circuit breakers (CLOSED → OPEN → HALF_OPEN) to prevent cascading failures
- **Retry with backoff** — tenacity exponential backoff on all Notion, Jira, and Confluence API calls (retries 429/5xx errors)
- **Memory management** — conversation history auto-evicts oldest threads (max 500); edit sessions expire after 1 hour TTL
- **Request tracing** — every request gets a UUID, latency is logged
- **Usage metrics** — `@jibsa stats` shows per-intern request counts, latencies, approval rates, and errors
- **Approval history** — `@jibsa history` shows recent approved/rejected plans with timestamps
- **Scheduled activity digest** — configurable weekly summary posted to your Jibsa channel
- **Thinking indicator** — posts a "Thinking..." message while CrewAI reasons, then removes it
- **Approval TTL** — pending plans auto-expire after a configurable timeout (default 1 hour)
- **Crew timeout** — configurable `SIGALRM`-based timeout for CrewAI executions (default 5 min)
- **Code sandbox hardening** — two-layer defence (regex + AST analysis) blocks imports, dunder access, and obfuscation tricks
- **Web search rate limiting** — token-bucket limiter (10/min) with ZenRows SERP fallback prevents IP bans

### Rich Slack UI (Block Kit)
- **Intern cards** — `list interns` shows per-intern cards with tools, responsibilities preview, and "View JD" buttons
- **JD display** — `show alex's jd` renders structured sections with fields, memory stats, and action hints
- **Stats dashboard** — `stats` shows metrics with recent actions timeline
- **Contextual help** — `help` provides grouped command reference; `help alex` shows intern-specific usage

### Tools

| Tool | Type | Description |
|------|------|-------------|
| **Notion** | Read + Write | Query and manage tasks, projects, notes, journals, expenses, workouts (26 databases) |
| **Jira** | Read + Write | Search issues (JQL), create/update issues, transitions, comments, worklogs |
| **Confluence** | Read + Write | Search pages (CQL), create/update pages, add comments |
| **Web Search** | Read-only | DuckDuckGo search with ZenRows SERP fallback — rate limited |
| **Web Reader** | Read-only | Fetch and read full web pages via [ZenRows](https://www.zenrows.com) (JS rendering, anti-bot) |
| **Code Exec** | Read-only | Sandboxed Python execution with AST-level security analysis |
| **File Generator** | Write (approval) | Generate CSV, JSON, Markdown, or text files and upload to Slack |
| **Image Generator** | Write (approval) | Generate AI images via Nano Banana 2 (Gemini) and upload to Slack |
| **Reminder** | Write (approval) | Schedule timed reminders via APScheduler — posts to Slack at the specified time |
| **Slack** | Write (approval) | Post messages to Slack channels |
| **Calendar** | Read-only (stub) | Google Calendar integration — coming in Phase 4 |

### Integrations

| Integration | Status |
|-------------|--------|
| **Slack** — Socket Mode bot, threaded conversations, Block Kit buttons | ✅ Live |
| **Notion** — Schema-free PARA Second Brain (26 databases) | ✅ Live |
| **Jira** — Issue search (JQL), create/update, transitions, comments, worklogs | ✅ Live |
| **Confluence** — Page search (CQL), create/update pages, comments | ✅ Live |
| **CrewAI** — Multi-provider LLM orchestration (Claude, GPT-4, Gemini) | ✅ Live |
| **ZenRows** — Web page fetching with JS rendering and anti-bot bypass | ✅ Live |
| **Nano Banana 2** — AI image generation via Google Gemini | ✅ Live |
| **APScheduler** — Background scheduler for timed reminders | ✅ Live |
| **Google Calendar** — Event management, scheduled reminders | 🔜 Phase 4 |
| **Gmail** — Email triage, weekly digest | 🔜 Phase 4 |

### Notion Second Brain

Jibsa connects to your Notion workspace with a **schema-free** architecture — no hardcoded property names or database structures. Add any database by editing `config/notion_databases.yaml`:

```yaml
- name: Tasks
  id: abc123...
  keywords: [task, todo, action]
```

**Available actions:** `create_task`, `update_task_status`, `create_project`, `create_note`, `create_journal_entry`, `log_expense`, `log_workout`

Reads use page flattening (any page to key-value JSON). Writes auto-discover property schemas at runtime.

### Jira + Confluence

Jibsa connects to your Atlassian Cloud instance using a single set of credentials (`JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`). Enable in `config/settings.yaml`:

```yaml
integrations:
  jira:
    enabled: true
  confluence:
    enabled: true
```

**Jira actions:** `create_issue`, `update_issue`, `transition_issue`, `add_comment`, `add_worklog`
**Confluence actions:** `create_page`, `update_page`, `add_comment`

Read tools let agents search Jira (JQL) and Confluence (CQL) during reasoning. Write operations go through the standard propose-approve flow.

### Per-User Credentials (Google OAuth)

Team-shared integrations (Notion, Jira, Confluence) use a single API token in `.env`. Personal services (Google Calendar, Gmail) use **per-user OAuth** — each team member connects their own account:

```
@jibsa connect google    → Jibsa DMs you an OAuth link
                         → you authorize and paste the code back
                         → tokens stored encrypted (Fernet + SQLite)
```

Credentials are encrypted at rest with AES-128-CBC and keyed by Slack user ID — one user cannot access another's tokens. See [Google OAuth Setup](docs/google-oauth-setup.md) for details.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/peterjhwang/jibsa-ai.git
cd jibsa-ai

# 2. Bootstrap (installs uv, creates .venv, compiles & installs deps)
./scripts/setup.sh

# 3. Configure
#    Edit .env with your Slack tokens, LLM API key, and Notion token
#    Edit config/notion_databases.yaml with your Notion database IDs

# 4. Verify setup
./scripts/doctor.sh
# Checks runtime, deps, env vars, and config validation

# 5. Run
./scripts/run.sh

# 6. Talk to Jibsa
# Go to #jibsa in Slack and say: "help" or "hire a content marketing intern"
```

### Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/setup.sh` | Full bootstrap: install uv, create `.venv`, compile + install deps |
| `./scripts/compile.sh` | Compile `requirements.in` → pinned `requirements.txt` |
| `./scripts/install.sh` | Install from pinned `requirements.txt` (add `--dev` for test deps) |
| `./scripts/run.sh` | Start Jibsa (Socket Mode) |
| `./scripts/test.sh` | Run pytest (pass any pytest args) |
| `./scripts/doctor.sh` | Health check: uv, .venv, deps, env vars, config |

### With Docker

```bash
cp .env.example .env
# Edit .env with your tokens

docker-compose up -d
```

---

## Documentation

- **[Slack App Setup](docs/slack-setup.md)** — Create and configure the Slack app
- **[Notion Setup](docs/notion-setup.md)** — Connect your Notion Second Brain
- **[Jira + Confluence Setup](docs/jira-confluence-setup.md)** — Connect Atlassian (team-shared API token)
- **[Google OAuth Setup](docs/google-oauth-setup.md)** — Per-user Google Calendar + Gmail credentials

---

## Configuration

All behaviour is controlled via YAML files in `config/`:

| File | Purpose |
|------|---------|
| `settings.yaml` | LLM provider, channel, timezone, approval keywords, integrations |
| `persona.yaml` | Jibsa's name, tone, and personality |
| `notion_databases.yaml` | Notion database IDs and keyword routing (gitignored) |
| `prompts/system.txt` | Jibsa orchestrator system prompt |
| `prompts/intern.txt` | Intern-specific system prompt template |
| `prompts/hire.txt` | Hiring flow system prompt |

### LLM Configuration

Jibsa uses CrewAI with multi-provider support. Configure in `config/settings.yaml`:

```yaml
llm:
  provider: "anthropic"          # "anthropic", "openai", or "google"
  model: "claude-sonnet-4-20250514"
  temperature: 0.7
  max_tokens: 4096
```

Set the corresponding API key in `.env`:
- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`
- Google: `GOOGLE_API_KEY`

Secrets go in `.env` (never committed).

---

## Project Structure

```
jibsa-ai/
├── src/
│   ├── app.py                  # Slack Bolt entry point (Socket Mode + graceful shutdown)
│   ├── orchestrator.py         # Central router: messages → interns → CrewAI → approval
│   ├── crew_runner.py          # CrewAI Agent/Task/Crew builder (primary engine)
│   ├── router.py               # Message parsing, intern routing, team detection
│   ├── hire_flow.py            # Conversational JD creation flow
│   ├── intern_registry.py      # CRUD for interns (Notion-backed, cached)
│   ├── tool_registry.py        # Tool catalog + per-intern permission checking
│   ├── approval.py             # ApprovalState machine per Slack thread (with TTL)
│   ├── config_schema.py        # Pydantic validation for settings.yaml
│   ├── doctor.py               # Health check CLI (python -m src.doctor)
│   ├── context.py              # ContextVar for per-request user identity
│   ├── circuit_breaker.py      # Three-state circuit breaker for API resilience
│   ├── metrics.py              # In-memory request tracking and stats
│   ├── scheduler.py            # APScheduler wrapper for timed reminders
│   ├── models/
│   │   └── intern.py           # InternJD dataclass (validation, channel-scoped memory)
│   ├── tools/
│   │   ├── notion_read_tool.py     # CrewAI BaseTool: Notion queries
│   │   ├── jira_read_tool.py       # CrewAI BaseTool: Jira issue search (JQL)
│   │   ├── confluence_read_tool.py # CrewAI BaseTool: Confluence page search (CQL)
│   │   ├── web_search_tool.py      # CrewAI BaseTool: DuckDuckGo + ZenRows fallback
│   │   ├── web_reader_tool.py      # CrewAI BaseTool: ZenRows page fetcher
│   │   ├── code_exec_tool.py       # CrewAI BaseTool: sandboxed Python (regex + AST)
│   │   ├── file_gen_tool.py        # CrewAI BaseTool: CSV/JSON/MD/TXT generator
│   │   ├── image_gen_tool.py       # CrewAI BaseTool: Nano Banana 2 image generation
│   │   ├── reminder_tool.py        # CrewAI BaseTool: scheduled reminders
│   │   ├── slack_tool.py           # CrewAI BaseTool: Slack post (write, needs approval)
│   │   └── calendar_tool.py        # CrewAI BaseTool: Calendar stub (Phase 4)
│   └── integrations/
│       ├── notion_client.py        # Thin Notion SDK wrapper (retry/backoff)
│       ├── notion_second_brain.py  # Schema-free PARA operations
│       ├── jira_client.py          # Thin Jira wrapper (retry/backoff, execute_step)
│       ├── confluence_client.py    # Thin Confluence wrapper (retry/backoff, execute_step)
│       ├── credential_store.py    # Fernet-encrypted SQLite per-user credential store
│       └── google_oauth.py        # Google OAuth2 OOB flow (per-user tokens)
│
├── config/
│   ├── settings.yaml           # LLM, channel, timezone, approval, integrations
│   ├── persona.yaml            # Jibsa's personality
│   ├── notion_databases.yaml   # Notion DB mappings (gitignored)
│   └── prompts/
│       ├── system.txt          # Jibsa orchestrator prompt
│       ├── intern.txt          # Intern system prompt template
│       └── hire.txt            # Hire flow prompt
│
├── scripts/
│   ├── setup.sh                # Full bootstrap (uv, .venv, compile, install)
│   ├── compile.sh              # requirements.in → requirements.txt
│   ├── install.sh              # Install pinned deps into .venv
│   ├── run.sh                  # Start Jibsa
│   ├── test.sh                 # Run pytest
│   └── doctor.sh               # Health check (runtime, deps, env, config)
│
├── data/                       # SQLite credential store (gitignored)
├── tests/                      # pytest test suite (435 passing)
├── docs/                       # Setup guides
├── assets/                     # Logo and images
├── requirements.in             # Loose dependency constraints (edit this)
├── requirements.txt            # Pinned lockfile (auto-generated)
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Architecture

```mermaid
graph TD
    User["👤 You in #jibsa"] -->|"@jibsa alex do X"| SlackSocket["Slack Socket Mode"]

    SlackSocket --> App["app.py<br/>Slack Bolt + Block Kit"]
    App --> Router["router.py<br/>Parse & Route"]

    Router -->|hire request| HireFlow["hire_flow.py<br/>JD Builder"]
    Router -->|intern task| Orchestrator["orchestrator.py<br/>Orchestrator"]
    Router -->|management cmd| Orchestrator

    HireFlow -->|JD complete| Registry["intern_registry.py<br/>Notion-backed"]

    Orchestrator --> CrewRunner["crew_runner.py<br/>CrewAI Engine"]

    CrewRunner -->|"Agent + Task + Crew"| CrewAI["CrewAI<br/>(Claude / GPT-4 / Gemini)"]

    CrewAI -->|tool call| Tools["Tools<br/>Notion · Jira · Confluence<br/>Web Search · Web Reader<br/>Code Exec · File Gen · Image Gen"]
    CrewAI -->|ambiguous| Clarify["Clarify<br/>Ask user for details"]
    Clarify -->|user replies| CrewAI
    CrewAI -->|action plan| Approval["approval.py<br/>Block Kit ✅ / ❌"]

    Approval -->|approved| Execute["Execute Plan<br/>Notion · Jira · Confluence<br/>Slack · Files · Images · Reminders"]
    Approval -->|rejected| User

    Tools -->|read results| CrewAI
    Execute -->|confirm| User
    CrewAI -->|response| User
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Orchestration | CrewAI | Native multi-provider LLM, Agent/Task/Crew model, built-in tool use, ambiguity detection |
| LLM support | Multi-provider | `anthropic/claude`, `openai/gpt-4o`, `google/gemini` via CrewAI |
| Slack transport | Socket Mode | No public URL or reverse proxy needed |
| Approval gate | Block Kit buttons + text | Interactive ✅/❌ buttons with text fallback, auto-expiring TTL |
| Tool isolation | Per-intern filtering | Each intern only accesses tools listed in their JD |
| Config validation | Pydantic | Catches typos and invalid values at startup, not runtime |
| API resilience | Circuit breaker + retry | Circuit breaker (3 failures → open) + tenacity exponential backoff on all external APIs |
| Notion reads | Page flattening | Any page → flat key-value JSON, passed raw to LLM |
| Notion writes | Runtime schema discovery | Auto-detect property types, no hardcoded schemas |
| Intern storage | Notion database | JDs stored in Notion Interns DB with caching |
| Database routing | Keyword matching | Config-driven — add any Notion database without code changes |
| Personal credentials | Fernet + SQLite | Per-user OAuth tokens encrypted at rest, keyed by Slack user ID |

---

## Testing

```bash
# Run all tests
./scripts/test.sh

# Run a specific test file
./scripts/test.sh -k "test_jira"

# Stop on first failure
./scripts/test.sh -x

# Run with coverage
./scripts/test.sh --cov=src --cov-report=term-missing
```

435 tests covering: routing, approval, CrewAI runner, hire flow, intern model, tool registry, all 11 tools, Jira/Confluence clients, credential store, Google OAuth, connection commands, orchestrator (help, edit, history, Block Kit), Notion second brain, circuit breakers, retry/backoff, startup validation, memory eviction, sandbox hardening, rate limiting, metrics, scheduler, doctor CLI.

---

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- A [Slack app](https://api.slack.com/apps) with Socket Mode + Interactivity enabled
- LLM API key (Anthropic, OpenAI, or Google — depending on `settings.yaml` config)
- Notion integration token (for Second Brain + intern storage)
- **Optional:** `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN` for Jira + Confluence integration
- **Optional:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` for per-user Google OAuth (Calendar + Gmail)
- **Optional:** `CREDENTIAL_ENCRYPTION_KEY` for persistent encrypted credential storage
- **Optional:** `ZENROWS_API_KEY` for the Web Reader tool and web search fallback
- **Optional:** `GOOGLE_API_KEY` for Nano Banana 2 image generation (also used if your LLM provider is Google)

---

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| **1** | Core loop: Slack bot + Claude + propose-approve flow | ✅ Done |
| **2** | Notion Second Brain (PARA: 26 databases, schema-free) | ✅ Done |
| **2.5** | Multi-intern platform: CrewAI, hiring flow, 5 tools, Block Kit | ✅ Done |
| **2.6** | Reliability (config validation, circuit breaker, metrics, approval TTL) | ✅ Done |
| **2.7** | New tools: Web Reader, File Gen, Image Gen, Reminders + team collaboration | ✅ Done |
| **2.8** | UX: help, edit JD, history, Block Kit, doctor CLI, activity digest | ✅ Done |
| **3** | Jira + Confluence, hardening (retry, shutdown, sandbox, rate limiting) | ✅ Done |
| **3.5** | Per-user credential store (encrypted SQLite + Google OAuth) | ✅ Done |
| **4** | Google Calendar + Gmail tools + scheduled jobs (morning briefing, EOD review) | 🔜 |
| **5** | Setup wizard, audit logging, open-source polish | 🔜 |

## License

[MIT](LICENSE)
