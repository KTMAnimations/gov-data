from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx

from govgraph.db import CachedResponse, cache_get, cache_set


def _cache_key(method: str, url: str, params: dict | None, json_body: dict | None) -> str:
    payload = {
        "method": method.upper(),
        "url": url,
        "params": params or {},
        "json": json_body or {},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class CachedHttpClient:
    def __init__(self, *, client: httpx.AsyncClient, db_conn, default_ttl_seconds: int = 60):
        self._client = client
        self._db = db_conn
        self._default_ttl_seconds = default_ttl_seconds

    async def get_json(self, url: str, *, params: dict | None = None, ttl_seconds: int | None = None) -> dict:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        key = _cache_key("GET", url, params, None)
        cached = cache_get(self._db, key)
        if cached and cached.is_fresh(datetime.now(tz=UTC)):
            return cached.body

        resp = await self._client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        cache_set(
            self._db,
            key,
            CachedResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                body=body,
                fetched_at=datetime.now(tz=UTC),
                ttl_seconds=ttl,
            ),
        )
        return body

    async def post_json(
        self, url: str, *, json_body: dict, params: dict | None = None, ttl_seconds: int | None = None
    ) -> dict:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        key = _cache_key("POST", url, params, json_body)
        cached = cache_get(self._db, key)
        if cached and cached.is_fresh(datetime.now(tz=UTC)):
            return cached.body

        resp = await self._client.post(url, params=params, json=json_body, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        cache_set(
            self._db,
            key,
            CachedResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                body=body,
                fetched_at=datetime.now(tz=UTC),
                ttl_seconds=ttl,
            ),
        )
        return body

