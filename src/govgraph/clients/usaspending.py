from __future__ import annotations

from typing import Any

from govgraph.clients.http import CachedHttpClient
from govgraph.settings import Settings


class UsaSpendingClient:
    def __init__(self, *, http: CachedHttpClient, settings: Settings):
        self._http = http
        self._settings = settings

    async def awards_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{str(self._settings.usaspending_base_url).rstrip('/')}/awards/search/"
        return await self._http.post_json(url, json_body=payload, ttl_seconds=300)

    async def awards_summary_for_uei(self, *, uei: str, limit: int = 25) -> dict[str, Any]:
        # USAspending filters vary; this is best-effort and intentionally conservative.
        payload = {
            "filters": {
                # Known to exist in some deployments; if upstream rejects, callers can retry with different filters.
                "recipient_uei": [uei],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Awarding Agency",
                "Award Type",
                "Last Modified Date",
            ],
            "page": 1,
            "limit": limit,
            "sort": "Award Amount",
            "order": "desc",
        }
        return await self.awards_search(payload)

