# Jibsa Platform Enhancements — Impact/Effort Analysis

_Date: 2026-03-14_

---

## Summary Matrix

| # | Feature | Impact | Effort | Priority | Depends On |
|---|---------|--------|--------|----------|------------|
| 1 | Skills Marketplace (JD Template Library) | 4 | 2 | **2.00** | Intern registry (done) |
| 2 | Doctor / Health Check CLI | 4 | 2 | **2.00** | Config validation (done) |
| 3 | Multi-Model Failover | 3 | 2 | **1.50** | CrewAI LLM config (done) |

All three are **low-to-medium effort** because they build directly on existing infrastructure.

---

## 1. Skills Marketplace — JD Template Library

**Impact: 4 | Effort: 2 | Priority: 2.00**

### What Exists Today

- `InternJD` dataclass with full validation (name, role, responsibilities, tools, tone, autonomy rules)
- `VALID_TOOL_NAMES` set controls per-intern tool access
- Hire flow already creates interns from structured JSON extracted by the LLM
- Notion-backed intern registry with CRUD

### What to Build

A library of pre-built JD templates (YAML files) that users can import via a Slack command like `@jibsa hire from template sprint-hygiene`.

**Concrete implementation:**

```
config/templates/
  finance-intern.yaml
  sprint-hygiene-intern.yaml
  compliance-intern.yaml
  standup-intern.yaml
  content-intern.yaml
```

Each template is a YAML file mapping directly to `InternJD` fields:

```yaml
name: Sprint Hygiene
role: Sprint health monitor and Jira triage assistant
responsibilities:
  - Triage new Jira tickets by component and priority
  - Flag stale issues older than 14 days
  - Produce weekly sprint health summary
tone: Direct and data-driven
tools_allowed: [notion, web_search]
autonomy_rules: Always propose before acting on ticket changes
```

**New code needed:**

| Component | Change | Size |
|-----------|--------|------|
| `src/template_registry.py` | Load/list/get templates from `config/templates/` | ~60 lines |
| `src/router.py` | Add `hire from template <name>` command parsing | ~15 lines |
| `src/hire_flow.py` | Add `start_from_template()` that pre-fills JD, goes straight to CONFIRMING | ~30 lines |
| Template YAML files | 5-8 starter templates | ~20 lines each |

**Total: ~200 lines of new code.**

### Why It's Easy

- The entire JD data model and validation pipeline exists. Templates are just pre-filled `InternJD` instances.
- The hire flow already has a CONFIRMING state — templates skip GATHERING and go straight to "here's the JD, want to create this intern?"
- No new integrations, no new APIs, no new dependencies.
- Community contributions = PRs adding YAML files. Very low barrier.

### Risks

- Low. Template validation uses the existing `InternJD.validate()` pipeline.
- Versioning templates as Jibsa adds new tools/features needs a light migration story (but YAML is flexible enough to not break).

---

## 2. Doctor / Health Check CLI

**Impact: 4 | Effort: 2 | Priority: 2.00**

### What Exists Today

- Pydantic config validation (`src/config_schema.py`) already checks settings.yaml structure, types, and ranges
- Notion client with schema auto-discovery (`src/integrations/notion_second_brain.py`)
- Slack Bolt with Socket Mode connection
- Environment variables for API keys (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `NOTION_TOKEN`, `ANTHROPIC_API_KEY`)

### What to Build

A CLI command (`python -m src.doctor` or `jibsa doctor`) that runs a checklist of health checks and prints a pass/fail report.

**Checks to implement:**

