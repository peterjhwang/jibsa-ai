"""
GmailClient — wrapper around the Gmail API v1.

Uses per-user OAuth credentials. Instantiated per-request with
the requesting user's credentials.
"""
import base64
import logging
import re
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def _extract_header(headers: list, name: str) -> str:
    """Find a header by name from Gmail headers list [{"name": ..., "value": ...}]."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


class GmailAPIError(Exception):
    """Wraps Gmail HttpError with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Gmail {operation} failed: {cause}")
        self.cause = cause


class GmailClient:
    def __init__(self, credentials):
        self._service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )

    # -------------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------------

    def list_messages(self, max_results: int = 10, query: str = "") -> list[dict]:
        """List recent messages, returning simplified metadata dicts."""
        logger.debug("list_messages -> max_results=%d query=%s", max_results, query)
        try:
            response = (
                self._service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            message_ids = response.get("messages", [])
            results = []
            for msg_ref in message_ids:
                msg_id = msg_ref["id"]
                msg = (
                    self._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=msg_id,
                        format="metadata",
                        metadataHeaders=["Subject", "From", "Date"],
                    )
                    .execute()
                )
                headers = msg.get("payload", {}).get("headers", [])
                results.append(
                    {
                        "id": msg_id,
                        "subject": _extract_header(headers, "Subject"),
                        "from": _extract_header(headers, "From"),
                        "date": _extract_header(headers, "Date"),
                        "snippet": msg.get("snippet", ""),
                    }
                )
            logger.debug("list_messages <- %d messages", len(results))
            return results
        except HttpError as e:
            logger.error("list_messages FAILED: %s", e)
            raise GmailAPIError("list_messages", e) from e

    def search_messages(self, query: str, max_results: int = 10) -> list[dict]:
        """Search messages using Gmail query syntax."""
        logger.debug("search_messages -> query=%s max_results=%d", query, max_results)
        return self.list_messages(max_results=max_results, query=query)

    def get_message(self, message_id: str) -> dict:
        """Retrieve a single message with full body text."""
        logger.debug("get_message -> id=%s", message_id)
        try:
            msg = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])

            body_text = self._extract_body(payload)

            result = {
                "id": message_id,
                "subject": _extract_header(headers, "Subject"),
                "from": _extract_header(headers, "From"),
                "to": _extract_header(headers, "To"),
                "date": _extract_header(headers, "Date"),
                "body": body_text[:4000],
            }
            logger.debug("get_message <- id=%s subject=%s", message_id, result["subject"])
            return result
        except HttpError as e:
            logger.error("get_message FAILED id=%s: %s", message_id, e)
            raise GmailAPIError("get_message", e) from e

    # -------------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------------

    def send_message(
        self, to: str, subject: str, body: str, cc: str = "", bcc: str = ""
    ) -> dict:
        """Send an email message."""
        logger.debug("send_message -> to=%s subject=%s", to, subject)
        try:
            msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            if cc:
                msg["cc"] = cc
            if bcc:
                msg["bcc"] = bcc
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            result = (
                self._service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            logger.debug("send_message <- id=%s", result.get("id"))
            return result
        except HttpError as e:
            logger.error("send_message FAILED to=%s: %s", to, e)
            raise GmailAPIError("send_message", e) from e

    def reply_to_message(self, message_id: str, body: str) -> dict:
        """Reply to an existing message, preserving thread."""
        logger.debug("reply_to_message -> message_id=%s", message_id)
        try:
            original = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="metadata",
                     metadataHeaders=["Subject", "From", "Message-ID"])
                .execute()
            )
            orig_headers = original.get("payload", {}).get("headers", [])
            orig_subject = _extract_header(orig_headers, "Subject")
            orig_from = _extract_header(orig_headers, "From")
            orig_message_id_header = _extract_header(orig_headers, "Message-ID")
            orig_thread_id = original.get("threadId", "")

            reply_subject = orig_subject
            if not re.match(r"(?i)^re:", reply_subject):
                reply_subject = f"Re: {reply_subject}"

            msg = MIMEText(body)
            msg["to"] = orig_from
            msg["subject"] = reply_subject
            if orig_message_id_header:
                msg["In-Reply-To"] = orig_message_id_header
                msg["References"] = orig_message_id_header

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            result = (
                self._service.users()
                .messages()
                .send(
                    userId="me",
                    body={"raw": raw, "threadId": orig_thread_id},
                )
                .execute()
            )
            logger.debug("reply_to_message <- id=%s", result.get("id"))
            return result
        except HttpError as e:
            logger.error("reply_to_message FAILED message_id=%s: %s", message_id, e)
            raise GmailAPIError("reply_to_message", e) from e

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        """Create a draft email."""
        logger.debug("create_draft -> to=%s subject=%s", to, subject)
        try:
            msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            result = (
                self._service.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw}})
                .execute()
            )
            logger.debug("create_draft <- id=%s", result.get("id"))
            return result
        except HttpError as e:
            logger.error("create_draft FAILED to=%s: %s", to, e)
            raise GmailAPIError("create_draft", e) from e

    # -------------------------------------------------------------------
    # Action plan dispatcher
    # -------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """Dispatch a single action plan step. Always returns a dict -- never raises."""
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "send_email":   self._handle_send_email,
            "reply_email":  self._handle_reply_email,
            "create_draft": self._handle_create_draft,
        }

        handler = handlers.get(action)
        if not handler:
            return {"ok": False, "error": f"Unknown action: {action}", "description": description}

        try:
            result = handler(params)
            result["description"] = description
            return result
        except GmailAPIError as e:
            logger.error("Gmail step %s failed: %s", action, e)
            return {"ok": False, "error": str(e), "description": description}
        except Exception as e:
            logger.error("Unexpected error in step %s: %s", action, e)
            return {"ok": False, "error": f"Unexpected error: {e}", "description": description}

    # -------------------------------------------------------------------
    # Step handlers
    # -------------------------------------------------------------------

    def _handle_send_email(self, params: dict) -> dict:
        to = params["to"]
        subject = params["subject"]
        body = params["body"]
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        result = self.send_message(to, subject, body, cc=cc, bcc=bcc)
        return {"ok": True, "message_id": result.get("id", "")}

    def _handle_reply_email(self, params: dict) -> dict:
        message_id = params["message_id"]
        body = params["body"]
        self.reply_to_message(message_id, body)
        return {"ok": True}

    def _handle_create_draft(self, params: dict) -> dict:
        to = params["to"]
        subject = params["subject"]
        body = params["body"]
        result = self.create_draft(to, subject, body)
        return {"ok": True, "draft_id": result.get("id", "")}

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Extract plain-text body from a Gmail message payload."""
        parts = payload.get("parts", [])
        if parts:
            # Look for text/plain first
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            # Fall back to text/html with tag stripping
            for part in parts:
                if part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                        return re.sub(r"<[^>]+>", "", html)

        # No parts — check payload.body directly
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        return ""
