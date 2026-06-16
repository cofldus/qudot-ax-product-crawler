from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from playwright.async_api import Page

_LOG = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))

# 반드시 "숫자원" 패턴만 파싱 — 포인트·적립금 등 숫자만 있는 텍스트 제외
_PRICE_RE = re.compile(r"([\d,]+)\s*원")

_PRICE_SELECTORS = [
    ".price_num",
    "[class*='price_num']",
    "[class*='Price_num']",
    ".co_price",
    "[class*='priceText']",
    "[class*='price'] strong",
]

_TITLE_SELECTORS = [
    "[class*='product_title']",
    "[class*='productName']",
    "[class*='title_name']",
    "[class*='ProductTitle']",
    "h2.name",
]


def _jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def fetch_lowest_price(
    product_name: str,
    page: Page,
    min_similarity: float = 0.35,
    timeout: int = 25000,
) -> dict | None:
    """네이버 쇼핑에서 product_name으로 검색해 최저가를 실조회한다.

    가격 파싱 원칙:
    - "N원" 패턴만 인정한다. 단순 숫자(포인트·적립금 등)는 무시한다.

    오탐 방지:
    - 결과 상품명과 Jaccard 유사도가 min_similarity(기본 0.35) 미만이면 None 반환
    - 가격을 찾지 못하면 None 반환

    Returns:
        {
            "price": int,
            "raw_price_text": str,
            "selector": str,
            "source_url": str,
            "matched_title": str,
            "similarity": float,
            "collected_at": str,   # ISO 8601 KST
        }
        또는 None (오탐·미검출·로딩 실패)
    """
    if not product_name:
        return None

    query = product_name.strip()[:40]
    search_url = (
        "https://search.shopping.naver.com/search/all"
        f"?query={query}&sort=price_asc&pagingSize=10"
    )

    try:
        await page.goto(search_url, wait_until="networkidle", timeout=timeout)
        await page.wait_for_timeout(2000)
    except Exception as exc:
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=timeout)
            await page.wait_for_timeout(3000)
        except Exception:
            _LOG.warning("네이버 쇼핑 페이지 로딩 실패 (%s): %s", query, exc)
            return None

    price: int | None = None
    raw_price_text: str = ""
    matched_selector: str = ""
    title: str = ""
    result_url: str = search_url

    for sel in _PRICE_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            text = (await el.inner_text()).strip()
            m = _PRICE_RE.search(text)
            if m:
                candidate = int(m.group(1).replace(",", ""))
                if candidate > 0:
                    price = candidate
                    raw_price_text = text
                    matched_selector = sel
                    break
        except Exception:
            continue

    for sel in _TITLE_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                t = (await el.inner_text()).strip()
                if t:
                    title = t
                    break
        except Exception:
            continue

    try:
        link = await page.query_selector("a[href*='smartstore'], a[href*='brand.naver']")
        if link:
            href = await link.get_attribute("href")
            if href:
                result_url = href
    except Exception:
        pass

    if price is None:
        _LOG.warning("네이버 쇼핑 가격 추출 실패 (원 패턴 없음): query=%r", query)
        return None

    similarity = _jaccard(product_name, title) if title else 0.0
    if similarity < min_similarity:
        _LOG.warning(
            "최저가 오탐 감지 — sim=%.2f < %.2f | query=%r | title=%r",
            similarity, min_similarity, query, title,
        )
        return None

    result = {
        "price": price,
        "raw_price_text": raw_price_text,
        "selector": matched_selector,
        "source_url": result_url,
        "matched_title": title,
        "similarity": round(similarity, 3),
        "collected_at": datetime.now(_KST).isoformat(),
    }
    _LOG.info("최저가 조회 성공: %d원 (sim=%.2f) %r", price, similarity, title)
    return result
