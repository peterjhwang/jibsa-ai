# Jibsa AI – v1 Project Tasks
**Version**: v1.0 Target (post v0.6)
**Last updated**: March 13, 2026
**Owner**: Peter Hwang
**Repo**: https://github.com/peterjhwang/jibsa-ai
**Goal**: Stable, production-ready multi-AI-intern platform with CrewAI orchestration

## Overall Vision (1 sentence)
Any Slack user can create custom AI interns (with job descriptions + tools + approval rules) and delegate tasks — all inside one @jibsa bot for v1.

## Current Status – v0.6 ✅
- Conversational hiring flow (`@jibsa hire ...`)
- Intern registry + persona storage (Notion-backed)
- Smart message routing (`@jibsa ask Alex to ...`, `@jibsa alex do X`)
- Threaded work with **[Intern Name — Role]** prefix
- Propose-approve gate (text-based ✅/❌ + Block Kit buttons)
- Notion Second Brain (schema-free, 26 databases)
- **CrewAI orchestration** — each intern is a CrewAI Agent with tools, memory, and multi-provider LLM
- **Job Description enforcement** — validation (name, role, responsibilities, tool names), conversational JD refiner
- **5 tools live**: Notion query, Web Search (DuckDuckGo), Code Exec (sandboxed), Slack post (with approval), Calendar (stub)
- **ToolRegistry** with per-intern filtering + write-action permission checks
- **Block Kit approval buttons** — interactive Approve/Reject in Slack
- **Per-intern memory** (capped at 20 entries, injected into backstory)
- **Management commands**: `list interns`, `team`, `show <name>'s jd`, `fire <name>`
- **181 tests passing**

## v1.0 Success Criteria
- [x] 4–5 pre-built tools working
- [x] CrewAI orchestration per intern
- [x] Structured JD enforcement
- [ ] Self-hostable, documented, MIT-ready for open-source
- [ ] End-to-end manual testing with real interns + tasks

## Detailed Task List

### 1. Core Orchestration – CrewAI Integration ✅ DONE
- [x] Install CrewAI + create Agent/Task/Crew per intern request
- [x] Map Job Description → CrewAI Role + Backstory + Goals
- [x] Task decomposition: Plan → Human Approve → Execute (tools + Claude)
- [x] Per-intern memory (short-term list + Notion long-term)
- [ ] Test with 3 sample interns (content, dev, ops) — manual end-to-end

### 2. Job Description Enforcement ✅ DONE
- [x] Define structured schema (Name, Role, Responsibilities, Tone, Tools, Autonomy Rules)
- [x] Conversational JD refiner (CrewAI helps user write complete JD via hire flow)
- [x] Validation + default approval rules on creation
- [x] Store in Notion Interns database

### 3. Tools System ✅ DONE
- [x] Create `ToolRegistry` (pre-built only, catalog + CrewAI instances)
- [x] Implement 5 tools:
  - [x] Notion query (read-only during reasoning)
  - [x] Web search (DuckDuckGo, no API key)
  - [x] Code exec (sandboxed Python subprocess)
  - [x] Slack post/reply (write tool, requires approval)
  - [x] Calendar stub (Phase 3 — Google Calendar)
- [x] JD → tool filtering (intern only sees assigned tools)
- [x] Tool calling via CrewAI + error handling
- [x] Slack message execution after approval

### 4. Commands & UX ✅ DONE (mention-based)
- [x] Mention-based commands (no slash commands — using `@jibsa` prefix):
  - `@jibsa list interns` / `team`
  - `@jibsa show alex's jd`
  - `@jibsa fire alex`
  - `@jibsa alex do <task>` / `@jibsa ask alex to <task>`
- [x] Conversational hiring flow with clarification + JD preview + approval
- [x] Block Kit approval buttons (✅ Approve / ❌ Reject)
- [x] Thread formatting with `[Intern Name — Role]` prefix

### 5. Persistence & State ✅ DONE
- [x] InternRegistry → Notion database (with caching)
- [x] Thread → intern mapping + approval state
- [x] Basic logging throughout

### 6. Documentation & Polish ✅ DONE
- [x] Update README.md with v0.6 multi-intern architecture + Mermaid diagram
- [x] Write CONTRIBUTING.md (dev setup, architecture, adding tools/integrations, testing)
- [x] Update .env.example with all required env vars (LLM API keys, Notion token)
- [x] Docker Compose + Dockerfile updated (removed Claude CLI dependency)
- [x] License + open-source badge (MIT)

### 7. Testing & Release — TODO
- [ ] Manual end-to-end tests with 3 interns + real tasks
- [ ] Verify Block Kit buttons work in live Slack
- [ ] Verify Slack post tool executes after approval
- [ ] Tag v1.0 + write release notes

## Out of Scope for v1.0
- Real per-intern Slack bots (custom @mentions)
- User-created custom tools
- Multi-workspace
- Cost tracking / billing
- Advanced autonomy levels
- Google Calendar live integration (stub only for now)

## Next Steps (→ v1.0)
1. **End-to-end testing** — Manual tests in live Slack with real interns
2. **Tag v1.0** — Release notes

## Future (post v1.0)
- **Phase 3**: Jira + Google Calendar + APScheduler (morning briefing, EOD review)
- **Phase 4**: Gmail + weekly digest + team interactions
- **Phase 5**: Setup wizard, audit logging, open-source polish
