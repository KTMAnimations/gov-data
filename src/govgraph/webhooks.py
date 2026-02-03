from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 5
RETRY_DELAYS = [60, 300, 900, 3600, 7200]  # 1min, 5min, 15min, 1hr, 2hr


def generate_secret() -> str:
    """Generate a cryptographically secure webhook secret."""
    return secrets.token_urlsafe(32)


def sign_body(secret: str, body: bytes) -> str:
    """Sign the webhook body using HMAC-SHA256."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify a webhook signature (for documentation/example purposes)."""
    expected = sign_body(secret, body)
    return hmac.compare_digest(expected, signature)


def get_retry_delay(attempt: int) -> timedelta:
    """Get the retry delay for a given attempt number (exponential backoff)."""
    if attempt >= len(RETRY_DELAYS):
        return timedelta(seconds=RETRY_DELAYS[-1])
    return timedelta(seconds=RETRY_DELAYS[attempt])


@dataclass(frozen=True)
class WebhookSendResult:
    delivery_id: str
    status_code: int
    success: bool
    error: str | None = None


async def send_webhook(
    *,
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    event_type: str,
    payload: dict[str, Any],
    timeout_seconds: int = 10,
) -> WebhookSendResult:
    """
    Send a webhook with proper error handling.

    Returns a result object indicating success/failure.
    Does not raise exceptions on HTTP errors.
    """
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

    try:
        resp = await client.post(url, content=body, headers=headers, timeout=timeout_seconds)

        # Consider 2xx as success
        success = 200 <= resp.status_code < 300

        if not success:
            logger.warning(
                f"Webhook delivery failed",
                extra={
                    "delivery_id": delivery_id,
                    "url": url,
                    "status_code": resp.status_code,
                    "event_type": event_type,
                }
            )
        else:
            logger.info(
                f"Webhook delivered successfully",
                extra={
                    "delivery_id": delivery_id,
                    "url": url,
                    "status_code": resp.status_code,
                    "event_type": event_type,
                }
            )

        return WebhookSendResult(
            delivery_id=delivery_id,
            status_code=resp.status_code,
            success=success,
            error=None if success else f"HTTP {resp.status_code}",
        )

    except httpx.TimeoutException as e:
        logger.error(
            f"Webhook delivery timed out",
            extra={"delivery_id": delivery_id, "url": url, "error": str(e)}
        )
        return WebhookSendResult(
            delivery_id=delivery_id,
            status_code=0,
            success=False,
            error=f"Timeout: {e}",
        )

    except httpx.RequestError as e:
        logger.error(
            f"Webhook delivery failed with request error",
            extra={"delivery_id": delivery_id, "url": url, "error": str(e)}
        )
        return WebhookSendResult(
            delivery_id=delivery_id,
            status_code=0,
            success=False,
            error=f"Request error: {e}",
        )

    except Exception as e:
        logger.exception(
            f"Unexpected error during webhook delivery",
            extra={"delivery_id": delivery_id, "url": url}
        )
        return WebhookSendResult(
            delivery_id=delivery_id,
            status_code=0,
            success=False,
            error=f"Unexpected error: {e}",
        )


async def send_webhook_with_retry(
    *,
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    event_type: str,
    payload: dict[str, Any],
    timeout_seconds: int = 10,
    max_retries: int = MAX_RETRIES,
) -> WebhookSendResult:
    """
    Send a webhook with automatic retries on failure.

    Uses exponential backoff between retries.
    """
    import asyncio

    last_result: WebhookSendResult | None = None

    for attempt in range(max_retries):
        result = await send_webhook(
            client=client,
            url=url,
            secret=secret,
            event_type=event_type,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )

        last_result = result

        if result.success:
            return result

        # Don't retry on 4xx client errors (except 429 rate limit)
        if 400 <= result.status_code < 500 and result.status_code != 429:
            logger.info(
                f"Not retrying webhook due to client error",
                extra={
                    "delivery_id": result.delivery_id,
                    "status_code": result.status_code,
                    "attempt": attempt + 1,
                }
            )
            return result

        # Wait before retry (except on last attempt)
        if attempt < max_retries - 1:
            delay = get_retry_delay(attempt)
            logger.info(
                f"Retrying webhook delivery",
                extra={
                    "delivery_id": result.delivery_id,
                    "attempt": attempt + 1,
                    "next_attempt": attempt + 2,
                    "delay_seconds": delay.total_seconds(),
                }
            )
            await asyncio.sleep(delay.total_seconds())

    return last_result or WebhookSendResult(
        delivery_id=str(uuid4()),
        status_code=0,
        success=False,
        error="Max retries exceeded",
    )
