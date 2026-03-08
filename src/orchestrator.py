"""
Orchestrator — the central router.

Receives messages from Slack, manages conversation history per thread,
calls ClaudeRunner, handles the propose-approve flow, and dispatches
approved plans to integration clients (Phase 2+).
"""
import logging
from typing import Any

from .approval import ApprovalManager, ApprovalState
from .claude_runner import ClaudeRunner
from .integrations.notion_second_brain import build_second_brain

logger = logging.getLogger(__name__)


def _active_integrations(config: dict) -> list[str]:
    """Return list of enabled integration names from config."""
    return [
        name
        for name, cfg in config.get("integrations", {}).items()
        if cfg.get("enabled", False)
    ]


class Orchestrator:
    def __init__(self, slack_client: Any, config: dict):
        self.slack = slack_client
        self.config = config
        self.runner = ClaudeRunner(config)
        self.approval = ApprovalManager(config)
        self.notion = build_second_brain(config)
        self._history: dict[str, list[dict]] = {}  # thread_ts → message list
        self._max_history: int = config.get("jibsa", {}).get("max_history", 20)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def handle_message(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
    ) -> None:
        """Process an incoming Slack message."""
        ctx = self.approval.get(thread_ts)

        if ctx.state == ApprovalState.PENDING:
            self._handle_approval_response(ctx, channel, thread_ts, text)
            return

        self._handle_new_request(channel, thread_ts, user, text)

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _handle_approval_response(
        self,
        ctx,
        channel: str,
        thread_ts: str,
        text: str,
    ) -> None:
        if self.approval.is_approval(text):
            logger.info("Thread %s — plan approved", thread_ts)
            self._execute_plan(ctx.pending_plan, channel, thread_ts)
            self.approval.clear(thread_ts)

        elif self.approval.is_rejection(text):
            logger.info("Thread %s — plan rejected", thread_ts)
            self._post(channel, thread_ts, "Understood. What would you like to change?")
            self.approval.clear(thread_ts)

        else:
            # Ambiguous — re-prompt
            self._post(
                channel,
                thread_ts,
                "I still have a pending plan waiting for your decision. "
                "Reply ✅ to proceed or ❌ to cancel.",
            )

    def _handle_new_request(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
    ) -> None:
        history = self._get_history(thread_ts)
        active = _active_integrations(self.config)

        notion_context = ""
        if self.notion:
            try:
                notion_context = self.notion.get_context_for_request(text)
            except Exception:
                logger.warning("Notion context enrichment failed", exc_info=True)

        response = self.runner.run(
            user_message=text,
            history=history,
            active_integrations=active,
            notion_context=notion_context,
        )

        # Save user message to history
        self._add_to_history(thread_ts, "user", text)

        if isinstance(response, dict) and response.get("type") == "action_plan":
            plan_text = self._format_plan(response)
            self._post(channel, thread_ts, plan_text)
            self.approval.set_pending(thread_ts, response, channel)
            # Save Jibsa's plan proposal to history
            self._add_to_history(thread_ts, "assistant", plan_text)
        else:
            self._post(channel, thread_ts, str(response))
            self._add_to_history(thread_ts, "assistant", str(response))

    def _execute_plan(self, plan: dict, channel: str, thread_ts: str) -> None:
        steps = plan.get("steps", [])
        logger.info("Executing plan '%s' (%d steps)", plan.get("summary", ""), len(steps))

        results = []
        for step in steps:
            service = step.get("service", "")
            if service == "notion" and self.notion:
                result = self.notion.execute_step(step)
            else:
                result = {"ok": False, "error": f"'{service}' not connected yet"}
            results.append((step, result))
            logger.info("Step %s/%s → ok=%s", step.get("action"), service, result.get("ok"))

        lines = [f"✅ *{plan.get('summary', 'Plan')} — complete*\n"]
        for step, result in results:
            desc = step.get("description") or step.get("action", "step")
            if result.get("ok"):
                url = result.get("url", "")
                suffix = f" → <{url}|view>" if url else ""
                lines.append(f"  ✅ {desc}{suffix}")
            else:
                lines.append(f"  ⚠️ {desc} — {result.get('error', 'unknown error')}")

        self._post(channel, thread_ts, "\n".join(lines))

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_plan(self, plan: dict) -> str:
        lines = [f"📋 *Plan: {plan.get('summary', 'Proposed Action')}*\n"]
        for i, step in enumerate(plan.get("steps", []), 1):
            desc = step.get("description") or f"{step.get('action')} on {step.get('service')}"
            lines.append(f"  {i}. {desc}")
        lines.append("\nApprove? ✅ / ❌")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _get_history(self, thread_ts: str) -> list[dict]:
        return list(self._history.get(thread_ts, []))

    def _add_to_history(self, thread_ts: str, role: str, content: str) -> None:
        if thread_ts not in self._history:
            self._history[thread_ts] = []
        self._history[thread_ts].append({"role": role, "content": content})
        # Trim to max
        if len(self._history[thread_ts]) > self._max_history:
            self._history[thread_ts] = self._history[thread_ts][-self._max_history:]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _post(self, channel: str, thread_ts: str, text: str) -> None:
        try:
            self.slack.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception as e:
            logger.error("Failed to post to Slack: %s", e)
