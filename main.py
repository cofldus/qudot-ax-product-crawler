from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Windows cp949 환경에서 한글·특수문자 출력 보장
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _store_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    first = path.split("/")[0] if path else "store"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", first) or "store"


def _serialize(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"직렬화 불가: {type(obj)}")


def _print_summary(payload: dict) -> None:
    s = payload["summary"]
    print(f"\n[완료] {payload['store_url']} ({payload['crawler_type']})")
    print(f"  발견:    {s['total_discovered']}개")
    print(f"  시도:    {s['total_attempted']}개")
    print(f"  크롤링:  {s['total_crawled']}개")
    print(f"  정규화:  {s['total_normalized']}개")
    print(f"  실패:    {s['failed_count']}개")
    print(f"  부분:    {s['partial_count']}개")

    for i, p in enumerate(payload["products"], 1):
        pp = p.get("partner_product") or {}
        name = pp.get("name", "")
        status = p.get("status", "")
        sp = pp.get("sales_price")
        cp = pp.get("consumer_price")
        print(f"\n  [{i}] [{status}] {name}")
        if sp:
            price_str = f"판매가 {sp:,}원"
            if cp:
                price_str += f" / 정가 {cp:,}원"
            print(f"       {price_str}")
        errors = p.get("field_errors", {})
        if errors:
            print(f"       field_errors: {list(errors.keys())}")

    if payload.get("errors"):
        print(f"\n  [오류 {len(payload['errors'])}건]")
        for e in payload["errors"]:
            print(f"    [{e.get('error_type', 'ERR')}] {e.get('url', '')[:70]}")
            print(f"      → {e.get('reason', '')}")


async def _run(url: str, max_products: int, output: str | None) -> None:
    from app.services.crawl_service import run_crawl

    payload = await run_crawl(url, max_products=max_products)

    slug = _store_slug(url)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output:
        out_path = Path(output)
    else:
        out_path = Path("outputs") / f"result_{slug}_{timestamp}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_serialize)

    _print_summary(payload)
    print(f"\n  → {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="큐닷 AX 파트너 상품제안서 수집 CLI"
    )
    parser.add_argument("--url", required=True, help="수집할 스토어 URL")
    parser.add_argument(
        "--max-products", type=int, default=10,
        help="최대 상품 수 (기본 10)",
    )
    parser.add_argument(
        "--output", default=None,
        help="출력 JSON 경로 (기본: outputs/result_{slug}_{timestamp}.json)",
    )
    args = parser.parse_args()

    asyncio.run(_run(args.url, args.max_products, args.output))


if __name__ == "__main__":
    main()
