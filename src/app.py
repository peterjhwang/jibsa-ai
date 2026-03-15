"""
app.py — Entry point.

Starts the Slack Bolt app in Socket Mode alongside APScheduler.
All messages in the configured Jibsa channel are routed to the Orchestrator.

Usage:
    python -m src.app
    # or via Docker
"""
import atexit
import logging
import os
import re
import signal
import sys
import tempfile
from pathlib import Path

import yaml
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config_schema import validate_config
from .orchestrator import Orchestrator

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_config() -> dict:
    with open(_CONFIG_DIR / "settings.yaml") as f:
        raw = yaml.safe_load(f)
    # Validate config on load — raises pydantic.ValidationError on bad input
    validate_config(raw)
    return raw


def create_app(config: dict) -> tuple[App, Orchestrator]:
    slack_app = App(token=os.environ["SLACK_BOT_TOKEN"])
    orchestrator = Orchestrator(slack_app.client, config)

    target_channel = config.get("jibsa", {}).get("channel_name", "jibsa")

    def _route(event):
        if event.get("bot_id") or event.get("subtype"):
            return
        text = event.get("text", "").strip()
        if not text:
            return
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event["ts"]
        try:
            orchestrator.handle_message(
                channel=channel,
                thread_ts=thread_ts,
                user=event.get("user", ""),
                text=text,
            )
        except Exception:
            logger.error("Unhandled error processing message", exc_info=True)
            try:
                slack_app.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="⚠️ Something went wrong processing your message. Please try again.",
                )
            except Exception:
                logger.error("Failed to post error message to Slack", exc_info=True)

    @slack_app.event("message")
    def handle_message(event):
        _route(event)

    @slack_app.event("app_mention")
    def handle_mention(event):
        _route(event)

    # Block Kit button handlers for approve/reject
    @slack_app.action("approve_plan")
    def handle_approve(ack, body, respond):
        ack()
        channel = body["channel"]["id"]
        thread_ts = body["message"].get("thread_ts") or body["message"]["ts"]
        user = body["user"]["id"]
        orchestrator.handle_button_action("approve_plan", channel, thread_ts, user, respond)

    @slack_app.action("reject_plan")
    def handle_reject(ack, body, respond):
        ack()
        channel = body["channel"]["id"]
        thread_ts = body["message"].get("thread_ts") or body["message"]["ts"]
        user = body["user"]["id"]
        orchestrator.handle_button_action("reject_plan", channel, thread_ts, user, respond)

    # Block Kit "View JD" buttons (dynamic action_id per intern: view_jd_alex, view_jd_sarah, etc.)
    @slack_app.action(re.compile(r"^view_jd_.+"))
    def handle_view_jd(ack, body):
        ack()
        action_id = body["actions"][0]["action_id"]
        intern_name = action_id.replace("view_jd_", "")
        channel = body["channel"]["id"]
        thread_ts = body["message"].get("thread_ts") or body["message"]["ts"]
        try:
            intern = orchestrator.intern_registry.get_intern(intern_name)
            if intern:
                orchestrator._show_jd_blocks(channel, thread_ts, intern)
            else:
                slack_app.client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=f"Intern '{intern_name}' not found.",
                )
        except Exception:
            logger.error("Failed to handle view_jd for %s", intern_name, exc_info=True)

    # Verify the bot is responding to the right channel
    # (Socket Mode delivers all events; filter handled by Slack app subscription)
    logger.info("Jibsa will listen in #%s", target_channel)

    return slack_app, orchestrator


def _cleanup_temp_files() -> None:
    """Remove leftover jibsa temp dirs on exit."""
    tmp = Path(tempfile.gettempdir())
    for d in tmp.glob("jibsa_*"):
        try:
            if d.is_dir():
                import shutil
                shutil.rmtree(d, ignore_errors=True)
            elif d.is_file():
                d.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    config = load_config()
    slack_app, orchestrator = create_app(config)

    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise EnvironmentError("SLACK_APP_TOKEN is not set. Check your .env file.")

    handler = SocketModeHandler(slack_app, app_token)

    _shutting_down = False

    def _shutdown(sig, frame):
        nonlocal _shutting_down
        if _shutting_down:
            # Second signal — force exit immediately
            logger.warning("Forced exit.")
            os._exit(1)
        _shutting_down = True

        signame = signal.Signals(sig).name
        logger.info("Received %s — shutting down gracefully...", signame)

        # Close stores (fast, non-blocking)
        for store in ("audit", "intern_store", "sop_store", "credential_store"):
            try:
                getattr(orchestrator, store).close()
            except Exception:
                pass

        orchestrator.reminder_scheduler.shutdown()

        # SocketModeHandler.close() can hang — disconnect and force exit
        try:
            if handler.client and handler.client.is_connected():
                handler.client.disconnect()
        except Exception:
            pass

        _cleanup_temp_files()
        logger.info("Shutdown complete.")
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Also clean up temp files on normal exit
    atexit.register(_cleanup_temp_files)

    logger.info("Starting Jibsa (Socket Mode)...")
    handler.start()


if __name__ == "__main__":
    main()
