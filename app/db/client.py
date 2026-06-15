from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import AsyncClient

_client: "AsyncClient | None" = None


async def get_client(url: str, key: str) -> "AsyncClient":
    """Supabase AsyncClient 싱글턴을 반환한다."""
    global _client
    if _client is None:
        from supabase import acreate_client
        _client = await acreate_client(url, key)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
