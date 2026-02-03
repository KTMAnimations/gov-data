from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

from govgraph.security import is_safe_webhook_url


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    time_utc: datetime
    version: str


class SourceStatus(BaseModel):
    name: str
    base_url: str
    configured: bool


class SourcesResponse(BaseModel):
    sources: list[SourceStatus]


class PublicConfig(BaseModel):
    version: str
    requires_api_key: bool
    enable_poller: bool
    sources: list[SourceStatus]


class ContractorProfile(BaseModel):
    uei: str
    sam_entity: dict[str, Any] | None = None
    sam_exclusions: dict[str, Any] | None = None
    usaspending_awards: dict[str, Any] | None = None
    generated_at_utc: datetime
    provenance: dict[str, str] = Field(default_factory=dict)


class OpportunityItem(BaseModel):
    external_id: str
    title: str | None = None
    posted_at: datetime | None = None
    raw: dict[str, Any]


class OpportunitiesResponse(BaseModel):
    query: dict[str, Any]
    items: list[OpportunityItem]
    raw: dict[str, Any]


# Allowed event types for webhooks
ALLOWED_EVENT_TYPES = frozenset({
    "sam.opportunity.created",
    "sam.opportunity.updated",
    "sam.opportunity.deleted",
})


class WebhookSubscriptionCreate(BaseModel):
    url: HttpUrl
    event_type: str = Field(..., min_length=1, max_length=100)
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_webhook_url(cls, v: HttpUrl) -> HttpUrl:
        """Validate that the webhook URL is safe (not targeting internal resources)."""
        url_str = str(v)
        is_safe, error_msg = is_safe_webhook_url(url_str)
        if not is_safe:
            raise ValueError(f"Invalid webhook URL: {error_msg}")
        return v

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        """Validate that the event type is allowed."""
        if v not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"Invalid event_type. Allowed values: {', '.join(sorted(ALLOWED_EVENT_TYPES))}")
        return v


class WebhookSubscription(BaseModel):
    id: str
    url: HttpUrl
    event_type: str
    filters: dict[str, Any]
    secret: str
    active: bool
    created_at_utc: datetime


class WebhookDelivery(BaseModel):
    event_type: str
    delivery_id: str
    sent_at_utc: datetime
    target_url: HttpUrl
