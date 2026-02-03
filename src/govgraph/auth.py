from __future__ import annotations

from fastapi import Header, HTTPException

from govgraph.settings import Settings


def require_api_key(settings: Settings):
    async def _dep(x_api_key: str | None = Header(default=None, alias="X-Api-Key")) -> None:
        if not settings.api_key:
            return
        if not x_api_key or x_api_key != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    return _dep

