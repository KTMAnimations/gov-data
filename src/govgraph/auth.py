from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException

from govgraph.settings import Settings

logger = logging.getLogger(__name__)


def require_api_key(settings: Settings):
    """
    FastAPI dependency that validates API key authentication.

    Uses constant-time comparison to prevent timing attacks.
    """
    async def _dep(x_api_key: str | None = Header(default=None, alias="X-Api-Key")) -> None:
        # If no API key is configured, allow all requests
        if not settings.api_key:
            return

        # Missing API key
        if not x_api_key:
            logger.warning("API request rejected: missing API key")
            raise HTTPException(status_code=401, detail="API key required")

        # Use constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(x_api_key.encode("utf-8"), settings.api_key.encode("utf-8")):
            logger.warning("API request rejected: invalid API key")
            raise HTTPException(status_code=401, detail="Invalid API key")

    return _dep

