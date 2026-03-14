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

    def _shutdown(sig, frame):
        signame = signal.Signals(sig).name
        logger.info("Received %s — shutting down gracefully...", signame)
        orchestrator.audit.close()
        orchestrator.intern_store.close()
        orchestrator.credential_store.close()
        orchestrator.reminder_scheduler.shutdown()
        handler.close()
        _cleanup_temp_files()
        logger.info("Shutdown complete.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Also clean up temp files on normal exit
    atexit.register(_cleanup_temp_files)

    logger.info("Starting Jibsa (Socket Mode)...")
    handler.start()


if __name__ == "__main__":
    main()
