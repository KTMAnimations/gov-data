from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@dataclass(frozen=True)
class WebhookSendResult:
    delivery_id: str
    status_code: int


async def send_webhook(
    *,
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    event_type: str,
    payload: dict[str, Any],
    timeout_seconds: int = 10,
) -> WebhookSendResult:
    delivery_id = str(uuid4())
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-GovGraph-Event": event_type,
        "X-GovGraph-Delivery": delivery_id,
        "X-GovGraph-Signature": sign_body(secret, body),
        "X-GovGraph-Timestamp": datetime.now(tz=UTC).isoformat(),
        "User-Agent": "govgraph/0.1.0",
    }
    resp = await client.post(url, content=body, headers=headers, timeout=timeout_seconds)
    return WebhookSendResult(delivery_id=delivery_id, status_code=resp.status_code)

