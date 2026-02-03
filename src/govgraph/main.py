from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, date
from pathlib import Path
from re import sub as re_sub
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from govgraph.auth import require_api_key
from govgraph.clients.http import CachedHttpClient
from govgraph.clients.sam import SamClient
from govgraph.clients.usaspending import UsaSpendingClient
from govgraph.db import connect, init_db
from govgraph.models import (
    ContractorProfile,
    HealthResponse,
    OpportunitiesResponse,
    OpportunityItem,
    PublicConfig,
    SourcesResponse,
    SourceStatus,
    WebhookDelivery,
    WebhookSubscription,
    WebhookSubscriptionCreate,
)
from govgraph.poller import normalize_sam_opportunities, run_opportunity_poller
from govgraph.settings import Settings
from govgraph.webhooks import generate_secret, send_webhook


settings = Settings()
db_conn: sqlite3.Connection = connect(settings.db_path)
init_db(db_conn)

@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    task: asyncio.Task | None = None
    if settings.enable_poller:
        async def _run() -> None:
            async with httpx.AsyncClient() as client:
                cached_http = CachedHttpClient(client=client, db_conn=db_conn, default_ttl_seconds=60)
                sam = SamClient(http=cached_http, settings=settings)
                await run_opportunity_poller(
                    db_conn=db_conn,
                    sam=sam,
                    http_client=client,
                    poll_interval_seconds=settings.poll_interval_seconds,
                    webhook_timeout_seconds=settings.webhook_timeout_seconds,
                )

        task = asyncio.create_task(_run())

    yield

    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="GovGraph", version="0.1.0", lifespan=_lifespan)


def _strip_html(text: str) -> str:
    # Minimal sanitization for upstream error bodies that may return HTML.
    # Keep it conservative; we still include the raw body in the error payload.
    return re_sub(r"<[^>]+>", "", text).strip()


def _raise_upstream_error(*, source: str, err: httpx.HTTPStatusError, help_hint: str) -> None:
    body = err.response.text or ""
    raise HTTPException(
        status_code=502,
        detail={
            "error": "upstream_error",
            "source": source,
            "upstream_status": err.response.status_code,
            "message": help_hint,
            "upstream_body": body[:4000],
            "upstream_body_text": _strip_html(body)[:4000],
            "upstream_url": str(err.request.url),
        },
    )


def _source_statuses() -> list[SourceStatus]:
    sam_configured = bool(settings.api_data_gov_key)
    return [
        SourceStatus(name="sam.opportunities", base_url=str(settings.sam_opportunities_base_url), configured=sam_configured),
        SourceStatus(name="sam.entity", base_url=str(settings.sam_entity_base_url), configured=sam_configured),
        SourceStatus(name="sam.exclusions", base_url=str(settings.sam_exclusions_base_url), configured=sam_configured),
        SourceStatus(name="usaspending", base_url=str(settings.usaspending_base_url), configured=True),
    ]


@app.get("/public/config", response_model=PublicConfig)
async def public_config() -> PublicConfig:
    return PublicConfig(
        version="0.1.0",
        requires_api_key=bool(settings.api_key),
        enable_poller=bool(settings.enable_poller),
        sources=_source_statuses(),
    )


def _clients() -> tuple[CachedHttpClient, SamClient, UsaSpendingClient, httpx.AsyncClient]:
    # Per-request client to keep things simple and avoid global event-loop coupling.
    client = httpx.AsyncClient()
    cached_http = CachedHttpClient(client=client, db_conn=db_conn, default_ttl_seconds=60)
    sam = SamClient(http=cached_http, settings=settings)
    usaspending = UsaSpendingClient(http=cached_http, settings=settings)
    return cached_http, sam, usaspending, client


@app.get("/healthz", response_model=HealthResponse, dependencies=[Depends(require_api_key(settings))])
async def healthz() -> HealthResponse:
    return HealthResponse(time_utc=datetime.now(tz=UTC), version="0.1.0")


@app.get("/v1/sources", response_model=SourcesResponse, dependencies=[Depends(require_api_key(settings))])
async def sources() -> SourcesResponse:
    return SourcesResponse(sources=_source_statuses())


@app.get(
    "/v1/contractors/{uei}",
    response_model=ContractorProfile,
    dependencies=[Depends(require_api_key(settings))],
)
async def contractor_profile(uei: str) -> ContractorProfile:
    _, sam, usaspending, client = _clients()
    async with client:
        sam_entity: dict[str, Any] | None = None
        sam_exclusions: dict[str, Any] | None = None
        usaspending_awards: dict[str, Any] | None = None

        provenance = {
            "sam_entity_base_url": str(settings.sam_entity_base_url),
            "sam_exclusions_base_url": str(settings.sam_exclusions_base_url),
            "usaspending_base_url": str(settings.usaspending_base_url),
        }

        try:
            sam_entity = await sam.get_entity_by_uei(uei)
        except Exception:
            sam_entity = None
        try:
            sam_exclusions = await sam.get_exclusions_by_uei(uei)
        except Exception:
            sam_exclusions = None
        try:
            usaspending_awards = await usaspending.awards_summary_for_uei(uei=uei)
        except Exception:
            usaspending_awards = None

        return ContractorProfile(
            uei=uei,
            sam_entity=sam_entity,
            sam_exclusions=sam_exclusions,
            usaspending_awards=usaspending_awards,
            generated_at_utc=datetime.now(tz=UTC),
            provenance=provenance,
        )


