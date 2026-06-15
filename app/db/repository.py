from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)


async def save_crawl_result(payload: dict[str, Any], cfg) -> None:
    """크롤링 결과를 Supabase에 저장한다.

    - crawl_runs 테이블: 실행 요약 1건 insert
    - partner_products 테이블: 상품별 upsert (source_url 기준)
    SUPABASE_URL / SUPABASE_KEY가 비어 있으면 아무것도 하지 않는다.
    """
    if not (cfg.supabase_url and cfg.supabase_key):
        return

    try:
        from app.db.client import get_client
        client = await get_client(cfg.supabase_url, cfg.supabase_key)
    except Exception as exc:
        _LOG.warning("Supabase 클라이언트 초기화 실패 — DB 저장 건너뜀: %s", exc)
        return

    # ── 1. crawl_runs ────────────────────────────────────────────────
    s = payload["summary"]
    run_row = {
        "store_url": payload["store_url"],
        "crawler_type": payload["crawler_type"],
        "started_at": payload["started_at"],
        "finished_at": payload["finished_at"],
        "total_discovered": s["total_discovered"],
        "total_attempted": s["total_attempted"],
        "total_crawled": s["total_crawled"],
        "total_normalized": s["total_normalized"],
        "failed_count": s["failed_count"],
        "partial_count": s["partial_count"],
    }
    try:
        await client.table("crawl_runs").insert(run_row).execute()
    except Exception as exc:
        _LOG.warning("crawl_runs insert 실패: %s", exc)

    # ── 2. partner_products ──────────────────────────────────────────
    for entry in payload.get("products", []):
        pp = entry.get("partner_product", {})
        if not pp.get("source_url"):
            continue

        row = {
            "source_url": pp["source_url"],
            "store_url": payload["store_url"],
            "name": pp.get("name"),
            "image_url": pp.get("image_url"),
            "brand_name": pp.get("brand_name"),
            "option1": pp.get("option1"),
            "option2": pp.get("option2"),
            "consumer_price": pp.get("consumer_price"),
            "sales_price": pp.get("sales_price"),
            "lowest_price": pp.get("lowest_price"),
            "discount_rate": pp.get("discount_rate"),
            "hashtags": pp.get("hashtags") or [],
            "usp": pp.get("usp"),
            "category_group": [c if isinstance(c, str) else str(c)
                                for c in (pp.get("category_group") or [])],
            "status": entry.get("status", "partial"),
            "ai_fields": pp.get("ai_fields") or [],
            "missing_reasons": pp.get("missing_reasons") or {},
            "raw_evidence": entry.get("raw_evidence") or {},
            "field_errors": entry.get("field_errors") or {},
            "crawled_at": pp.get("crawled_at"),
        }
        try:
            await (
                client.table("partner_products")
                .upsert(row, on_conflict="source_url")
                .execute()
            )
        except Exception as exc:
            _LOG.warning("partner_products upsert 실패 (%s): %s", row["source_url"], exc)

    _LOG.info(
        "Supabase 저장 완료 — %s개 상품 upsert",
        len(payload.get("products", [])),
    )
