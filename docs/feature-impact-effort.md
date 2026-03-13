# Jibsa Feature Impact/Effort Analysis

_Date: 2026-03-14_

## Scoring Guide

- **Impact**: How much value this delivers to NZ/AU SaaS teams (1-5)
- **Effort**: Engineering effort to build (1-5, where 1=low, 5=very high)
- **Priority Score**: Impact / Effort (higher = better ROI)
- **Dependencies**: What needs to exist first

---

## Summary Matrix

| # | Feature | Impact | Effort | Priority | Depends On |
|---|---------|--------|--------|----------|------------|
| 1 | Standup & Async Coordination | 5 | 2 | **2.50** | Phase 3 (Jira + Cron) |
| 2 | Jira/Linear Triage & Sprint Hygiene | 5 | 3 | **1.67** | Jira integration |
| 3 | Meeting Notes → Action Items → Tickets | 4 | 2 | **2.00** | Jira integration |
| 4 | Customer Support / Intercom Triage | 4 | 4 | **1.00** | New integration (Intercom/Zendesk) |
| 5 | Compliance & Documentation (NZ/AU) | 3 | 3 | **1.00** | Notion Second Brain (done) |
| 6 | Financial Ops — Xero Integration | 4 | 4 | **1.00** | New integration (Xero API) |
| 7 | Incident Response Runbooks | 4 | 3 | **1.33** | Log/status page integrations |
| 8 | Sales Pipeline / CRM Light | 3 | 2 | **1.50** | Notion Second Brain (done) |
| 9 | Multi-Timezone Scheduling | 4 | 2 | **2.00** | Phase 3 (Google Calendar) |
| 10 | Content & Social | 3 | 1 | **3.00** | File/image gen (done) |

---

## Recommended Build Order

### Tier 1 — Build Now (high ROI, low/medium effort)

**10. Content & Social** — Priority Score: 3.00
- Impact: 3 | Effort: 1
- Why first: Most of the infrastructure already exists (file gen, image gen, Notion). This is mostly prompt engineering and a new intern archetype. Ship it in days, not weeks.
- What to build: LinkedIn post drafting from Notion content, release notes from git/Jira changelogs, blog outline generation.
- New code: Prompt templates, possibly a changelog-parsing tool. No new integrations.

**1. Standup & Async Coordination** — Priority Score: 2.50
- Impact: 5 | Effort: 2
- Why early: This is the flagship use case. Phase 3 cron + Jira gets you 80% there. The timezone pain is real and daily — high retention driver.
- What to build: Cron-triggered standup collection (poll Slack threads + Jira activity), timezone-aware digest formatting, morning post to designated channel.
- New code: Cron job definition, Slack thread scanner tool, digest formatter. Jira read integration (Phase 3).
- Risk: Low. Builds directly on planned Phase 3 work.

**9. Multi-Timezone Scheduling** — Priority Score: 2.00
- Impact: 4 | Effort: 2
- Why early: Falls out naturally from Phase 3 Google Calendar work. Daily-use feature for any cross-timezone team.
- What to build: Timezone overlap calculator, free/busy lookup via Google Calendar API, calendar hold proposal through approve flow.
- New code: Timezone utility, Google Calendar read tool, scheduling propose action.
- Risk: Low. Google Calendar is already planned.