@app.get(
    "/v1/opportunities/search",
    dependencies=[Depends(require_api_key(settings))],
)
async def opportunities_search(
    q: str | None = None,
    posted_from: date | None = None,
    posted_to: date | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    _, sam, _, client = _clients()
    async with client:
        try:
            payload = await sam.search_opportunities(
                q=q, posted_from=posted_from, posted_to=posted_to, limit=limit, offset=offset
            )
        except httpx.HTTPStatusError as e:
            _raise_upstream_error(
                source="sam.gov",
                err=e,
                help_hint="SAM.gov rejected the request. Set GOVGRAPH_API_DATA_GOV_KEY (an api.data.gov key) in .env, then restart GovGraph.",
            )
        return {"source": "sam.gov", "query": {"q": q, "posted_from": posted_from, "posted_to": posted_to}, "raw": payload}


@app.get(
    "/v1/opportunities",
    response_model=OpportunitiesResponse,
    dependencies=[Depends(require_api_key(settings))],
)
async def opportunities(
    q: str | None = None,
    posted_from: date | None = None,
    posted_to: date | None = None,
    limit: int = 25,
    offset: int = 0,
) -> OpportunitiesResponse:
    _, sam, _, client = _clients()
    async with client:
        try:
            payload = await sam.search_opportunities(
                q=q, posted_from=posted_from, posted_to=posted_to, limit=limit, offset=offset
            )
        except httpx.HTTPStatusError as e:
            _raise_upstream_error(
                source="sam.gov",
                err=e,
                help_hint="SAM.gov rejected the request. Set GOVGRAPH_API_DATA_GOV_KEY (an api.data.gov key) in .env, then restart GovGraph.",
            )

        items = [
            OpportunityItem(external_id=o.external_id, title=o.title, posted_at=o.posted_at, raw=o.raw)
            for o in normalize_sam_opportunities(payload)
        ]
        return OpportunitiesResponse(
            query={"q": q, "posted_from": posted_from, "posted_to": posted_to, "limit": limit, "offset": offset},
            items=items,
            raw=payload,
        )


@app.post(
    "/v1/webhooks/subscriptions",
    response_model=WebhookSubscription,
    dependencies=[Depends(require_api_key(settings))],
)
async def create_webhook_subscription(req: WebhookSubscriptionCreate) -> WebhookSubscription:
    sub_id = f"sub_{int(datetime.now(tz=UTC).timestamp())}"
    secret = generate_secret()
    created_at = datetime.now(tz=UTC)
    db_conn.execute(
        """
        INSERT INTO webhook_subscriptions(id, created_at, url, event_type, filters_json, secret, active)
        VALUES(?, ?, ?, ?, ?, ?, 1)
        """,
        (
            sub_id,
            created_at.isoformat(),
            str(req.url),
            req.event_type,
            json.dumps(req.filters, sort_keys=True),
            secret,
        ),
    )
    db_conn.commit()
    return WebhookSubscription(
        id=sub_id,
        url=req.url,
        event_type=req.event_type,
        filters=req.filters,
        secret=secret,
        active=True,
        created_at_utc=created_at,
    )


@app.get(
    "/v1/webhooks/subscriptions/{sub_id}",
    response_model=WebhookSubscription,
    dependencies=[Depends(require_api_key(settings))],
)
async def get_webhook_subscription(sub_id: str) -> WebhookSubscription:
    row = db_conn.execute("SELECT * FROM webhook_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return WebhookSubscription(
        id=row["id"],
        url=row["url"],
        event_type=row["event_type"],
        filters=json.loads(row["filters_json"]),
        secret=row["secret"],
        active=bool(row["active"]),
        created_at_utc=datetime.fromisoformat(row["created_at"]),
    )


@app.delete(
    "/v1/webhooks/subscriptions/{sub_id}",
    dependencies=[Depends(require_api_key(settings))],
)
async def delete_webhook_subscription(sub_id: str) -> dict[str, Any]:
    db_conn.execute("UPDATE webhook_subscriptions SET active = 0 WHERE id = ?", (sub_id,))
    db_conn.commit()
    return {"deleted": True, "id": sub_id}


@app.post(
    "/v1/webhooks/subscriptions/{sub_id}/test",
    response_model=WebhookDelivery,
    dependencies=[Depends(require_api_key(settings))],
)
async def test_webhook_subscription(sub_id: str) -> WebhookDelivery:
    row = db_conn.execute("SELECT * FROM webhook_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if not row["active"]:
        raise HTTPException(status_code=409, detail="Subscription is inactive")

    payload = {
        "event_type": row["event_type"],
        "message": "test delivery from govgraph",
        "sent_at": datetime.now(tz=UTC).isoformat(),
    }
    async with httpx.AsyncClient() as client:
        result = await send_webhook(
            client=client,
            url=row["url"],
            secret=row["secret"],
            event_type=row["event_type"],
            payload=payload,
            timeout_seconds=settings.webhook_timeout_seconds,
        )

    return WebhookDelivery(
        event_type=row["event_type"],
        delivery_id=result.delivery_id,
        sent_at_utc=datetime.now(tz=UTC),
        target_url=row["url"],
    )


# Mount the frontend last so API routes take precedence.
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
