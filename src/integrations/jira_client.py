"""
JiraClient — thin wrapper around the atlassian-python-api Jira SDK.

Knows nothing about Jibsa's domain. Translates SDK exceptions
into a single local JiraAPIError so callers are insulated from SDK internals.

All public methods use tenacity retry with exponential backoff for transient
failures (rate limits, server errors). Non-retryable errors propagate immediately.
"""
import logging
from typing import Any

from atlassian import Jira
from requests.exceptions import HTTPError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient Jira API errors worth retrying."""
    if isinstance(exc, HTTPError):
        # 429 = rate limit, 500+ = server errors, 502/503/504 = gateway errors
        status = getattr(exc.response, "status_code", None)
        return status in (429, 500, 502, 503, 504)
    return False


_jira_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class JiraAPIError(Exception):
    """Wraps Jira HTTPError with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Jira {operation} failed: {cause}")
        self.cause = cause


class JiraClient:
    def __init__(self, server: str, email: str, api_token: str):
        self._client = Jira(url=server, username=email, password=api_token)
        self._server = server.rstrip("/")

    # -------------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------------

    @_jira_retry
    def search_issues(
        self,
        jql: str,
        fields: str = "*all",
        max_results: int = 20,
    ) -> list[dict]:
        """Search issues using JQL and return list of issue objects."""
        logger.debug("search_issues -> jql=%s max=%d", jql, max_results)
        try:
            # Use the new /rest/api/3/search/jql endpoint (old /search was removed)
            params = {"jql": jql, "maxResults": max_results, "fields": fields}
            response = self._client.get("rest/api/3/search/jql", params=params)
            issues = response.get("issues", [])
            logger.debug("search_issues <- %d issues", len(issues))
            return issues
        except HTTPError as e:
            logger.error("search_issues FAILED jql=%s: %s", jql, e)
            raise JiraAPIError("search_issues", e) from e

    @_jira_retry
    def get_issue(self, issue_key: str) -> dict:
        """Retrieve a single issue by key."""
        logger.debug("get_issue -> key=%s", issue_key)
        try:
            issue = self._client.issue(issue_key)
            logger.debug("get_issue <- key=%s", issue_key)
            return issue
        except HTTPError as e:
            logger.error("get_issue FAILED key=%s: %s", issue_key, e)
            raise JiraAPIError("get_issue", e) from e

    @_jira_retry
    def get_transitions(self, issue_key: str) -> list[dict]:
        """Get available transitions for an issue."""
        logger.debug("get_transitions -> key=%s", issue_key)
        try:
            transitions = self._client.get_issue_transitions(issue_key)
            logger.debug("get_transitions <- %d transitions", len(transitions))
            return transitions
        except HTTPError as e:
            logger.error("get_transitions FAILED key=%s: %s", issue_key, e)
            raise JiraAPIError("get_transitions", e) from e

    # -------------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------------

    @_jira_retry
    def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        description: str = "",
        **kwargs: Any,
    ) -> dict:
        """Create a new issue in a project."""
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = description
        fields.update(kwargs)
        logger.debug("create_issue -> project=%s summary=%s type=%s", project_key, summary, issue_type)
        try:
            issue = self._client.issue_create(fields=fields)
            logger.debug("create_issue <- key=%s", issue.get("key"))
            return issue
        except HTTPError as e:
            logger.error("create_issue FAILED project=%s: %s", project_key, e)
            raise JiraAPIError("create_issue", e) from e

    @_jira_retry
    def update_issue(self, issue_key: str, fields: dict) -> dict:
        """Update fields on an existing issue."""
        logger.debug("update_issue -> key=%s fields=%s", issue_key, list(fields.keys()))
        try:
            result = self._client.update_issue_field(issue_key, fields)
            logger.debug("update_issue <- ok")
            return result
        except HTTPError as e:
            logger.error("update_issue FAILED key=%s: %s", issue_key, e)
            raise JiraAPIError("update_issue", e) from e

    @_jira_retry
    def transition_issue(self, issue_key: str, transition_name: str) -> dict:
        """Transition an issue to a new status by transition name."""
        logger.debug("transition_issue -> key=%s transition=%s", issue_key, transition_name)
        try:
            transitions = self._client.get_issue_transitions(issue_key)
            matching = [t for t in transitions if t.get("name", "").lower() == transition_name.lower()]
            if not matching:
                available = [t.get("name") for t in transitions]
                raise ValueError(
                    f"Transition '{transition_name}' not found for {issue_key}. "
                    f"Available: {available}"
                )
            result = self._client.set_issue_status(issue_key, transition_name)
            logger.debug("transition_issue <- ok")
            return result
        except HTTPError as e:
            logger.error("transition_issue FAILED key=%s: %s", issue_key, e)
            raise JiraAPIError("transition_issue", e) from e

    @_jira_retry
    def add_comment(self, issue_key: str, body: str) -> dict:
        """Add a comment to an issue."""
        logger.debug("add_comment -> key=%s", issue_key)
        try:
            result = self._client.issue_add_comment(issue_key, body)
            logger.debug("add_comment <- ok")
            return result
        except HTTPError as e:
            logger.error("add_comment FAILED key=%s: %s", issue_key, e)
            raise JiraAPIError("add_comment", e) from e

    @_jira_retry
    def add_worklog(self, issue_key: str, time_spent: str, comment: str = "") -> dict:
        """Add a worklog entry to an issue."""
        logger.debug("add_worklog -> key=%s time=%s", issue_key, time_spent)
        try:
            result = self._client.issue_worklog(issue_key, time_spent, comment=comment or None)
            logger.debug("add_worklog <- ok")
            return result
        except HTTPError as e:
            logger.error("add_worklog FAILED key=%s: %s", issue_key, e)
            raise JiraAPIError("add_worklog", e) from e

    # -------------------------------------------------------------------
    # Action plan dispatcher
    # -------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """Dispatch a single action plan step. Always returns a dict -- never raises."""
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "create_issue":     self._handle_create_issue,
            "update_issue":     self._handle_update_issue,
            "transition_issue": self._handle_transition_issue,
            "add_comment":      self._handle_add_comment,
            "add_worklog":      self._handle_add_worklog,
        }

        handler = handlers.get(action)
        if not handler:
            return {"ok": False, "error": f"Unknown action: {action}", "description": description}

        try:
            result = handler(params)
            result["description"] = description
            return result
        except JiraAPIError as e:
            logger.error("Jira step %s failed: %s", action, e)
            return {"ok": False, "error": str(e), "description": description}
        except Exception as e:
            logger.error("Unexpected error in step %s: %s", action, e)
            return {"ok": False, "error": f"Unexpected error: {e}", "description": description}

    # -------------------------------------------------------------------
    # Step handlers
    # -------------------------------------------------------------------

    def _handle_create_issue(self, params: dict) -> dict:
        project_key = params.get("project_key") or params.get("project", "")
        if not project_key:
            return {"ok": False, "error": "Missing project_key in params"}
        summary = params.get("summary", "")
        if not summary:
            return {"ok": False, "error": "Missing summary in params"}
        issue_type = params.get("issue_type", params.get("type", "Task"))
        description = params.get("description", "")
        extra = {k: v for k, v in params.items() if k not in ("project_key", "project", "summary", "issue_type", "type", "description")}
        issue = self.create_issue(project_key, summary, issue_type, description, **extra)
        key = issue.get("key", "")
        url = f"{self._server}/browse/{key}" if key else ""
        return {"ok": True, "key": key, "url": url}

    def _handle_update_issue(self, params: dict) -> dict:
        issue_key = params["issue_key"]
        fields = params.get("fields", {})
        self.update_issue(issue_key, fields)
        url = f"{self._server}/browse/{issue_key}"
        return {"ok": True, "key": issue_key, "url": url}

    def _handle_transition_issue(self, params: dict) -> dict:
        issue_key = params["issue_key"]
        transition_name = params["transition_name"]
        self.transition_issue(issue_key, transition_name)
        url = f"{self._server}/browse/{issue_key}"
        return {"ok": True, "key": issue_key, "url": url}

    def _handle_add_comment(self, params: dict) -> dict:
        issue_key = params["issue_key"]
        body = params["body"]
        self.add_comment(issue_key, body)
        url = f"{self._server}/browse/{issue_key}"
        return {"ok": True, "key": issue_key, "url": url}

    def _handle_add_worklog(self, params: dict) -> dict:
        issue_key = params["issue_key"]
        time_spent = params["time_spent"]
        comment = params.get("comment", "")
        self.add_worklog(issue_key, time_spent, comment)
        url = f"{self._server}/browse/{issue_key}"
        return {"ok": True, "key": issue_key, "url": url}
