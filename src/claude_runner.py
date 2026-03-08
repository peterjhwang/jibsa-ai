"""
ClaudeRunner — wraps the `claude -p` headless CLI subprocess.

Builds the system prompt from persona + config, sends the user prompt via
stdin, and returns either a parsed JSON action plan (dict) or plain text (str).
"""
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

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
    # Try bare JSON first
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try to extract from ```json ... ``` block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None


class ClaudeRunner:
    def __init__(self, config: dict):
        self.config = config
        self._persona = _load_yaml(_CONFIG_DIR / "persona.yaml")["persona"]
        self._system_template = _load_text(_CONFIG_DIR / "prompts" / "system.txt")
        self._timeout = config.get("jibsa", {}).get("claude_timeout", 120)

    def _build_system_prompt(
        self, history: list[dict], integrations: list[str], notion_context: str = ""
    ) -> str:
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        now = datetime.now()

        integration_lines = (
            "\n".join(f"- {i}" for i in integrations)
            if integrations
            else "No integrations connected yet (Phase 1). You can propose plans but cannot execute them."
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
    ) -> dict | str:
        """
        Send user_message to Claude and return the response.

        Returns:
            dict  — if Claude returns a valid JSON action plan
            str   — for conversational responses
        """
        system_prompt = self._build_system_prompt(
            history=history or [],
            integrations=active_integrations or [],
            notion_context=notion_context,
        )

        cmd = ["claude", "-p", "--system-prompt", system_prompt]

        logger.debug("Calling claude -p with %d chars of system prompt", len(system_prompt))

        try:
            result = subprocess.run(
                cmd,
                input=user_message,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error("claude -p timed out after %ds", self._timeout)
            return "⚠️ Claude timed out. Please try again."
        except FileNotFoundError:
            logger.error("claude CLI not found — is it installed?")
            return "⚠️ Claude CLI not found. Run `npm install -g @anthropic-ai/claude-code` and authenticate."

        if result.returncode != 0:
            logger.error("claude -p exited %d: %s", result.returncode, result.stderr)
            return f"⚠️ Claude returned an error. (exit {result.returncode})"

        output = result.stdout.strip()

        # Try to parse as action plan
        parsed = _extract_json(output)
        if parsed and parsed.get("type") == "action_plan":
            return parsed

        return output
