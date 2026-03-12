# Jibsa AI · 집사

**Create Unlimited AI Interns in Slack**

Jibsa lets any team member instantly “hire” custom AI interns — content writers, developers, ops assistants, researchers — right inside your Slack workspace.

Each intern gets:
- A **Job Description** (persona + responsibilities + approval rules)
- Assigned **tools** (Notion today, more coming)
- Autonomous execution with **human-in-the-loop safety** (propose → approve)

Open-source Python app. Built for SaaS teams who want to scale with AI teammates.

---

## Why Jibsa?

Stop hiring juniors for repetitive work.  
Any Slack user can create a specialized AI intern in seconds and start delegating tasks.

**Core Principles**
- Safety: Every external action requires explicit approval
- Conversational: Natural language hiring and tasking
- Extensible: Easy to add tools and new interns
- Self-hostable & MIT-licensed

---

## Current Status – v0.5 Complete ✅

**What’s now live**
- Conversational hiring: `@jibsa hire a content marketing intern who...`
- Multi-intern management & registry
- Smart message routing (`@jibsa ask Alex to...`)
- Job description storage + loading
- Inherited propose-approve flow
- Full Notion Second Brain (schema-free PARA system)

**How it works**
1. Mention `@jibsa` to hire or assign tasks
2. Jibsa refines the job description conversationally
3. Intern is created with its own persona and tools
4. Tasks run in threads with **[Intern Name]** prefixes

---

## Quick Start

```bash
git clone https://github.com/peterjhwang/jibsa-ai.git
cd jibsa-ai

uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

cp .env.example .env          # Add Slack + Notion tokens
python -m src.app
```

Or with Docker:
```bash
cp .env.example .env
docker-compose up -d
```

Talk to Jibsa in your designated channel.

---

## Features

- AI-assisted hiring flow
- Multiple concurrent interns
- Notion tool integration (create/update tasks, projects, notes, journals, expenses, etc.)
- Threaded task conversations
- Propose-approve gate on all writes

---

## Roadmap & Next Steps

### v0.6 – Immediate (1–2 weeks)
1. Integrate **CrewAI** for structured task orchestration per intern
2. Enforce structured Job Description format (Persona, Tools, Approval Rules)
3. Add intern memory & context persistence
4. Expand tools: web search + basic code interpreter
5. Improve hiring UX (better clarification + JD refinement)

### v1.0 – Target April 2026
- Stable multi-intern platform with 4–5 tools
- `/interns list` and status commands
- Better logging & error handling
- Polished documentation + contribution guide

### Future (v1.5+)
- Real per-intern Slack bots (custom @mentions)
- User-created tools (“make a tool that pulls Stripe data”)
- Advanced autonomy levels
- Cost tracking & multi-workspace support

---

## Project Structure (key new files)

```
src/
├── app.py
├── orchestrator.py
├── router.py              # New: routes to interns
├── intern_registry.py     # New: stores JDs
├── hire_flow.py           # New: conversational hiring
├── claude_runner.py
└── integrations/
    └── notion_second_brain.py
```

---

## Contributing

We love pull requests!  
Especially welcome:
- New tool integrations
- CrewAI workflow improvements
- Hiring flow enhancements

Open issues or ping me on X (@PeterHwangDS).