| Check | How | Complexity |
|-------|-----|------------|
| Config file valid | Run existing Pydantic validation on `settings.yaml` | Trivial — already exists |
| Slack tokens present | Check env vars `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Trivial |
| Slack connection works | Call `client.auth_test()` | Simple — Slack SDK method |
| Slack bot permissions | Check `auth_test` scopes vs required scopes list | Simple |
| Notion token valid | Call `notion.users.me()` | Simple — SDK method |
| Notion Interns DB exists | Query for Interns database by name | Simple — reuse `_discover_databases()` |
| Notion schema correct | Check required properties (Name, Role, etc.) exist | Medium — reuse auto-discover logic |
| LLM API key present | Check `ANTHROPIC_API_KEY` (or relevant provider key) | Trivial |
| LLM API reachable | Send a minimal completion request | Simple |
| Tool dependencies | Check optional deps (duckduckgo-search, etc.) are importable | Simple |

**New code needed:**

| Component | Change | Size |
|-----------|--------|------|
| `src/doctor.py` | Health check runner with pass/fail/warn output | ~150 lines |
| `src/__main__.py` or CLI entry | Add `doctor` subcommand | ~10 lines |

**Total: ~160 lines of new code.**

### Why It's Easy

- Most checks are one API call + error handling. The hard validation logic (Pydantic schema, Notion auto-discover) already exists.
- No new dependencies needed.
- Output is just formatted text to stdout — no UI complexity.

### Risks

- Very low. Read-only checks, no side effects.
- API rate limits are not a concern for single check calls.

---

## 3. Multi-Model Failover

**Impact: 3 | Effort: 2 | Priority: 1.50**

### What Exists Today

- `_build_llm_string()` in `crew_runner.py` produces a single `"provider/model"` string (e.g., `"anthropic/claude-sonnet-4-20250514"`)
- Config has `llm.provider` and `llm.model` fields (single values)
- CrewAI natively supports LLM strings for multiple providers (anthropic, openai, etc.)
- `_run_with_timeout()` already wraps crew execution with error handling

### What to Build

Add a `fallback` list to the LLM config and retry logic in the crew runner.

**Config change:**

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  fallback:
    - provider: openai
      model: gpt-4o
    - provider: anthropic
      model: claude-haiku-4-5-20251001
```

**New code needed:**

| Component | Change | Size |
|-----------|--------|------|
| `src/config_schema.py` | Add `fallback` list to `LLMConfig` Pydantic model | ~10 lines |
| `src/crew_runner.py` | Build fallback LLM strings in `__init__` | ~10 lines |
| `src/crew_runner.py` | Wrap `_run_crew()` with retry loop over fallback LLMs | ~30 lines |
| `config/settings.yaml` | Add example fallback config (commented out) | ~5 lines |

**Total: ~55 lines of new code.**

### Why It's Easy

- CrewAI already handles different providers via the LLM string format. Switching from `"anthropic/claude-sonnet-4-20250514"` to `"openai/gpt-4o"` is just a string swap.
- The retry pattern is straightforward: catch the API error, rebuild the Agent with the next LLM string, retry `Crew.kickoff()`.
- No new dependencies (users already need the provider SDK installed for their fallback provider, but that's their concern).

### Implementation Detail

```python
def _run_crew(self, agent, task, ...):
    llm_chain = [self._llm_string] + self._fallback_llm_strings
    last_error = None
    for llm in llm_chain:
        try:
            agent.llm = llm
            crew = Crew(agents=[agent], tasks=[task], ...)
            return crew.kickoff()
        except (APIError, Timeout) as e:
            last_error = e
            logger.warning(f"LLM {llm} failed, trying next fallback...")
    raise last_error
```

### Risks

- Medium. Different LLMs may produce different output formats — if Claude returns structured JSON but GPT-4o doesn't follow the same format, downstream parsing could break. Mitigation: the prompt templates should be model-agnostic (they mostly are already).
- Users need API keys for all configured providers.
- CrewAI's internal caching/memory may behave differently across providers — needs testing.

---

## Comparison to OpenClaw

| Feature | OpenClaw | Jibsa (proposed) | Jibsa Advantage |
|---------|----------|-------------------|-----------------|
| Skills marketplace | ClawHub (community plugin system) | JD template library (YAML files) | Simpler, lower barrier to contribute, no plugin API to maintain |
| Health check | `openclaw doctor` CLI | `jibsa doctor` CLI | Deeper checks (Notion schema, Slack permissions, LLM reachability) |
| Multi-model failover | Not built-in | Config-driven fallback chain | Explicit, user-controlled, no magic |

---

## Recommended Build Order

1. **Doctor** — Immediate DX win. Reduces support burden from day one. Can ship independently.
2. **JD Templates** — Enables the "try Jibsa in 5 minutes" onboarding story. Ship 5 starter templates.
3. **Multi-Model Failover** — Nice reliability improvement, but less urgent unless users report outage pain.

All three can be built in parallel by different contributors since they touch different parts of the codebase with no overlap.
