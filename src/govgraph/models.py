from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


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


class ContractorProfile(BaseModel):
    uei: str
    sam_entity: dict[str, Any] | None = None
    sam_exclusions: dict[str, Any] | None = None
    usaspending_awards: dict[str, Any] | None = None
    generated_at_utc: datetime
    provenance: dict[str, str] = Field(default_factory=dict)


class WebhookSubscriptionCreate(BaseModel):
    url: HttpUrl
    event_type: str
    filters: dict[str, Any] = Field(default_factory=dict)


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

