"""
LLMRunner — LangChain-based LLM runner replacing the `claude -p` subprocess.

Supports multiple LLM backends (Anthropic Claude, OpenAI, Google Gemini)
via LangChain's ChatModel interface. Drop-in replacement for ClaudeRunner
with the same run() signature.
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _load_text(path: Path) -> str:
    with open(path) as f:
        return f.read()


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from text, stripping markdown code fences if present."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _create_chat_model(llm_config: dict) -> Any:
    """Factory to create a LangChain ChatModel based on provider config."""
    provider = llm_config.get("provider", "anthropic")
    model = llm_config.get("model", "claude-sonnet-4-20250514")
    temperature = llm_config.get("temperature", 0.7)
    max_tokens = llm_config.get("max_tokens", 4096)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}. Use 'anthropic', 'openai', or 'google'.")


class LLMRunner:
    """LangChain-based LLM runner. Same interface as ClaudeRunner."""

    def __init__(self, config: dict, system_prompt_template: str | None = None):
        """
        Args:
            config: Full app config dict.
            system_prompt_template: Override the default system.txt template.
                Used by interns to inject their own persona-specific prompt.
        """
        self.config = config
        self._persona = _load_yaml(_CONFIG_DIR / "persona.yaml")["persona"]
        self._system_template = system_prompt_template or _load_text(
            _CONFIG_DIR / "prompts" / "system.txt"
        )
        self._timeout = config.get("jibsa", {}).get("claude_timeout", 120)

        llm_config = config.get("llm", {})
        if not llm_config:
            # Sensible defaults for backward compatibility
            llm_config = {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
        self._llm = _create_chat_model(llm_config)

    def _build_system_prompt(
        self,
        history: list[dict],
        integrations: list[str],
        notion_context: str = "",
        extra_replacements: dict[str, str] | None = None,
    ) -> str:
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        now = datetime.now()

        integration_lines = (
            "\n".join(f"- {i}" for i in integrations)
            if integrations
            else "No integrations connected yet. You can propose plans but cannot execute them."
        )

        history_text = ""
        if history:
            lines = []
            for msg in history:
                role = "User" if msg["role"] == "user" else "Jibsa"
                lines.append(f"{role}: {msg['content']}")
            history_text = "\n".join(lines)

        replacements = {
            "{name}": self._persona["name"],
            "{owner}": "the user",
            "{tone}": self._persona["tone"].strip(),
            "{boundaries}": self._persona["boundaries"].strip(),
            "{integrations}": integration_lines,
            "{notion_context}": (
                f"## Notion Context\nThe following is live data from the user's Notion Second Brain. "
                f"Use it to give specific, grounded answers.\n\n{notion_context}"
                if notion_context else ""
            ),
            "{date}": now.strftime("%A, %B %d, %Y"),
            "{time}": now.strftime("%H:%M"),
            "{timezone}": tz,
            "{history}": history_text or "(no prior messages in this thread)",
        }

        if extra_replacements:
            replacements.update(extra_replacements)

        result = self._system_template
        for key, value in replacements.items():
            result = result.replace(key, value)
        return result

    def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        active_integrations: list[str] | None = None,
        notion_context: str = "",
        extra_replacements: dict[str, str] | None = None,
    ) -> dict | str:
        """
        Send user_message to the LLM and return the response.

        Returns:
            dict  — if the LLM returns a valid JSON action plan
            str   — for conversational responses
        """
        system_prompt = self._build_system_prompt(
            history=history or [],
            integrations=active_integrations or [],
            notion_context=notion_context,
            extra_replacements=extra_replacements,
        )

        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]

        logger.debug("Calling LLM with %d chars of system prompt", len(system_prompt))

        try:
            response = self._llm.invoke(messages)
            output = response.content.strip()
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return f"⚠️ LLM call failed: {e}"

        # Try to parse as action plan
        parsed = _extract_json(output)
        if parsed and parsed.get("type") == "action_plan":
            return parsed

        return output
