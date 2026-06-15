"""네이버 스토어 크롤러 스모크 테스트 — main.py 없이 단독 실행.

실행 예시:
  python scripts/smoke_naver_store.py --url https://brand.naver.com/kefii --max-products 3
  python scripts/smoke_naver_store.py --url https://smartstore.naver.com/phytonutri --max-products 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows cp949 환경에서 한글·특수문자 출력 보장
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import settings
from app.crawlers.naver_store import NaverStoreCrawler


def _store_slug(url: str) -> str:
    """URL에서 안전한 스토어 식별자를 추출한다.

    예) https://brand.naver.com/kefii/... → kefii
        https://smartstore.naver.com/phytonutri → phytonutri
    """
    path = urlparse(url).path.strip("/")
    first = path.split("/")[0] if path else "unknown"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", first)


def _serialize(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"직렬화 불가: {type(obj)}")


def _print_product(i: int, raw) -> None:
    price_evidence = {
        k: raw.raw_evidence.get(k)
        for k in ("price_api", "price_dom", "price_fallback")
        if k in raw.raw_evidence
    }
    detail_len = len(raw.detail_text) if raw.detail_text else 0

    print(f"\n── 상품 {i} {'─' * 40}")
    print(f"  source_url:      {raw.source_url}")
    print(f"  name:            {raw.name}")
    print(f"  crawl_error:     {raw.crawl_error}")
    print(f"  sales_price:     {raw.sales_price}")
    print(f"  consumer_price:  {raw.consumer_price}")
    print(f"  primary_image:   {raw.primary_image_url}")
    print(f"  options[:3]:     {raw.option_texts[:3]}")
    print(f"  is_soldout:      {raw.is_soldout}")
    print(f"  category_path:   {raw.category_path}")
    print(f"  detail_text:     {'있음 (len=' + str(detail_len) + ')' if raw.detail_text else '없음'}")
    print(f"  evidence keys:   {list(raw.raw_evidence.keys())}")
    if price_evidence:
        print(f"  price evidence:")
        for k, v in price_evidence.items():
            print(f"    [{k}] {v}")
    if raw.field_errors:
        print(f"  field_errors:    {raw.field_errors}")


async def run(url: str, max_products: int) -> None:
    slug = _store_slug(url)
    started_at = datetime.now()
    print(f"\n[smoke] URL:          {url}")
    print(f"[smoke] max_products: {max_products}")
    print(f"[smoke] headless:     {settings.playwright_headless}")

    async with NaverStoreCrawler() as crawler:
        result = await crawler.crawl(url, max_products=max_products)
        source_counts = crawler._last_source_counts

    print(f"\n[smoke] 발견 URL:     {result.discovered_count}개")
    if source_counts:
        breakdown = "  ".join(f"{src}={cnt}" for src, cnt in source_counts.items())
        print(f"[smoke] 소스 분포:    {breakdown}")
    print(f"[smoke] 처리 대상:    {min(result.discovered_count, max_products)}개")
    print(f"[OK]  수집 성공: {result.success_count}개")
    print(f"[ERR] 실패:      {result.failed_count}개")

    for i, raw in enumerate(result.products, 1):
        _print_product(i, raw)

    if result.errors:
        print(f"\n── 오류 목록 {'─' * 38}")
        for e in result.errors:
            print(f"  [{e.get('error_type', 'UNKNOWN')}] {e.get('url', '')[:80]}")
            print(f"    → {e.get('reason', '')}")

    # JSON 저장
    finished_at = datetime.now()
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    out_path = Path("outputs") / f"smoke_{slug}_{timestamp}.json"
    out_path.parent.mkdir(exist_ok=True)

    payload = {
        "store_url": result.store_url,
        "crawler_type": "naver_store",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "summary": {
            "total_discovered": result.discovered_count,
            "total_crawled": len(result.products) + len(result.errors),
            "success_count": result.success_count,
            "failed_count": result.failed_count,
        },
        "products": [asdict(p) for p in result.products],
        "errors": result.errors,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_serialize)

    print(f"\n[smoke] 결과 저장 → {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="네이버 스토어 크롤러 스모크 테스트")
    parser.add_argument("--url", default="https://brand.naver.com/kefii")
    parser.add_argument("--max-products", type=int, default=3)
    args = parser.parse_args()

    asyncio.run(run(args.url, args.max_products))
