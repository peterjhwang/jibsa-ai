"""
GoogleCalendarClient — wrapper around the Google Calendar API v3.

Uses per-user OAuth credentials. Instantiated per-request with
the requesting user's credentials.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class GoogleCalendarAPIError(Exception):
    """Wraps Google Calendar API errors with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Google Calendar {operation} failed: {cause}")
        self.cause = cause


class GoogleCalendarClient:
    def __init__(self, credentials):
        self._service = build(
            "calendar", "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    # -------------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------------

    def list_today_events(self, timezone: str = "UTC") -> list[dict]:
        """List all events for today in the given timezone."""
        logger.debug("list_today_events → timezone=%s", timezone)
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        try:
            response = (
                self._service.events()
                .list(
                    calendarId="primary",
                    timeMin=start_of_day.isoformat(),
                    timeMax=end_of_day.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                )
                .execute()
            )
            items = response.get("items", [])
            logger.debug("list_today_events ← %d events", len(items))
            return items
        except HttpError as e:
            logger.error("list_today_events FAILED: %s", e)
            raise GoogleCalendarAPIError("list_today_events", e) from e

    def list_upcoming_events(self, days: int = 7, timezone: str = "UTC") -> list[dict]:
        """List upcoming events for the next N days."""
        logger.debug("list_upcoming_events → days=%d timezone=%s", days, timezone)
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        time_max = now + timedelta(days=days)
        try:
            response = (
                self._service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                )
                .execute()
            )
            items = response.get("items", [])
            logger.debug("list_upcoming_events ← %d events", len(items))
            return items
        except HttpError as e:
            logger.error("list_upcoming_events FAILED: %s", e)
            raise GoogleCalendarAPIError("list_upcoming_events", e) from e

    def search_events(self, query: str, days: int = 30, timezone: str = "UTC") -> list[dict]:
        """Search events by text query within the next N days."""
        logger.debug("search_events → query=%s days=%d timezone=%s", query, days, timezone)
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        time_max = now + timedelta(days=days)
        try:
            response = (
                self._service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=time_max.isoformat(),
                    q=query,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                )
                .execute()
            )
            items = response.get("items", [])
            logger.debug("search_events ← %d events", len(items))
            return items
        except HttpError as e:
            logger.error("search_events FAILED query=%s: %s", query, e)
            raise GoogleCalendarAPIError("search_events", e) from e

    def get_event(self, event_id: str, calendar_id: str = "primary") -> dict:
        """Retrieve a single event by ID."""
        logger.debug("get_event → event_id=%s calendar=%s", event_id, calendar_id)
        try:
            event = (
                self._service.events()
                .get(calendarId=calendar_id, eventId=event_id)
                .execute()
            )
            logger.debug("get_event ← summary=%s", event.get("summary"))
            return event
        except HttpError as e:
            logger.error("get_event FAILED event_id=%s: %s", event_id, e)
            raise GoogleCalendarAPIError("get_event", e) from e

    # -------------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------------

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        attendees: list[str] | None = None,
        calendar_id: str = "primary",
    ) -> dict:
        """Create a new calendar event."""
        logger.debug("create_event → summary=%s start=%s end=%s", summary, start, end)
        body: dict = {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if description:
            body["description"] = description
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]
        try:
            event = (
                self._service.events()
                .insert(calendarId=calendar_id, body=body)
                .execute()
            )
            logger.debug("create_event ← id=%s", event.get("id"))
            return event
        except HttpError as e:
            logger.error("create_event FAILED summary=%s: %s", summary, e)
            raise GoogleCalendarAPIError("create_event", e) from e

    def update_event(self, event_id: str, updates: dict, calendar_id: str = "primary") -> dict:
        """Patch an existing calendar event."""
        logger.debug("update_event → event_id=%s fields=%s", event_id, list(updates.keys()))
        try:
            event = (
                self._service.events()
                .patch(calendarId=calendar_id, eventId=event_id, body=updates)
                .execute()
            )
            logger.debug("update_event ← ok")
            return event
        except HttpError as e:
            logger.error("update_event FAILED event_id=%s: %s", event_id, e)
            raise GoogleCalendarAPIError("update_event", e) from e

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None:
        """Delete a calendar event."""
        logger.debug("delete_event → event_id=%s", event_id)
        try:
            self._service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            logger.debug("delete_event ← ok")
        except HttpError as e:
            logger.error("delete_event FAILED event_id=%s: %s", event_id, e)
            raise GoogleCalendarAPIError("delete_event", e) from e

    # -------------------------------------------------------------------
    # Action plan dispatch
    # -------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """Dispatch a single action plan step. Always returns a dict -- never raises."""
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "create_event": self._handle_create_event,
            "update_event": self._handle_update_event,
            "delete_event": self._handle_delete_event,
        }

        handler = handlers.get(action)
        if not handler:
            return {"ok": False, "error": f"Unknown action: {action}", "description": description}

        try:
            result = handler(params)
            result["description"] = description
            return result
        except GoogleCalendarAPIError as e:
            logger.error("Google Calendar step %s failed: %s", action, e)
            return {"ok": False, "error": str(e), "description": description}
        except Exception as e:
            logger.error("Unexpected error in step %s: %s", action, e)
            return {"ok": False, "error": f"Unexpected error: {e}", "description": description}

    # -------------------------------------------------------------------
    # Step handlers
    # -------------------------------------------------------------------

    def _handle_create_event(self, params: dict) -> dict:
        event = self.create_event(
            summary=params["summary"],
            start=params["start"],
            end=params["end"],
            description=params.get("description", ""),
            attendees=params.get("attendees"),
            calendar_id=params.get("calendar_id", "primary"),
        )
        return {
            "ok": True,
            "event_id": event.get("id", ""),
            "url": event.get("htmlLink", ""),
        }

    def _handle_update_event(self, params: dict) -> dict:
        event_id = params["event_id"]
        updates = params.get("updates", {})
        calendar_id = params.get("calendar_id", "primary")
        event = self.update_event(event_id, updates, calendar_id)
        return {
            "ok": True,
            "event_id": event_id,
            "url": event.get("htmlLink", ""),
        }

    def _handle_delete_event(self, params: dict) -> dict:
        event_id = params["event_id"]
        calendar_id = params.get("calendar_id", "primary")
        self.delete_event(event_id, calendar_id)
        return {"ok": True, "event_id": event_id}
