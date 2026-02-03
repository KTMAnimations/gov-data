from __future__ import annotations

from fastapi.testclient import TestClient

from govgraph.main import app


def test_healthz() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert "time_utc" in payload

