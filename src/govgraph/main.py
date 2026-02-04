from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, date
from pathlib import Path
from re import sub as re_sub
from typing import Any, AsyncIterator, Annotated
from fastapi import Path as PathParam
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from govgraph.auth import require_api_key
from govgraph.clients.http import CachedHttpClient
from govgraph.clients.sam import SamClient
from govgraph.clients.usaspending import UsaSpendingClient
from govgraph.db import connect, init_db, ThreadSafeDatabase
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
from govgraph.security import redact_url
from govgraph.settings import Settings
from govgraph.webhooks import generate_secret, send_webhook

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Avoid logging full request URLs (may include secrets in query params).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

settings = Settings()
db_conn: ThreadSafeDatabase = connect(settings.db_path)
init_db(db_conn)

# Shared HTTP client for the application
_http_client: httpx.AsyncClient | None = None


def _create_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _http_client
    # Create shared HTTP client
    _http_client = _create_http_client()
    logger.info("Application started", extra={"version": "0.1.0"})

    task: asyncio.Task | None = None
    if settings.enable_poller:
        async def _run() -> None:
            cached_http = CachedHttpClient(client=_http_client, db_conn=db_conn, default_ttl_seconds=60)
            sam = SamClient(http=cached_http, settings=settings)
            await run_opportunity_poller(
                db_conn=db_conn,
                sam=sam,
                http_client=_http_client,
                poll_interval_seconds=settings.poll_interval_seconds,
                webhook_timeout_seconds=settings.webhook_timeout_seconds,
            )

        task = asyncio.create_task(_run())
        logger.info("Opportunity poller started", extra={"interval": settings.poll_interval_seconds})

    yield

    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        logger.info("Opportunity poller stopped")

    # Clean up HTTP client
    if _http_client:
        await _http_client.aclose()
        _http_client = None

    # Close database connection
    db_conn.close()
    logger.info("Application shutdown complete")


app = FastAPI(title="GovGraph", version="0.1.0", lifespan=_lifespan)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _strip_html(text: str) -> str:
    """Minimal sanitization for upstream error bodies that may return HTML."""
    return re_sub(r"<[^>]+>", "", text).strip()


def _parse_upstream_error(body: str) -> tuple[str | None, str | None]:
    try:
        parsed = json.loads(body)
    except Exception:
        return None, None

    if not isinstance(parsed, dict):
        return None, None

    err = parsed.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        message = err.get("message") or err.get("detail") or err.get("description")
        return (str(code) if code is not None else None, str(message) if message is not None else None)
    if isinstance(err, str):
        # Some upstreams put a short code/message under "error".
        return err, parsed.get("message") if isinstance(parsed.get("message"), str) else None

    code = parsed.get("code") or parsed.get("error_code")
    message = parsed.get("message") or parsed.get("detail") or parsed.get("error_description")
    return (str(code) if code is not None else None, str(message) if message is not None else None)


def _raise_upstream_error(*, source: str, err: httpx.HTTPStatusError, help_hint: str) -> None:
    body = err.response.text or ""
    upstream_error_code, upstream_error_message = _parse_upstream_error(body)
    message = help_hint
    if upstream_error_code == "API_KEY_INVALID":
        message = (
            "SAM.gov reports the configured api.data.gov key is invalid. "
            "Verify GOVGRAPH_API_DATA_GOV_KEY in .env (no quotes/spaces) and that the key is activated."
        )
    if upstream_error_message:
        suffix = f"Upstream: {upstream_error_message}"
        if upstream_error_code:
            suffix = f"{suffix} ({upstream_error_code})"
        message = f"{message} {suffix}"

    redacted_upstream_url = redact_url(str(err.request.url))
    logger.error(
        "Upstream API error",
        extra={
            "source": source,
            "status_code": err.response.status_code,
            "url": redacted_upstream_url,
            "upstream_error_code": upstream_error_code,
        }
    )
    raise HTTPException(
        status_code=502,
        detail={
            "error": "upstream_error",
            "source": source,
            "upstream_status": err.response.status_code,
            "message": message,
            "upstream_error_code": upstream_error_code,
            "upstream_error_message": upstream_error_message,
            "upstream_body": body[:4000],
            "upstream_body_text": _strip_html(body)[:4000],
            "upstream_url": redacted_upstream_url,
        },
    )


def _raise_upstream_request_error(*, source: str, err: httpx.RequestError, help_hint: str) -> None:
    redacted_upstream_url = redact_url(str(err.request.url)) if err.request else None
    logger.error(
        "Upstream request error",
        extra={
            "source": source,
            "url": redacted_upstream_url,
            "error_type": type(err).__name__,
        },
    )
    raise HTTPException(
        status_code=502,
        detail={
            "error": "upstream_unreachable",
            "source": source,
            "message": help_hint,
            "upstream_url": redacted_upstream_url,
            "upstream_error": type(err).__name__,
        },
    )


