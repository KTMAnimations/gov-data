from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from govgraph.clients.sam import SamClient
from govgraph.db import kv_get, kv_set, seen_event_add, seen_event_exists
from govgraph.webhooks import send_webhook


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
    # Cursor stored as YYYY-MM-DD for simplicity.
    cursor_key = "sam.opportunities.posted_from"
    while True:
        try:
            posted_from_str = kv_get(db_conn, cursor_key)
            if posted_from_str:
                posted_from = date.fromisoformat(posted_from_str)
            else:
                posted_from = (datetime.now(tz=UTC) - timedelta(days=2)).date()

            payload = await sam.search_opportunities(q=None, posted_from=posted_from, limit=100, offset=0)
            opportunities = normalize_sam_opportunities(payload)

            # Update cursor optimistically to today (best effort).
            kv_set(db_conn, cursor_key, datetime.now(tz=UTC).date().isoformat())

            # Fetch active subscriptions
            rows = db_conn.execute(
                "SELECT id, url, event_type, filters_json, secret FROM webhook_subscriptions WHERE active = 1"
            ).fetchall()

            for opp in opportunities:
                if seen_event_exists(db_conn, "sam.opportunity", opp.external_id):
                    continue
                seen_event_add(db_conn, "sam.opportunity", opp.external_id)

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
                    # MVP: ignore filters in poller; filters are applied via subscription configuration later.
                    await send_webhook(
                        client=http_client,
                        url=row["url"],
                        secret=row["secret"],
                        event_type=row["event_type"],
                        payload=event_payload,
                        timeout_seconds=webhook_timeout_seconds,
                    )
        except Exception:
            # Keep the poller alive; production should add structured logging and backoff.
            pass

        await asyncio.sleep(poll_interval_seconds)

