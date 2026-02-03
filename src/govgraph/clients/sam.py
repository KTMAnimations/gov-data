from __future__ import annotations

from datetime import date
from typing import Any

from govgraph.clients.http import CachedHttpClient
from govgraph.settings import Settings


class SamClient:
    def __init__(self, *, http: CachedHttpClient, settings: Settings):
        self._http = http
        self._settings = settings

    async def search_opportunities(
        self,
        *,
        q: str | None,
        posted_from: date | None = None,
        posted_to: date | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        # SAM Opportunities API is query-param based; the exact filter names can evolve.
        # We keep the interface stable and allow base URL override via settings.
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if self._settings.api_data_gov_key:
            params["api_key"] = self._settings.api_data_gov_key
        if q:
            params["q"] = q
        if posted_from:
            params["postedFrom"] = posted_from.isoformat()
        if posted_to:
            params["postedTo"] = posted_to.isoformat()
        return await self._http.get_json(str(self._settings.sam_opportunities_base_url), params=params, ttl_seconds=30)

    async def get_entity_by_uei(self, uei: str) -> dict[str, Any]:
        # Entity Management is less uniform across environments; treat as best-effort.
        params: dict[str, Any] = {"ueiSAM": uei}
        if self._settings.api_data_gov_key:
            params["api_key"] = self._settings.api_data_gov_key
        return await self._http.get_json(str(self._settings.sam_entity_base_url), params=params, ttl_seconds=3600)

    async def get_exclusions_by_uei(self, uei: str) -> dict[str, Any]:
        params: dict[str, Any] = {"ueiSAM": uei}
        if self._settings.api_data_gov_key:
            params["api_key"] = self._settings.api_data_gov_key
        return await self._http.get_json(str(self._settings.sam_exclusions_base_url), params=params, ttl_seconds=3600)

