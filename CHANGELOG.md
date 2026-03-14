# Changelog

All notable changes to Jibsa are documented here.

## [0.11.0] — 2026-03-14
### Added
- Interactive setup wizard (`python -m src.setup`)
- Audit logging — all actions persisted to SQLite
- `@jibsa audit` command to view recent actions
- CHANGELOG, GitHub issue/PR templates, CONTRIBUTING update

## [0.10.0] — 2026-03-14
### Added
- Google Calendar tool (read + write, per-user OAuth)
- Gmail tool (read + write, per-user OAuth)
- Morning briefing scheduled job (calendar + tasks + Jira)
- EOD review scheduled job (today's actions + tomorrow's schedule)
- APScheduler jobs persisted to SQLite (survive restarts)

## [0.9.5] — 2026-03-14
### Added
- SQLite-backed intern JD storage (Notion no longer required for core)
- Encrypted per-user credential store (Fernet + SQLite)
- Google OAuth2 flow via Slack DMs
- `connect google` / `disconnect google` / `my connections` commands
- `current_user_id` ContextVar for per-request user identity

### Changed
- Notion is now fully optional (toggle via settings.yaml)
- InternRegistry rewritten to use SQLite backend

## [0.9.0] — 2026-03-14
### Added
- Jira integration (JQL search, create/update/transition issues, comments, worklogs)
- Confluence integration (CQL search, create/update pages, comments)
- Tenacity retry with exponential backoff on Notion, Jira, Confluence APIs
- Circuit breakers on all external API calls (Notion, Jira, Confluence)
- Graceful shutdown (SIGTERM/SIGINT handlers)
- Startup env var validation with clear error messages
- Memory management (thread history eviction, edit session TTL)
- Code sandbox hardening (AST analysis layer)
- Web search rate limiting with ZenRows SERP fallback
- Temp file cleanup on exit
- Dev scripts: setup.sh, compile.sh, install.sh, run.sh, test.sh, doctor.sh
- requirements.in → requirements.txt pinned lockfile workflow

## [0.8.0] — 2026-03-13
### Added
- `@jibsa help` contextual help (grouped commands, per-intern help)
- `@jibsa edit alex's jd` interactive JD editing sessions
- `@jibsa history` approval history with timestamps
- `@jibsa stats` Block Kit usage metrics dashboard
- `doctor` CLI for health checks
- Weekly activity digest scheduled job
- Block Kit cards for intern listing, JD display, stats

## [0.7.0] — 2026-03-12
### Added
- Web Reader tool (ZenRows page fetcher)
- File Generator tool (CSV, JSON, Markdown, text)
- Image Generator tool (Nano Banana 2 / Gemini)
- Reminder tool (APScheduler timed messages)
- Team collaboration (`form team alex, sarah to ...`)

## [0.6.0] — 2026-03-11
### Added
- CrewAI as primary execution engine
- Config validation (Pydantic settings schema)
- Circuit breaker for Notion API
- Request metrics tracking
- Approval TTL (auto-expire pending plans)
- Crew timeout (SIGALRM-based)

## [0.5.0] — 2026-03-10
### Added
- Multi-intern system with conversational hiring
- Intern routing (`@jibsa alex do X`)
- Per-intern tool filtering
- Per-intern memory (global + channel-scoped)
- Notion-backed intern registry
- Block Kit approval buttons

## [0.2.0] — 2026-03-08
### Added
- Notion Second Brain (schema-free, 26 databases)
- Page flattening (any Notion page → key-value JSON)
- Runtime schema discovery for writes
- Keyword-based database routing

## [0.1.0] — 2026-03-07
### Added
- Core Slack bot (Socket Mode)
- Claude runner (subprocess-based)
- Propose-approve gate (text-based)
- Basic Slack threaded conversations
