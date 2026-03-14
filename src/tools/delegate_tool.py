"""
DelegateToInternTool — CrewAI tool for inter-intern delegation.

Allows an intern to delegate a subtask to another intern mid-reasoning.
The target intern runs a mini crew, and the result is returned as a
string to the calling agent.

Key constraint: delegated interns do NOT receive the delegation tool,
preventing infinite recursion.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..intern_registry import InternRegistry
    from ..tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class DelegateInput(BaseModel):
    """Input schema for delegation."""
    intern_name: str = Field(
        ...,
        description="Name of the intern to delegate to (e.g. 'sarah', 'alex')",
    )
    task: str = Field(
        ...,
        description="The subtask to delegate — be specific about what you need",
    )


class DelegateToInternTool(BaseTool):
    name: str = "Delegate to Intern"
    description: str = (
        "Ask another intern to help with a subtask. Use this when you need "
        "expertise or tools you don't have. The other intern will work on "
        "your subtask and return the result. Be specific about what you need."
    )
    args_schema: Type[BaseModel] = DelegateInput

    # Set via factory — avoids pydantic validation issues
    _intern_registry: Any = None
    _crew_runner: Any = None
    _tool_registry: Any = None
    _config: dict = {}
    _notion: Any = None

    def _run(self, intern_name: str, task: str) -> str:
        if not self._intern_registry or not self._crew_runner:
            return "Delegation is not configured."

        # Look up target intern
        intern = self._intern_registry.get_intern(intern_name.strip())
        if not intern:
            available = self._intern_registry.get_intern_names()
            return (
                f"No intern named '{intern_name}'. "
                f"Available interns: {', '.join(available) or 'none'}"
            )

        logger.info("Delegation: → %s: %.80s", intern.name, task)

        # Build backstory (same as orchestrator._handle_intern_request)
        responsibilities = "\n".join(f"- {r}" for r in intern.responsibilities)
        tools_desc = self._tool_registry.get_tool_descriptions_for_prompt(intern) if self._tool_registry else ""
        backstory = (
            f"You are {intern.name}, a {intern.role}.\n\n"
            f"Responsibilities:\n{responsibilities}\n\n"
            f"Communication style: {intern.tone}\n\n"
            f"Tools available:\n{tools_desc}\n\n"
            f"You have been asked to help with a specific subtask by a colleague. "
            f"Focus on completing this subtask and returning a clear, useful result. "
            f"For read-only work, respond directly. "
            f"For write operations, describe what SHOULD be done (the requesting intern will handle approval)."
        )

        # Get tools for the target intern — WITHOUT delegation tool (prevents recursion)
        crewai_tools = []
        if self._tool_registry:
            all_tools = self._tool_registry.get_crewai_tools_for_intern(intern)
            crewai_tools = [
                t for t in all_tools
                if not isinstance(t, DelegateToInternTool)
            ]

        # Get optional Notion context
        notion_context = ""
        if self._notion:
            try:
                ctx = self._notion.get_context_for_request(task)
                if ctx:
                    notion_context = ctx
            except Exception:
                pass

        # Run mini crew for the target intern
        try:
            response = self._crew_runner.run_for_intern(
                user_message=task,
                intern_name=intern.name,
                intern_role=intern.role,
                intern_backstory=backstory,
                tools=crewai_tools,
                notion_context=notion_context,
            )
        except Exception as e:
            logger.error("Delegation to %s failed: %s", intern.name, e)
            return f"Delegation to {intern.name} failed: {e}"

        # If the delegated intern returned an action plan, summarize it as text
        # (can't approve mid-delegation — the primary intern handles that)
        if isinstance(response, dict) and response.get("type") == "action_plan":
            summary = response.get("summary", "")
            steps = response.get("steps", [])
            step_descs = [s.get("description", s.get("action", "")) for s in steps]
            steps_text = "\n".join(f"  - {d}" for d in step_descs)
            return (
                f"[{intern.name} proposes the following actions (needs user approval)]\n"
                f"Summary: {summary}\n"
                f"Steps:\n{steps_text}\n\n"
                f"NOTE: These write operations need to go through the approval flow. "
                f"Include them in your action plan if you agree."
            )

        return f"[Response from {intern.name}]\n{response}"

    @classmethod
    def create(
        cls,
        intern_registry: InternRegistry,
        crew_runner: Any,
        tool_registry: ToolRegistry,
        config: dict,
        notion: Any = None,
    ) -> DelegateToInternTool:
        """Factory method to create with proper references."""
        tool = cls()
        tool._intern_registry = intern_registry
        tool._crew_runner = crew_runner
        tool._tool_registry = tool_registry
        tool._config = config
        tool._notion = notion
        return tool
