from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import govgraph.main as main
from govgraph.clients.sam import SamClient


def test_raise_upstream_error_redacts_api_key() -> None:
    request = httpx.Request("GET", "https://api.sam.gov/foo?api_key=SECRET&x=1")
    response = httpx.Response(
        status_code=403,
        request=request,
        text='{"error":{"code":"API_KEY_INVALID","message":"An invalid API key was supplied."}}',
    )
    err = httpx.HTTPStatusError("forbidden", request=request, response=response)

    with pytest.raises(HTTPException) as exc:
        main._raise_upstream_error(source="sam.gov", err=err, help_hint="hint")

    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert "SECRET" not in detail.get("upstream_url", "")
    assert "api_key=REDACTED" in detail.get("upstream_url", "")
    assert detail.get("upstream_error_code") == "API_KEY_INVALID"
    assert "invalid" in (detail.get("message") or "").lower()


def test_opportunities_endpoint_works_without_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_search_opportunities(self, **_: object) -> dict:
        return {"opportunitiesData": []}

    monkeypatch.setattr(SamClient, "search_opportunities", _fake_search_opportunities, raising=True)

    client: TestClient | None = None
    try:
        client = TestClient(main.app)
        resp = client.get("/v1/opportunities?q=test&limit=1")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
    finally:
        if client is not None:
            client.close()
        # Ensure the lazy-created AsyncClient doesn't leak across tests.
        if main._http_client is not None:
            asyncio.run(main._http_client.aclose())
            main._http_client = None
