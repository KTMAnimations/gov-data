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


def test_public_config_and_frontend_root() -> None:
    client = TestClient(app)
    cfg = client.get("/public/config")
    assert cfg.status_code == 200
    body = cfg.json()
    assert "version" in body
    assert "sources" in body

    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers.get("content-type", "")
