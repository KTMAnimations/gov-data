from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from govgraph.clients.sam import SamClient
from govgraph.db import kv_get, kv_set, seen_event_add
from govgraph.security import redact_url
from govgraph.webhooks import send_webhook

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedOpportunity:
    external_id: str
    title: str | None
    posted_at: datetime | None
    raw: dict[str, Any]


def _first_present(obj: dict[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None


def normalize_sam_opportunities(payload: dict[str, Any]) -> list[NormalizedOpportunity]:
    candidates = (
        payload.get("opportunitiesData")
        or payload.get("opportunities")
        or payload.get("data")
        or payload.get("results")
        or []
    )
    if not isinstance(candidates, list):
        return []

    out: list[NormalizedOpportunity] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        external_id = _first_present(item, ["noticeId", "notice_id", "id", "solicitationNumber", "solicitation_number"])
        if not external_id:
            continue

        title = _first_present(item, ["title", "solicitationTitle", "solicitation_title"])
        posted_raw = _first_present(item, ["postedDate", "posted_date", "posted", "publishDate"])
        posted_at: datetime | None = None
        if isinstance(posted_raw, str):
            try:
                posted_at = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
            except ValueError:
                posted_at = None

        out.append(
            NormalizedOpportunity(
                external_id=str(external_id),
                title=str(title) if title is not None else None,
                posted_at=posted_at,
                raw=item,
            )
        )
    return out


async def run_opportunity_poller(
    *,
    db_conn,
    sam: SamClient,
    http_client: httpx.AsyncClient,
    poll_interval_seconds: int,
    webhook_timeout_seconds: int,
) -> None:
    """
    Background poller that fetches new opportunities from SAM.gov and sends webhooks.

    Uses atomic INSERT OR IGNORE to prevent race conditions on seen events.
    """
    cursor_key = "sam.opportunities.posted_from"
    consecutive_errors = 0
    max_backoff_seconds = 300  # 5 minutes max backoff

    while True:
        try:
            posted_from_str = kv_get(db_conn, cursor_key)
            if posted_from_str:
                posted_from = date.fromisoformat(posted_from_str)
            else:
                posted_from = (datetime.now(tz=UTC) - timedelta(days=2)).date()

            logger.debug(
                "Polling for opportunities",
                extra={"posted_from": posted_from.isoformat()}
            )

            payload = await sam.search_opportunities(q=None, posted_from=posted_from, limit=100, offset=0)
            opportunities = normalize_sam_opportunities(payload)

            logger.info(
                "Fetched opportunities from SAM.gov",
                extra={"count": len(opportunities), "posted_from": posted_from.isoformat()}
            )

            # Update cursor optimistically to today (best effort).
            kv_set(db_conn, cursor_key, datetime.now(tz=UTC).date().isoformat())

            # Fetch active subscriptions
            rows = db_conn.execute(
                "SELECT id, url, event_type, filters_json, secret FROM webhook_subscriptions WHERE active = 1"
            ).fetchall()

            new_opportunities = 0
            webhooks_sent = 0

            for opp in opportunities:
                # Use atomic INSERT OR IGNORE to prevent race conditions
                # Returns True if the event was newly added
                is_new = seen_event_add(db_conn, "sam.opportunity", opp.external_id)
                if not is_new:
                    continue

                new_opportunities += 1

                event_payload = {
                    "event_type": "sam.opportunity.created",
                    "source": "sam.gov",
                    "external_id": opp.external_id,
                    "title": opp.title,
                    "posted_at": opp.posted_at.isoformat() if opp.posted_at else None,
                    "raw": opp.raw,
                }

                for row in rows:
                    if row["event_type"] != "sam.opportunity.created":
                        continue

                    result = await send_webhook(
                        client=http_client,
                        url=row["url"],
                        secret=row["secret"],
                        event_type=row["event_type"],
                        payload=event_payload,
                        timeout_seconds=webhook_timeout_seconds,
                    )

                    if result.success:
                        webhooks_sent += 1
                    else:
                        logger.warning(
                            "Webhook delivery failed",
                            extra={
                                "subscription_id": row["id"],
                                "external_id": opp.external_id,
                                "error": result.error,
                            }
                        )

            if new_opportunities > 0:
                logger.info(
                    "Processed new opportunities",
                    extra={"new_opportunities": new_opportunities, "webhooks_sent": webhooks_sent}
                )

            # Reset error counter on success
            consecutive_errors = 0

        except httpx.HTTPStatusError as e:
            consecutive_errors += 1
            logger.error(
                "SAM.gov API error during polling",
                extra={
                    "status_code": e.response.status_code,
                    "consecutive_errors": consecutive_errors,
                }
            )

        except httpx.RequestError as e:
            consecutive_errors += 1
            logger.error(
                "Network error during polling",
                extra={
                    "error_type": type(e).__name__,
                    "url": redact_url(str(e.request.url)) if e.request else None,
                    "consecutive_errors": consecutive_errors,
                }
            )

        except Exception as e:
            consecutive_errors += 1
            logger.exception(
                "Unexpected error during polling",
                extra={"error": str(e), "consecutive_errors": consecutive_errors}
            )

        # Calculate sleep time with exponential backoff on errors
        if consecutive_errors > 0:
            backoff_seconds = min(
                poll_interval_seconds * (2 ** (consecutive_errors - 1)),
                max_backoff_seconds
            )
            logger.info(
                f"Backing off before next poll",
                extra={"backoff_seconds": backoff_seconds, "consecutive_errors": consecutive_errors}
            )
            await asyncio.sleep(backoff_seconds)
        else:
            await asyncio.sleep(poll_interval_seconds)
