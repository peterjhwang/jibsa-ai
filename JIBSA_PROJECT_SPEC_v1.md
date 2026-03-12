# Jibsa AI – v1 Project Tasks
**Version**: v1.0 Target (post v0.5)  
**Last updated**: March 13, 2026  
**Owner**: Peter Hwang  
**Repo**: https://github.com/peterjhwang/jibsa-ai  
**Goal**: Stable, production-ready multi-AI-intern platform with CrewAI orchestration

## Overall Vision (1 sentence)
Any Slack user can create custom AI interns (with job descriptions + tools + approval rules) and delegate tasks — all inside one @jibsa bot for v1.

## Current Status – v0.5 ✅ (18 min ago)
- Conversational hiring flow (`@jibsa hire ...`)
- Intern registry + persona storage
- Smart message routing (`@jibsa ask Alex to ...`)
- Threaded work with **[Intern Name]** prefix
- Propose-approve gate inherited
- Notion Second Brain (schema-free)

**README still shows old single-secretary version** → will update after this doc.

## v1.0 Success Criteria
- 4–5 pre-built tools working
- CrewAI orchestration per intern
- Structured JD enforcement
- Clean commands (`/interns list`, `/interns delete`, etc.)
- Self-hostable, documented, MIT-ready for open-source

## Detailed Task List (Priority + Owner + Est. Time)

### 1. Core Orchestration – CrewAI Integration (HIGH | Peter | 3–4 days)
- [ ] Install CrewAI + create one `InternCrew` class per intern
- [ ] Map Job Description → CrewAI Role + Backstory + Goals
- [ ] Task decomposition: Plan → Human Approve → Execute (tools + Claude)
- [ ] Persistent memory (CrewAI short-term + Notion long-term)
- [ ] Test with 3 sample interns (content, dev, ops)

### 2. Job Description Enforcement (HIGH | Peter | 2 days)
- [ ] Define strict YAML/JSON schema (sections: Name, Persona, Responsibilities, Tools, Approval Rules)
- [ ] Conversational JD refiner (Claude helps user write complete JD)
- [ ] Validation + default approval rules on creation
- [ ] Store in Notion + local fallback

### 3. Tools System – Simple Start (MED | Open or Peter | 3 days)
- [ ] Create `ToolRegistry` (pre-built only)
- [ ] Implement 4 new tools:
  - Web search (Tavily or DuckDuckGo)
  - Basic code interpreter (safe sandbox)
  - Slack post/reply
  - Calendar stub (future Google)
- [ ] JD → tool filtering (intern only sees assigned tools)
- [ ] Tool calling via Claude + error handling

### 4. Commands & UX (MED | Peter | 2 days)
- [ ] Slash commands:
  - `/interns list`
  - `/interns status <name>`
  - `/interns delete <name>`
  - `/task <intern> <description>`
- [ ] Improve hiring flow with clarification questions + preview
- [ ] Better thread formatting (`[Alex] Plan:` + approval buttons)

### 5. Persistence & State (MED | Peter | 1 day)
- [ ] InternRegistry → Notion database (or SQLite for speed)
- [ ] Thread → intern mapping + approval state
- [ ] Basic logging + error recovery

### 6. Documentation & Polish (HIGH | Peter | 1–2 days)
- [ ] Update README.md (use the concise version I gave earlier)
- [ ] Add this V1_PROJECT_TASKS.md to repo
- [ ] Write CONTRIBUTING.md + architecture diagram (Mermaid)
- [ ] Update .env.example + docker-compose
- [ ] License + open-source badge

### 7. Testing & Release (HIGH | Peter | 1 day)
- [ ] Manual tests with 3 interns + real tasks
- [ ] Basic pytest coverage for router + registry
- [ ] Tag v1.0 + write release notes

## Out of Scope for v1.0
- Real per-intern Slack bots (custom @mentions)
- User-created custom tools
- Multi-workspace
- Cost tracking / billing
- Advanced autonomy levels

## Suggested Timeline
- Mar 13–17 → CrewAI + JD enforcement + tools (core)
- Mar 18–20 → Commands + polish
- Mar 21 → Testing + release v1.0

## Next Immediate Steps (do these today)
1. Create this file (`V1_PROJECT_TASKS.md`) and push
2. Start CrewAI integration (task 1)
3. Update README.md with the concise version I sent earlier

---

**Ready to ship v1.0 by end of March.**  
Mark tasks as you go or assign to contributors.  

Ping me when you want:
- CrewAI starter code template
- JD YAML schema example
- Mermaid architecture diagram
- Or the next batch of code for any task above.

Let’s get this merged and tagged! 🚀