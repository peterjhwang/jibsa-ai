"""
Config validation — pydantic models for settings.yaml.

Validates all configuration on startup so that typos and missing fields
are caught early instead of causing silent runtime failures.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class _Permissive(BaseModel):
    """Base that ignores unknown fields (forward-compat with future config keys)."""
    model_config = {"extra": "ignore"}


class LLMConfig(_Permissive):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=128000)


class JibsaConfig(_Permissive):
    channel_name: str = "jibsa"
    timezone: str = "UTC"
    max_history: int = Field(default=20, ge=1)
    claude_timeout: int = Field(default=120, ge=10)
    claude_max_concurrent: int = Field(default=3, ge=1)
    crew_timeout: int = Field(default=300, ge=30, description="Max seconds for a CrewAI crew execution")
    crew_max_iter: int = Field(default=10, ge=1, le=50)
    code_exec_timeout: int = Field(default=30, ge=5, le=120)
    code_exec_max_output: int = Field(default=4000, ge=100)
    credential_db_path: str = "data/credentials.db"
    intern_db_path: str = "data/jibsa.db"


class SchedulerJobConfig(_Permissive):
    enabled: bool = False
    cron: str = ""


class SchedulerConfig(_Permissive):
    morning_briefing: SchedulerJobConfig = SchedulerJobConfig()
    eod_review: SchedulerJobConfig = SchedulerJobConfig()
    overdue_alerts: SchedulerJobConfig = SchedulerJobConfig()
    weekly_digest: SchedulerJobConfig = SchedulerJobConfig()


class ApprovalConfig(_Permissive):
    approve_keywords: list[str] = [
        "✅", "yes", "approved", "go", "go ahead", "do it", "proceed",
    ]
    reject_keywords: list[str] = [
        "❌", "no", "cancel", "stop", "revise", "change",
    ]
    ttl_seconds: int = Field(default=3600, ge=60, description="Pending plan expiry in seconds")

    @field_validator("approve_keywords", "reject_keywords")
    @classmethod
    def keywords_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("keyword list must not be empty")
        return v


class IntegrationToggle(_Permissive):
    enabled: bool = False
    parent_page_id: str = ""


class IntegrationsConfig(_Permissive):
    notion: IntegrationToggle = IntegrationToggle()
    jira: IntegrationToggle = IntegrationToggle()
    confluence: IntegrationToggle = IntegrationToggle()
    google_calendar: IntegrationToggle = IntegrationToggle()
    gmail: IntegrationToggle = IntegrationToggle()


class Settings(_Permissive):
    """Top-level validated settings — mirrors config/settings.yaml."""
    llm: LLMConfig = LLMConfig()
    jibsa: JibsaConfig = JibsaConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    approval: ApprovalConfig = ApprovalConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()


def validate_config(raw: dict) -> Settings:
    """Validate a raw config dict and return a Settings object.

    Raises pydantic.ValidationError with helpful messages on bad input.
    """
    return Settings.model_validate(raw)