def _raise_upstream_decode_error(*, source: str, upstream_url: str, err: Exception, help_hint: str) -> None:
    redacted_upstream_url = redact_url(upstream_url)
    logger.error(
        "Upstream decode error",
        extra={
            "source": source,
            "url": redacted_upstream_url,
            "error": str(err),
        },
    )
    raise HTTPException(
        status_code=502,
        detail={
            "error": "upstream_invalid_response",
            "source": source,
            "message": help_hint,
            "upstream_url": redacted_upstream_url,
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


def _get_http_client() -> httpx.AsyncClient:
    """Get the shared HTTP client."""
    global _http_client
    if _http_client is None:
        # Defensive fallback for environments that don't run lifespan events (or in tests).
        _http_client = _create_http_client()
        logger.warning("HTTP client lazily initialized (lifespan not active)")
    return _http_client


def _get_clients() -> tuple[CachedHttpClient, SamClient, UsaSpendingClient]:
    """Get API clients using the shared HTTP client."""
    client = _get_http_client()
    cached_http = CachedHttpClient(client=client, db_conn=db_conn, default_ttl_seconds=60)
    sam = SamClient(http=cached_http, settings=settings)
    usaspending = UsaSpendingClient(http=cached_http, settings=settings)
    return cached_http, sam, usaspending


@app.get("/public/config", response_model=PublicConfig)
async def public_config() -> PublicConfig:
    """Get public API configuration (no auth required)."""
    return PublicConfig(
        version="0.1.0",
        requires_api_key=bool(settings.api_key),
        enable_poller=bool(settings.enable_poller),
        sources=_source_statuses(),
    )


@app.get("/healthz", response_model=HealthResponse, dependencies=[Depends(require_api_key(settings))])
async def healthz() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(time_utc=datetime.now(tz=UTC), version="0.1.0")


@app.get("/v1/sources", response_model=SourcesResponse, dependencies=[Depends(require_api_key(settings))])
async def sources() -> SourcesResponse:
    """List configured data sources."""
    return SourcesResponse(sources=_source_statuses())


@app.get(
    "/v1/contractors/{uei}",
    response_model=ContractorProfile,
    dependencies=[Depends(require_api_key(settings))],
)
async def contractor_profile(
    uei: Annotated[str, PathParam(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9]+$")]
) -> ContractorProfile:
    """Get contractor profile by UEI."""
    _, sam, usaspending = _get_clients()

    sam_entity: dict[str, Any] | None = None
    sam_exclusions: dict[str, Any] | None = None
    usaspending_awards: dict[str, Any] | None = None

    provenance = {
        "sam_entity_base_url": str(settings.sam_entity_base_url),
        "sam_exclusions_base_url": str(settings.sam_exclusions_base_url),
        "usaspending_base_url": str(settings.usaspending_base_url),
    }

    # Fetch from each source with proper error logging
    try:
        sam_entity = await sam.get_entity_by_uei(uei)
    except httpx.HTTPStatusError as e:
        logger.warning(f"SAM entity lookup failed", extra={"uei": uei, "status": e.response.status_code})
    except Exception as e:
        logger.warning(f"SAM entity lookup error", extra={"uei": uei, "error": str(e)})

    try:
        sam_exclusions = await sam.get_exclusions_by_uei(uei)
    except httpx.HTTPStatusError as e:
        logger.warning(f"SAM exclusions lookup failed", extra={"uei": uei, "status": e.response.status_code})
    except Exception as e:
        logger.warning(f"SAM exclusions lookup error", extra={"uei": uei, "error": str(e)})

    try:
        usaspending_awards = await usaspending.awards_summary_for_uei(uei=uei)
    except httpx.HTTPStatusError as e:
        logger.warning(f"USAspending lookup failed", extra={"uei": uei, "status": e.response.status_code})
    except Exception as e:
        logger.warning(f"USAspending lookup error", extra={"uei": uei, "error": str(e)})

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
    q: Annotated[str | None, Query(max_length=500)] = None,
    posted_from: date | None = None,
    posted_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> dict[str, Any]:
    """Search SAM.gov opportunities (raw response)."""
    # Validate date range
    if posted_from and posted_to and posted_from > posted_to:
        raise HTTPException(status_code=400, detail="posted_from must be before posted_to")

    _, sam, _ = _get_clients()
    try:
        payload = await sam.search_opportunities(
            q=q, posted_from=posted_from, posted_to=posted_to, limit=limit, offset=offset
        )
    except httpx.HTTPStatusError as e:
        _raise_upstream_error(
            source="sam.gov",
            err=e,
            help_hint="SAM.gov rejected the request. Check GOVGRAPH_API_DATA_GOV_KEY (an api.data.gov key) in .env.",
        )
    except httpx.RequestError as e:
        _raise_upstream_request_error(
            source="sam.gov",
            err=e,
            help_hint="Could not reach SAM.gov. Check your network connection and try again.",
        )
    except ValueError as e:
        _raise_upstream_decode_error(
            source="sam.gov",
            upstream_url=str(settings.sam_opportunities_base_url),
            err=e,
            help_hint="SAM.gov returned an invalid response. Try again later.",
        )

    logger.info(
        "Opportunities search completed",
        extra={"q": q, "limit": limit, "offset": offset}
    )
    return {"source": "sam.gov", "query": {"q": q, "posted_from": posted_from, "posted_to": posted_to}, "raw": payload}


@app.get(
    "/v1/opportunities",
    response_model=OpportunitiesResponse,
    dependencies=[Depends(require_api_key(settings))],
)
async def opportunities(
    q: Annotated[str | None, Query(max_length=500)] = None,
    posted_from: date | None = None,
    posted_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> OpportunitiesResponse:
    """Search SAM.gov opportunities (normalized response)."""
    # Validate date range
    if posted_from and posted_to and posted_from > posted_to:
        raise HTTPException(status_code=400, detail="posted_from must be before posted_to")

    _, sam, _ = _get_clients()
    try:
        payload = await sam.search_opportunities(
            q=q, posted_from=posted_from, posted_to=posted_to, limit=limit, offset=offset
        )
    except httpx.HTTPStatusError as e:
        _raise_upstream_error(
            source="sam.gov",
            err=e,
            help_hint="SAM.gov rejected the request. Check GOVGRAPH_API_DATA_GOV_KEY (an api.data.gov key) in .env.",
        )
    except httpx.RequestError as e:
        _raise_upstream_request_error(
            source="sam.gov",
            err=e,
            help_hint="Could not reach SAM.gov. Check your network connection and try again.",
        )
    except ValueError as e:
        _raise_upstream_decode_error(
            source="sam.gov",
            upstream_url=str(settings.sam_opportunities_base_url),
            err=e,
            help_hint="SAM.gov returned an invalid response. Try again later.",
        )

    items = [
        OpportunityItem(external_id=o.external_id, title=o.title, posted_at=o.posted_at, raw=o.raw)
        for o in normalize_sam_opportunities(payload)
    ]

    logger.info(
        "Opportunities search completed",
        extra={"q": q, "limit": limit, "offset": offset, "results": len(items)}
    )

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
    """Create a new webhook subscription."""
    # Generate unique ID using UUID
    sub_id = f"sub_{uuid4().hex[:12]}"
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

    logger.info(
        "Webhook subscription created",
        extra={"subscription_id": sub_id, "event_type": req.event_type}
    )

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
    """Get a webhook subscription by ID."""
    row = db_conn.execute("SELECT * FROM webhook_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        filters = json.loads(row["filters_json"])
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in filters for subscription {sub_id}")
        filters = {}

    return WebhookSubscription(
        id=row["id"],
        url=row["url"],
        event_type=row["event_type"],
        filters=filters,
        secret=row["secret"],
        active=bool(row["active"]),
        created_at_utc=datetime.fromisoformat(row["created_at"]),
    )


@app.delete(
    "/v1/webhooks/subscriptions/{sub_id}",
    dependencies=[Depends(require_api_key(settings))],
)
async def delete_webhook_subscription(sub_id: str) -> dict[str, Any]:
    """Deactivate a webhook subscription."""
    db_conn.execute("UPDATE webhook_subscriptions SET active = 0 WHERE id = ?", (sub_id,))
    db_conn.commit()

    logger.info("Webhook subscription deactivated", extra={"subscription_id": sub_id})

    return {"deleted": True, "id": sub_id}


@app.post(
    "/v1/webhooks/subscriptions/{sub_id}/test",
    response_model=WebhookDelivery,
    dependencies=[Depends(require_api_key(settings))],
)
async def test_webhook_subscription(sub_id: str) -> WebhookDelivery:
    """Send a test webhook delivery."""
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

    client = _get_http_client()
    result = await send_webhook(
        client=client,
        url=row["url"],
        secret=row["secret"],
        event_type=row["event_type"],
        payload=payload,
        timeout_seconds=settings.webhook_timeout_seconds,
    )

    logger.info(
        "Test webhook sent",
        extra={
            "subscription_id": sub_id,
            "delivery_id": result.delivery_id,
            "success": result.success,
        }
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
