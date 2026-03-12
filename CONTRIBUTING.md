# Contributing to Jibsa

Thanks for your interest in contributing to Jibsa! This guide covers development setup, architecture, and conventions.

## Development Setup

```bash
# Clone
git clone https://github.com/peterjhwang/jibsa-ai.git
cd jibsa-ai

# Create venv
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Run tests
python -m pytest tests/ -v
```

## Running the Bot

```bash
# Set up environment
cp .env.example .env
# Fill in SLACK_BOT_TOKEN, SLACK_APP_TOKEN, NOTION_TOKEN

cp config/notion_databases.yaml.example config/notion_databases.yaml
# Fill in your Notion database IDs

# Start
python -m src.app
```

## Architecture Overview

```
Slack message
    → app.py (Bolt event handler)
    → orchestrator.py (central router)
        → router.py (parse: hire? intern task? management? jibsa?)
        → hire_flow.py (conversational JD creation)
        → crew_runner.py (CrewAI Agent + Task + Crew)
            → tools/ (Notion, web search, code exec, Slack, calendar)
        → approval.py (propose-approve gate, Block Kit buttons)
        → integrations/ (Notion execution)
    → Slack reply
```

### Key Concepts

- **Orchestrator** (`orchestrator.py`): Central entry point. Routes messages, manages history, dispatches to hire flow / interns / Jibsa.
- **CrewRunner** (`crew_runner.py`): Builds CrewAI Agent + Task + Crew per request. Three entry points: `run_for_jibsa()`, `run_for_intern()`, `run_for_hire()`.
- **InternJD** (`models/intern.py`): Dataclass representing an intern's Job Description. Includes validation, per-intern memory, and formatting.
- **ToolRegistry** (`tool_registry.py`): Manages tool catalog + CrewAI instances. Filters tools per intern based on JD. Checks write-action permissions.
- **Propose-Approve**: Read-only tools execute during CrewAI reasoning. Write operations produce a JSON `action_plan` → Slack Block Kit buttons → execute after approval.

### Adding a New Tool

1. Create `src/tools/your_tool.py` — subclass `crewai.tools.BaseTool`
2. Add to `TOOL_CATALOG` in `src/tool_registry.py` (with `write_actions` list)
3. Add tool name to `VALID_TOOL_NAMES` in `src/models/intern.py`
4. Register instance in `orchestrator._register_crewai_tools()`
5. If it's a write tool, add execution handler in `orchestrator._execute_plan()`
6. Export from `src/tools/__init__.py`
7. Add tests in `tests/test_tools.py`

### Adding a New Integration

1. Create client in `src/integrations/your_service.py`
2. Add `enabled: false` entry in `config/settings.yaml` under `integrations`
3. Add env var to `.env.example`
4. Wire into orchestrator's `_execute_plan()` for write operations
5. Add setup docs in `docs/`

## Testing

```bash
# All tests
python -m pytest tests/ -v

# Specific file
python -m pytest tests/test_orchestrator.py -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

All external services are mocked in tests. No API keys or live connections needed.

### Test Files

| File | Covers |
|------|--------|
| `test_orchestrator.py` | Routing, approval, Block Kit buttons, Slack execution |
| `test_crew_runner.py` | CrewAI crew building, JSON extraction, LLM string format |
| `test_router.py` | Message parsing, intern routing, management commands |
| `test_hire_flow.py` | JD extraction, validation, session lifecycle |
| `test_intern_model.py` | InternJD validation, memory, formatting |
| `test_tool_registry.py` | Tool filtering, permissions, CrewAI instances |
| `test_tools.py` | All 5 CrewAI tools (Notion, web search, code exec, Slack, calendar) |
| `test_approval.py` | Approval state machine, keyword matching |
| `test_second_brain.py` | Notion schema-free reads/writes, page flattening |
| `test_intern_registry.py` | Intern CRUD, caching, Notion integration |

## Code Conventions

- **Python 3.12+** with type hints
- **No hardcoded schemas** — Notion operations discover property types at runtime
- **All writes require approval** — never bypass the propose-approve gate
- **Tests mock all external services** — no API keys in CI
- **CrewAI tools** subclass `BaseTool` from `crewai.tools`
- **Logging** via `logging.getLogger(__name__)` throughout

## Project Layout

```
src/
├── app.py                  # Slack Bolt entry + Block Kit action handlers
├── orchestrator.py         # Central router and plan executor
├── crew_runner.py          # CrewAI engine (Agent/Task/Crew)
├── router.py               # Message parsing and intern routing
├── hire_flow.py            # Conversational JD builder
├── intern_registry.py      # Intern CRUD (Notion-backed)
├── tool_registry.py        # Tool catalog + permission checking
├── approval.py             # Approval state machine
├── models/
│   └── intern.py           # InternJD dataclass
├── tools/
│   ├── notion_read_tool.py # Notion queries (read-only)
│   ├── web_search_tool.py  # DuckDuckGo search
│   ├── code_exec_tool.py   # Sandboxed Python
│   ├── slack_tool.py       # Slack post (write)
│   └── calendar_tool.py    # Calendar stub
└── integrations/
    ├── notion_client.py    # Notion SDK wrapper
    └── notion_second_brain.py  # Schema-free PARA ops
```
