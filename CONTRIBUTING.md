# Contributing to Jibsa

Thanks for your interest in contributing! This guide covers development setup, architecture, and conventions.

## Quick Setup

```bash
# Clone and run the setup wizard
git clone https://github.com/peterjhwang/jibsa-ai.git
cd jibsa-ai
./scripts/setup.sh              # bootstrap: uv, .venv, compile, install
python -m src.setup              # interactive config wizard (first time)
./scripts/doctor.sh              # verify everything works
./scripts/test.sh                # run tests
```

## Running the Bot

```bash
./scripts/run.sh                 # start Jibsa (Socket Mode)
LOG_LEVEL=DEBUG ./scripts/run.sh # verbose logging
```

## Architecture Overview

```
Slack message
    → app.py (Bolt event handler + graceful shutdown)
    → orchestrator.py (central router)
        → router.py (parse: hire? intern? team? management? connect?)
        → hire_flow.py (conversational JD creation)
        → crew_runner.py (CrewAI Agent + Task + Crew)
            → tools/ (Notion, Jira, Confluence, Calendar, Gmail, Web, Code, etc.)
        → approval.py (propose-approve gate, Block Kit buttons)
        → integrations/ (Notion, Jira, Confluence, Calendar, Gmail execution)
        → audit_store.py (persistent audit logging)
    → Slack reply
```

### Key Concepts

- **Orchestrator** (`orchestrator.py`): Central entry point. Routes messages, manages history, dispatches to hire flow / interns / Jibsa.
- **CrewRunner** (`crew_runner.py`): Builds CrewAI Agent + Task + Crew per request. Agents clarify ambiguous requests before acting.
- **InternJD** (`models/intern.py`): Dataclass for intern Job Descriptions. Stored in SQLite.
- **ToolRegistry** (`tool_registry.py`): Tool catalog + per-intern permission filtering.
- **CredentialStore** (`integrations/credential_store.py`): Fernet-encrypted per-user OAuth tokens in SQLite.
- **AuditStore** (`integrations/audit_store.py`): Persistent action log in SQLite.
- **Clarify-Propose-Approve**: Read tools execute during reasoning. Write operations produce a JSON `action_plan` → Block Kit buttons → execute after approval.

### Adding a New Tool

1. Create `src/tools/your_tool.py` — subclass `crewai.tools.BaseTool`
2. Add to `TOOL_CATALOG` in `src/tool_registry.py` (with `write_actions` list)
3. Add tool name to `VALID_TOOL_NAMES` in `src/models/intern.py`
4. Register instance in `orchestrator._register_crewai_tools()`
5. If it's a write tool, add execution handler in `orchestrator._execute_plan()`
6. Add valid actions to `config/prompts/system.txt`
7. Export from `src/tools/__init__.py`
8. Add tests

### Adding a New Integration

1. Create client in `src/integrations/your_service.py` (follow `jira_client.py` pattern)
2. Add `enabled: false` in `config/settings.yaml` under `integrations`
3. Add env vars to `.env.example`
4. Wire into orchestrator's `_execute_plan()` for write operations
5. Add audit logging for key actions
6. Add setup docs in `docs/`
7. Add tests

### Adding an Audit Event

Call `self.audit.log()` at the action point:
```python
self.audit.log(
    action="your_action_name",
    user_id=current_user_id.get(""),
    service="service_name",
    details={"key": "value"},
    status="ok",  # or "error", "partial"
    thread_ts=thread_ts,
)
```

## Testing

```bash
./scripts/test.sh                # all tests
./scripts/test.sh -k "test_name" # specific test
./scripts/test.sh -x             # stop on first failure
./scripts/test.sh --cov=src      # with coverage
```

All external services are mocked in tests. No API keys or live connections needed.

## Code Conventions

- **Python 3.12+** with type hints
- **No hardcoded schemas** — Notion operations discover property types at runtime
- **All writes require approval** — never bypass the propose-approve gate
- **Tests mock all external services** — no API keys in CI
- **CrewAI tools** subclass `BaseTool` from `crewai.tools`
- **Logging** via `logging.getLogger(__name__)` throughout
- **SQLite stores** use `threading.Lock()` for write operations
- **Audit all significant actions** — proposals, approvals, executions, CRUD

## Pull Request Guidelines

- Create a feature branch from `main`
- Keep PRs focused — one feature or fix per PR
- Add tests for new functionality
- Run `./scripts/test.sh` before submitting
- Use descriptive commit messages