**3. Meeting Notes → Action Items → Tickets** — Priority Score: 2.00
- Impact: 4 | Effort: 2
- Why early: Once Jira integration exists (from #1/#2), this is mostly an LLM extraction task. Users paste notes into Slack, intern proposes tickets. The approve flow handles safety perfectly.
- What to build: Meeting notes parser (LLM-based action item extraction), Jira ticket proposal formatter, batch-create through approve flow.
- New code: Prompt template for extraction, Jira write action (batch ticket creation).
- Risk: Low. The hard part (Jira integration) is already needed for #1.

### Tier 2 — Build Next (high impact, medium effort)

**2. Jira/Linear Triage & Sprint Hygiene** — Priority Score: 1.67
- Impact: 5 | Effort: 3
- Why Tier 2: High impact but needs deeper Jira integration than read-only (labeling, moving tickets, bulk operations). Worth building once the Jira foundation from Tier 1 is solid.
- What to build: Ticket classifier (by component/priority), stale issue detector, sprint health dashboard, auto-label proposals through approve flow.
- New code: Jira query tools (JQL), classification prompts, sprint metrics calculator, Jira write actions (label, transition).
- Risk: Medium. JQL complexity varies across team setups. Needs good defaults.

**8. Sales Pipeline / CRM Light** — Priority Score: 1.50
- Impact: 3 | Effort: 2
- Why Tier 2: Notion Second Brain already exists. This is a new intern archetype with pipeline-specific Notion templates and follow-up reminders. Low effort but niche impact.
- What to build: Pipeline Notion template, deal-tracking queries, stale-lead alerts (cron), outreach email drafting.
- New code: Notion pipeline template, reminder cron jobs, email draft tool (Phase 4 Gmail prerequisite helps here).

**7. Incident Response Runbooks** — Priority Score: 1.33
- Impact: 4 | Effort: 3
- Why Tier 2: High value during incidents, but incidents are (hopefully) infrequent. Needs integrations with logging/monitoring platforms that vary per team.
- What to build: Status page checker tool, error log summarizer, customer-facing status draft, Slack incident channel management.
- New code: Generic log reader tool (configurable per platform), status page API tool, incident comms templates.
- Risk: Medium. Log platforms vary widely (Datadog, Sentry, CloudWatch). May need plugin architecture.

### Tier 3 — Build Later (high effort or narrow audience)

**5. Compliance & Documentation (NZ/AU)** — Priority Score: 1.00
- Impact: 3 | Effort: 3
- Why later: Valuable but narrow. Requires deep domain knowledge in NZ/AU privacy law baked into prompts. The audit capability (scan Notion for gaps) is interesting but the legal accuracy bar is high.
- What to build: Privacy policy auditor (scan Notion pages against checklist), PIA draft generator, compliance checklist tracker.
- New code: Compliance checklists (NZ Privacy Act 2020, AU Privacy Act 1988), Notion page scanner, gap analysis prompts.
- Risk: High. Legal accuracy is critical — wrong compliance advice is worse than no advice. Needs disclaimers and human review emphasis.

**4. Customer Support / Intercom Triage** — Priority Score: 1.00
- Impact: 4 | Effort: 4
- Why later: New integration (Intercom/Zendesk/Freshdesk APIs), webhook handling, sentiment analysis, escalation routing. Significant new surface area.
- What to build: Support platform integration (Intercom API), message ingestion, sentiment classifier, draft response generator, escalation rules engine.
- New code: Full new integration module, sentiment tool, response templates, routing logic.
- Risk: Medium-high. Each support platform has different APIs. The approve flow mitigates auto-reply risk, but response quality needs to be very good.

**6. Financial Ops — Xero Integration** — Priority Score: 1.00
- Impact: 4 | Effort: 4
- Why later: Xero OAuth2 + API integration is non-trivial. Financial data handling needs extra security consideration. But the NZ/AU differentiation is very strong — this could be a headline feature once the platform is mature.
- What to build: Xero OAuth2 flow, cash flow summary tool, overdue invoice alerts, expense reconciliation, monthly snapshot generator.
- New code: Full Xero integration module, financial report templates, reconciliation logic.
- Risk: High. Financial data requires strict security, potential regulatory considerations. OAuth2 token management adds complexity. But massive differentiator.

---

## Build Phases Alignment

| Phase | Features | Timeline Dependency |
|-------|----------|-------------------|
| Phase 3 (Jira + Calendar + Cron) | #1, #2, #3, #9 | Current roadmap |
| Phase 4 (Gmail + Digest) | #8 (email drafts), #10 (content distribution) | After Phase 3 |
| Phase 5 (New integrations) | #4 (Intercom), #6 (Xero), #7 (Logging) | After Phase 4 |
| Anytime (prompt-only) | #5 (Compliance), #10 (Content) | Minimal deps |

---

## Key Takeaway

The highest-ROI path: **Content (#10) → Standup (#1) → Scheduling (#9) → Meeting Notes (#3) → Jira Triage (#2)**. This sequence builds on each previous feature's infrastructure, keeps effort low per step, and delivers the most visible NZ/AU timezone value early. Xero (#6) is the long-term differentiator to invest in once the platform is proven.
