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

    # DISCOVERY_FAILED는 상품 단위 시도가 없으므로 total_attempted 에서 제외
    product_errors = [
        e for e in result.errors if e.get("error_type") != "DISCOVERY_FAILED"
    ]
    total_attempted = len(result.products) + len(product_errors)
    total_crawled = len(result.products)

    payload = {
        "store_url": result.store_url,
        "crawler_type": crawler_type,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "summary": {
            "total_discovered": result.discovered_count,
            "total_attempted": total_attempted,
            "total_crawled": total_crawled,
            "total_normalized": total_normalized,
            "failed_count": result.failed_count,
            "partial_count": partial_count,
            "skipped_count": result.skipped_count,
        },
        "products": product_entries,
        "errors": result.errors,
    }

    # Supabase 설정이 있으면 결과를 DB에 저장 (설정 없으면 건너뜀)
    from app.db.repository import save_crawl_result
    await save_crawl_result(payload, cfg)

    return payload
