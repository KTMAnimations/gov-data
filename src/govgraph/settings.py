from __future__ import annotations

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOVGRAPH_", env_file=".env", extra="ignore")

    # GovGraph auth (optional)
    api_key: str | None = Field(default=None, description="If set, requires X-Api-Key on all endpoints.")

    # Storage
    db_path: str = Field(default="govgraph.db")

    # Shared gateway key used by multiple federal APIs (optional; depends on source)
    api_data_gov_key: str | None = Field(default=None)

    # Upstream endpoints (override as upstream APIs evolve)
    sam_opportunities_base_url: AnyHttpUrl = Field(
        default="https://api.sam.gov/prod/opportunities/v2/search"
    )
    sam_entity_base_url: AnyHttpUrl = Field(default="https://api.sam.gov/entity-information/v3/entities")
    sam_exclusions_base_url: AnyHttpUrl = Field(
        default="https://api.sam.gov/entity-information/v3/exclusions"
    )
    usaspending_base_url: AnyHttpUrl = Field(default="https://api.usaspending.gov/api/v2")

    # Background polling for webhook alerts
    enable_poller: bool = Field(default=False)
    poll_interval_seconds: int = Field(default=300, ge=10)
    webhook_timeout_seconds: int = Field(default=10, ge=1)

