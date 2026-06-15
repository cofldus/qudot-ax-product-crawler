from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.ai.normalizer import normalize
from app.config import detect_crawler_type, settings as default_settings
from app.schemas.raw_product import RawProduct


async def run_crawl(
    store_url: str,
    max_products: int | None = None,
    cfg=None,
) -> dict[str, Any]:
    """스토어 URL을 크롤링하고 AI 정규화까지 실행해 최종 payload를 반환한다.

    crawl_error가 있는 상품은 errors 목록에만 기록하고
    total_normalized에 포함하지 않는다.
    """
    from app.config import settings as _default_cfg

    cfg = cfg or _default_cfg
    started_at = datetime.now()

    crawler_type = detect_crawler_type(store_url)

    if crawler_type == "naver_store":
        from app.crawlers.naver_store import NaverStoreCrawler
        crawler_cls = NaverStoreCrawler
    else:
        from app.crawlers.generic_mall import GenericMallCrawler
        crawler_cls = GenericMallCrawler

    async with crawler_cls(cfg) as crawler:
        result = await crawler.crawl(store_url, max_products=max_products)

    finished_at = datetime.now()

    product_entries: list[dict[str, Any]] = []
    total_normalized = 0
    partial_count = 0

    # crawl_error 없는 상품만 정규화 시도
    for raw in result.products:
        pp, status = await normalize(raw, cfg)
        if status == "normalized":
            total_normalized += 1
        else:
            partial_count += 1

        raw_dict = asdict(raw)
        product_entries.append({
            "status": status,
            "raw_product": raw_dict,
            "partner_product": pp.model_dump(mode="json"),
            "field_errors": raw.field_errors,
            "raw_evidence": raw.raw_evidence,
        })

    return {
        "store_url": result.store_url,
        "crawler_type": crawler_type,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "summary": {
            "total_discovered": result.discovered_count,
            "total_crawled": len(result.products) + len(result.errors),
            "total_normalized": total_normalized,
            "failed_count": result.failed_count,
            "partial_count": partial_count,
        },
        "products": product_entries,
        "errors": result.errors,
    }
