"""
app.py — Entry point.

Starts the Slack Bolt app in Socket Mode alongside APScheduler.
All messages in the configured Jibsa channel are routed to the Orchestrator.

Usage:
    python -m src.app
    # or via Docker
"""
import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .orchestrator import Orchestrator

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_config() -> dict:
    with open(_CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


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
        orchestrator.handle_message(
            channel=event.get("channel", ""),
            thread_ts=event.get("thread_ts") or event["ts"],
            user=event.get("user", ""),
            text=text,
        )

    @slack_app.event("message")
    def handle_message(event):
        _route(event)

    @slack_app.event("app_mention")
    def handle_mention(event):
        _route(event)

    # Verify the bot is responding to the right channel
    # (Socket Mode delivers all events; filter handled by Slack app subscription)
    logger.info("Jibsa will listen in #%s", target_channel)

    return slack_app, orchestrator


def main():
    config = load_config()
    slack_app, _ = create_app(config)

    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise EnvironmentError("SLACK_APP_TOKEN is not set. Check your .env file.")

    logger.info("Starting Jibsa (Socket Mode)...")
    handler = SocketModeHandler(slack_app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
