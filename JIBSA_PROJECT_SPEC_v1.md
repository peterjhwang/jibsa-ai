# Jibsa AI – v1 Project Specification
Last updated: March 2026
Repo: https://github.com/peterjhwang/jibsa-ai

## Tagline
Create unlimited AI Interns in Slack.  
Give them job descriptions, tools, and tasks — they work autonomously but always propose before acting (configurable).

## Core Problem Solved
SaaS teams need fast, low-cost scaling.  
Instead of hiring juniors, any team member can spawn tailored AI agents (marketing intern, dev helper, ops assistant, etc.) in seconds — all inside Slack.

## What is an "Intern"?
- AI agent (powered by Claude via CLI for now)
- Defined by: Job Description (persona, responsibilities, tone, approval rules)
- Equipped with: Pre-defined tools (Notion, future: Jira, Calendar, code exec, search…)
- Works in: Threads or DMs
- Behavior: Proposes plan/output → waits for human approval (unless JD explicitly allows autonomy)
- Interaction: Mention @jibsa + task → routes to correct intern persona

## Target Users (v1)
- Anyone in the Slack workspace (no admin gate)
- Internal company use (open-source for self-hosting by others later)

## v1 Architecture Decision (Compromise for Speed)
Goal: Prove multi-intern concept quickly without managing dozens of Slack apps.

Chosen path → Hybrid (leans toward your B preference but starts simpler):
- Single Slack app/bot → @jibsa
- Interns = internal personas managed by one orchestrator
- Each intern has its own:
  - Name (e.g. "Alex – Content Intern")
  - Job Description (stored in DB/file)
  - Tool subset
  - Approval rules
- In Slack: Users say "@jibsa hire ..." or "@jibsa ask Alex to ..."
- Replies prefixed: **[Alex – Content Intern]** → mimics separate identity
- Future v2: Spin up real per-intern Slack bots (App installation API + dynamic tokens)

This gives 80% of the UX value with 20% complexity.

## Key Features – v1 Scope

1. Hire / Create Intern (conversational)
   - User: "@jibsa hire a content marketing intern who writes LinkedIn posts and tracks campaign performance"
   - Jibsa: Asks clarifying questions if needed → generates/refines clear Job Description
   - Saves JD (YAML/JSON in Notion or local file)
   - Assigns default tools + approval rules
   - Confirms: "Intern 'Alex' created. Ready to assign tasks."

2. Job Description Structure (required sections – enforced)
   - Name / Nickname
   - Role / Persona
   - Key Responsibilities
   - Tone & Communication Style
   - Tools Allowed (from pre-defined list)
   - Autonomy & Confirmation Rules
     Examples:
     - "Always propose before: sending email, updating Notion production data, posting anywhere"
     - "Research & drafts → fully autonomous; final publish → requires approval"
     - "Ask only if output > $X cost or touches customer data"

3. Tools (Simple start – pre-built only)
   - Current: Notion (create/update tasks/projects/notes/expenses/journals…)
   - Planned short-term: Web search, basic code interpreter, Slack post/message
   - Assignment: Listed in JD → Jibsa only exposes those tools to that intern

4. Task Assignment & Work
   - "@jibsa ask Alex to write 3 LinkedIn posts about our new feature"
   - OR "@jibsa Alex, research competitors in CRM space"
   - Jibsa routes to persona → runs CrewAI-like flow:
     - Agent plans (proposes steps)
     - Human approves/revises
     - Executes approved steps (tools + Claude)
     - Delivers result in thread

5. Tech Stack Evolution
   - Keep: Slack Bolt (Socket Mode), Claude CLI runner, Notion schema-free
   - Add: **CrewAI** (core orchestration – roles, tasks, sequential/hierarchical process)
   - Storage: Notion (intern JDs, thread states) + local JSON fallback
   - State: Per-thread + per-intern memory (CrewAI handles short-term; Notion for long-term)

## Non-Goals for v1
- Real per-intern Slack bots (too much OAuth/app management)
- User-created custom tools (Python functions or dynamic tool gen)
- Multi-workspace
- Advanced billing/token tracking
- Full voice/image multimodal

## Roadmap Sketch
v0.5 (next 2–4 weeks)  → Multi-persona in single bot + CrewAI integration + hire flow
v1.0                      → Stable, documented, 4–5 pre-built tools, good README
v1.5                      → Custom @mentions via Slack user groups or display hacks
v2.0                      → Dynamic Slack app creation per intern (your original B vision)

## Contribution / Open-Source Notes
- License: MIT
- Self-host friendly (Docker-compose ready)
- Clear docs: How to add tools, change LLM backend, extend JDs

## Next Immediate Steps
1. Refactor current single-Jibsa → multi-persona routing
2. Integrate CrewAI (start with simple Crew per task)
3. Build conversational hire flow (Claude helps refine JD)
4. Enforce JD structure (sections + approval rules)
5. Update README with this spec + architecture diagram

Feel free to tweak any section — especially JD template, tool list priorities, or if you want to push harder toward real per-bot interns sooner.