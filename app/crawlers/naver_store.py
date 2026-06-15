from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Page

from app.config import Settings
from app.crawlers.base import BaseCrawler
from app.schemas.raw_product import RawProduct


# ── 가격 파싱 상수 ──────────────────────────────────────────────────

# "원"이 붙은 숫자만 대상. 퍼센트·옵션 개수·리뷰 수를 가격으로 오인하지 않는다.
_PRICE_TEXT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원")
_PRICE_RANGE = (1_000, 10_000_000)

# 가격 주변 context에서 아래 키워드가 발견되면 이 가격은 제외
# "혜택가"는 sales_price role hint로 사용되므로 "혜택"은 제외 목록에서 뺀다
_EXCLUDE_KW = frozenset({
    "배송비", "무료배송", "쿠폰", "적립", "포인트",
    "이상 구매", "구매 시", "리뷰", "톡톡", "최대",
})

_CONSUMER_KW = frozenset({"소비자가", "정가", "원가", "정상가"})
_SALES_KW = frozenset({"판매가", "할인가", "할인판매가", "최종가", "혜택가"})

# 네이버 상품 detail API: salePrice=정가(consumer), discountedSalePrice=판매가(sales)
_PRICE_KEY_MAP: dict[str, str] = {
    "salePrice": "consumer_price",
    "discountedSalePrice": "sales_price",
    "channelSalePrice": "sales_price",
    "consumerPrice": "consumer_price",
    "originalPrice": "consumer_price",
}

# 오류 페이지 문구 — 이 값이 상품명으로 잡히면 무효 처리한다
_INVALID_PRODUCT_NAMES: frozenset[str] = frozenset({
    "상품이 존재하지 않습니다.",
    "상품이 존재하지 않습니다",
    "페이지를 찾을 수 없습니다.",
    "일시적으로 상품 정보를 불러올 수 없습니다.",
    "판매중지된 상품",
    "접근할 수 없는 상품",
})

_NAME_KEYS = frozenset({"name", "productName", "channelProductName"})
_IMAGE_KEYS = frozenset({"representativeImage", "mainImage", "imageUrl"})
_PRODUCT_ID_KEYS = frozenset({"productNo", "channelProductNo"})

# 상품 목록 API 탐지 — discover 단계에서 사용
_API_URL_PATTERN = re.compile(
    r"products?|channel-products?|category|search", re.IGNORECASE
)

# 상품 상세 API 탐지 — extract 단계에서 사용
# /n/v2/channels/ = 브랜드스토어, /i/v2/channels/ = 스마트스토어
_NAVER_DETAIL_API_RE = re.compile(
    r"/n/v2/channels/|/i/v2/channels/|channel-products?",
    re.IGNORECASE,
)


# ── 헬퍼 함수 (단위 테스트 가능) ────────────────────────────────────

def _parse_price(text: str | None) -> int | None:
    """'N원' 패턴만 파싱한다. 퍼센트·순수 숫자는 가격으로 보지 않는다."""
    if not text:
        return None
    m = _PRICE_TEXT_RE.search(str(text))
    if not m:
        return None
    val = int(m.group(1).replace(",", ""))
    return val if _PRICE_RANGE[0] <= val <= _PRICE_RANGE[1] else None


def _extract_product_id(product_url: str) -> str | None:
    """URL의 /products/<숫자> 세그먼트에서 product_id를 추출한다.

    숫자로만 구성된 마지막 path segment만 허용한다.
    """
    path = urlparse(product_url).path.rstrip("/")
    segment = path.split("/")[-1] if path else ""
    return segment if segment.isdigit() else None


def _is_invalid_product_name(name: str) -> bool:
    """네이버 오류 페이지의 문구가 상품명으로 잡혔는지 판단한다."""
    return name.strip() in _INVALID_PRODUCT_NAMES


def _normalize_image_url(url: str) -> str:
    """//로 시작하는 상대 프로토콜 URL에 https:를 붙인다."""
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _store_base_url(store_url: str) -> str:
    """scheme + netloc + 첫 번째 path segment만 추출한다.

    예) https://brand.naver.com/kefii/category/123 → https://brand.naver.com/kefii
    """
    parsed = urlparse(store_url)
    first_segment = parsed.path.strip("/").split("/")[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/{first_segment}" if first_segment else base


def _response_matches_product_id(data: dict[str, Any], product_id: str) -> bool:
    """API 응답 최상위에 product_id와 일치하는 식별 필드가 있는지 확인한다.

    URL 경로로 상품을 특정할 수 없는 API에서 body 기반 2차 검증에 사용한다.
    """
    if not isinstance(data, dict):
        return False
    for key in ("productNo", "channelProductNo", "id"):
        val = data.get(key)
        if val is not None and str(val) == product_id:
            return True
    return False


# ── __NEXT_DATA__ / API 응답 JSON 탐색 헬퍼 ──────────────────────────

def _collect_product_urls_from_json(data: Any, base_url: str) -> list[str]:
    """JSON에서 productNo/channelProductNo를 재귀 탐색해 상품 URL 목록을 만든다.

    list/product 맥락이 아닌 곳에서 발견된 ID도 수집하지만,
    DOM URL이 최우선이므로 여기서 만든 URL은 보조용이다.
    """
    seen: dict[str, None] = {}

    def _traverse(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if (
                    k in _PRODUCT_ID_KEYS
                    and isinstance(v, (int, str))
                    and str(v).isdigit()
                ):
                    seen[f"{base_url}/products/{v}"] = None
                elif k in ("productUrl", "detailUrl") and isinstance(v, str) and "products/" in v:
                    url = v if v.startswith("http") else f"https:{v}"
                    seen[url.split("?")[0]] = None
                _traverse(v)
        elif isinstance(obj, list):
            for item in obj:
                _traverse(item)

    _traverse(data)
    return list(seen)


def _extract_fields_from_json(data: Any) -> dict[str, Any]:
    """__NEXT_DATA__ JSON에서 이름·가격·이미지·옵션을 재귀 탐색한다."""
    result: dict[str, Any] = {}

    def _traverse(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in _NAME_KEYS and isinstance(v, str) and v and "name" not in result:
                    result["name"] = v
                elif k in _PRICE_KEY_MAP and isinstance(v, (int, float)) and v > 0:
                    target = _PRICE_KEY_MAP[k]
                    if target not in result:
                        result[target] = int(v)
                elif k in _IMAGE_KEYS and "image_url" not in result:
                    if isinstance(v, str) and v.startswith("http"):
                        result["image_url"] = v
                    elif isinstance(v, dict):
                        url = v.get("url") or v.get("src")
                        if url and isinstance(url, str):
                            result["image_url"] = _normalize_image_url(url)
                elif k == "optionItems" and isinstance(v, list) and "options" not in result:
                    opts = [
                        item.get("value") or item.get("name", "")
                        for item in v
                        if isinstance(item, dict)
                    ]
                    result["options"] = [o for o in opts if o]
                _traverse(v)
        elif isinstance(obj, list):
            for item in obj:
                _traverse(item)

    _traverse(data)
    return result


def _analyze_price_candidates(
    body_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None, int | None, str | None]:
    """본문 텍스트에서 'N원' 가격 후보를 분석한다.

    Returns:
        (candidates, excluded_candidates, consumer_price, sales_price, consistency_error)
        candidates: 유효 후보 (price_text, value, context, role_hint)
        excluded_candidates: 제외된 후보 (+ excluded_reason)
        consumer_price: 선택된 정가 (없으면 None)
        sales_price: 선택된 판매가 (없으면 None)
        consistency_error: sales > consumer 발생 시 오류 메시지
    """
    valid: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for m in _PRICE_TEXT_RE.finditer(body_text):
        amount = int(m.group(1).replace(",", ""))
        if not (_PRICE_RANGE[0] <= amount <= _PRICE_RANGE[1]):
            continue

        ctx_start = max(0, m.start() - 30)
        ctx_end = min(len(body_text), m.end() + 30)
        full_ctx = body_text[ctx_start:ctx_end].strip()

        excluded_by = next((kw for kw in _EXCLUDE_KW if kw in full_ctx), None)

        entry: dict[str, Any] = {
            "price_text": m.group(0).strip(),
            "value": amount,
            "context": full_ctx,
            "role_hint": None,
        }

        if excluded_by:
            entry["excluded_reason"] = excluded_by
            excluded.append(entry)
            continue

        if any(kw in full_ctx for kw in _CONSUMER_KW):
            entry["role_hint"] = "consumer_price"
        elif any(kw in full_ctx for kw in _SALES_KW):
            entry["role_hint"] = "sales_price"

        valid.append(entry)

    # 동일 금액 중복 제거 — 역할 힌트 있는 항목 우선
    seen_val: dict[int, dict[str, Any]] = {}
    for c in valid:
        amt = c["value"]
        if amt not in seen_val or (c["role_hint"] and not seen_val[amt]["role_hint"]):
            seen_val[amt] = c
    candidates = list(seen_val.values())

    consumer_price: int | None = None
    sales_price: int | None = None

    if candidates:
        consumer_hints = [c["value"] for c in candidates if c["role_hint"] == "consumer_price"]
        sales_hints = [c["value"] for c in candidates if c["role_hint"] == "sales_price"]
        unclassified = [c["value"] for c in candidates if not c["role_hint"]]

        if consumer_hints:
            consumer_price = max(consumer_hints)
        if sales_hints:
            sales_price = min(sales_hints)

        if not consumer_price and not sales_price:
            amounts = sorted(unclassified)
            if len(amounts) >= 2:
                consumer_price = amounts[-1]
                sales_price = amounts[0]
            elif amounts:
                sales_price = amounts[0]
        elif sales_price and not consumer_price and unclassified:
            bigger = [v for v in unclassified if v > sales_price]
            if bigger:
                consumer_price = max(bigger)

    consistency_error: str | None = None
    if consumer_price and sales_price:
        if consumer_price == sales_price:
            consumer_price = None
        elif sales_price > consumer_price:
            consistency_error = (
                f"fallback sales({sales_price}) > consumer({consumer_price}) "
                "— consumer_price null 처리"
            )
            consumer_price = None

    return candidates, excluded, consumer_price, sales_price, consistency_error


# ── 크롤러 ──────────────────────────────────────────────────────────

class NaverStoreCrawler(BaseCrawler):
    """네이버 브랜드스토어 / 스마트스토어 공용 크롤러.

    URL 발견 우선순위:
      1. DOM에 실제 노출된 a[href*="/products/"] (가장 신뢰도 높음)
      2. __NEXT_DATA__ JSON에서 상품 ID/URL 탐색
      3. API 응답 인터셉트 (보조 — 추천/번들 상품 ID 오탐 가능성 있음)

    상세 추출 우선순위:
      1. /n/v2/ 또는 /i/v2/ channels 상품 detail API (브랜드스토어·스마트스토어 공통)
      2. /products/<product_id>/contents/<content_id>/ contents API
      3. __NEXT_DATA__ JSON fallback
      4. DOM selector fallback
      5. 페이지 본문 텍스트 가격 fallback (가격 전용)
    """

    def __init__(self, cfg: Settings | None = None) -> None:
        super().__init__(cfg)
        # _discover 완료 후 URL 소스별 카운트를 보존 (smoke 출력용)
        self._last_source_counts: dict[str, int] = {}

    async def discover_product_urls(self, store_url: str) -> list[str]:
        assert self._context is not None
        page = await self._context.new_page()
        try:
            return await self._discover(page, store_url)
        finally:
            await page.close()

    async def _discover(self, page: Page, store_url: str) -> list[str]:
        base_url = _store_base_url(store_url)
        intercepted_urls: list[str] = []
        _pending_tasks: list[asyncio.Task] = []

        async def _handle_response(response) -> None:
            if not _API_URL_PATTERN.search(response.url):
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "javascript" not in content_type:
                return
            try:
                body = await response.json()
                intercepted_urls.extend(
                    _collect_product_urls_from_json(body, base_url)
                )
            except Exception:
                pass

        def _on_response(response) -> None:
            task = asyncio.create_task(_handle_response(response))
            _pending_tasks.append(task)

        page.on("response", _on_response)
        ok = await self._goto_and_wait(page, store_url)

        next_data_urls = await self._urls_from_next_data(page, base_url)
        dom_urls = await self._urls_from_dom_scroll(page, base_url)

        page.remove_listener("response", _on_response)
        if _pending_tasks:
            await asyncio.gather(*_pending_tasks, return_exceptions=True)

        if not ok:
            self._last_source_counts = {}
            return []

        api_urls = list(dict.fromkeys(intercepted_urls))

        # DOM 노출 URL 우선 — API 인터셉트는 추천/번들 상품 보완용
        # 소스별로 추적해 smoke 출력에서 신뢰도를 구분한다
        merged: dict[str, None] = {}
        source_map: dict[str, str] = {}

        for u in dom_urls:
            if u not in merged:
                merged[u] = None
                source_map[u] = "DOM"
        for u in next_data_urls:
            if u not in merged:
                merged[u] = None
                source_map[u] = "__NEXT_DATA__"
        for u in api_urls:
            if u not in merged:
                merged[u] = None
                source_map[u] = "API"

        counts: dict[str, int] = {}
        for src in source_map.values():
            counts[src] = counts.get(src, 0) + 1
        self._last_source_counts = counts

        return list(merged)

    async def _urls_from_next_data(self, page: Page, base_url: str) -> list[str]:
        try:
            text: str | None = await page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__'); "
                "return el ? el.textContent : null; }"
            )
            if not text:
                return []
            return _collect_product_urls_from_json(json.loads(text), base_url)
        except Exception:
            return []

    async def _urls_from_dom_scroll(self, page: Page, base_url: str) -> list[str]:
        seen: dict[str, None] = {}
        max_scrolls = 8

        for _ in range(max_scrolls):
            hrefs: list[str] = await page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href*=\"/products/\"]'))"
                ".map(a => a.href)"
            )
            prev_count = len(seen)
            for href in hrefs:
                clean = href.split("?")[0]
                if "/products/" in clean:
                    seen[clean] = None

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)

            if len(seen) == prev_count:
                break

        return list(seen)

    # ── 상세 추출 ────────────────────────────────────────────────────

    async def extract_product_detail(self, page: Page, product_url: str) -> RawProduct:
        raw = RawProduct(source_url=product_url)

        product_id = _extract_product_id(product_url)
        if not product_id:
            raw.crawl_error = "상품 ID 추출 실패"
            return raw

        product_api_data: dict[str, Any] = {}
        contents_api_data: dict[str, Any] = {}
        matched_api_urls: list[str] = []
        unmatched_count: list[int] = [0]
        _pending_tasks: list[asyncio.Task] = []

        async def _handle_apis(response) -> None:
            url = response.url
            resp_url = url.split("?")[0]
            # 채널 API이거나 URL 경로에 product_id가 포함된 경우만 처리
            is_channel_api = bool(_NAVER_DETAIL_API_RE.search(url))
            has_product_in_path = f"/products/{product_id}" in resp_url
            if not is_channel_api and not has_product_in_path:
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                return
            try:
                body = await response.json()
                if not isinstance(body, dict):
                    return
                # URL 경로로 상품 확정: /products/{product_id} 끝 세그먼트
                if resp_url.endswith(f"/products/{product_id}"):
                    product_api_data.update(body)
                    matched_api_urls.append(resp_url)
                # contents API: /products/{product_id}/contents/...
                elif f"/products/{product_id}/contents/" in resp_url:
                    contents_api_data.update(body)
                    matched_api_urls.append(resp_url)
                # body 검증: 상품 ID가 응답 내 식별 필드와 일치하는 경우
                elif _response_matches_product_id(body, product_id):
                    product_api_data.update(body)
                    matched_api_urls.append(resp_url)
                else:
                    unmatched_count[0] += 1
            except Exception:
                pass

        def _on_response(response) -> None:
            task = asyncio.create_task(_handle_apis(response))
            _pending_tasks.append(task)

        page.on("response", _on_response)
        ok = await self._goto_and_wait(page, product_url)
        page.remove_listener("response", _on_response)

        if _pending_tasks:
            await asyncio.gather(*_pending_tasks, return_exceptions=True)

        # 접근 결과 기록 — 로그인 리디렉트·접근 차단 감지
        final_url = page.url
        try:
            page_title = await page.title()
        except Exception:
            page_title = ""

        login_detected = (
            "nid.naver.com" in final_url
            or "login" in final_url.lower()
        )

        raw.raw_evidence["access"] = {
            "final_url": final_url,
            "page_title": page_title,
            "redirect_or_login_detected": login_detected,
            "matched_api_urls": matched_api_urls,
            "unmatched_api_urls_count": unmatched_count[0],
        }

        if login_detected:
            raw.crawl_error = "로그인 페이지로 리디렉트됨 — 비인증 접근 차단"
            return raw

        if not ok:
            raw.crawl_error = f"페이지 로딩 실패: {product_url}"
            return raw

        # 우선순위 1: product detail API
        if product_api_data:
            self._apply_product_api_fields(raw, product_api_data)
            raw.raw_evidence["product_api"] = {
                "matched": True,
                "keys": list(product_api_data.keys())[:20],
            }
        else:
            raw.raw_evidence["product_api"] = {"matched": False}

        # 우선순위 2: contents API (상세설명)
        if contents_api_data:
            self._apply_contents_api_fields(raw, contents_api_data)
            raw.raw_evidence["contents_api"] = {
                "matched": True,
                "keys": list(contents_api_data.keys()),
            }
        else:
            raw.raw_evidence["contents_api"] = {"matched": False}

        # 우선순위 3: __NEXT_DATA__ JSON fallback
        if not raw.name or not raw.sales_price:
            fields = await self._fields_from_next_data(page)
            self._apply_json_fields(raw, fields)

        # 우선순위 4: DOM selector fallback
        if not raw.name:
            raw.name = await self._dom_name(page)
            if raw.name:
                raw.raw_evidence["name"] = "DOM selector fallback"
            else:
                raw.field_errors["name"] = "product API/JSON/__NEXT_DATA__/DOM 모두 실패"

        if not raw.sales_price:
            await self._dom_prices(raw, page)

        if not raw.image_urls:
            img = await self._dom_image(page)
            if img:
                raw.image_urls = [_normalize_image_url(img)]
                raw.raw_evidence["image_url"] = "DOM img fallback"
            else:
                raw.field_errors["image_url"] = "DOM에서 대표 이미지를 찾지 못함"

        if not raw.option_texts:
            raw.option_texts = await self._dom_options(page)
            if raw.option_texts:
                raw.raw_evidence["option_texts"] = "DOM select/button fallback"
            else:
                raw.field_errors["option_texts"] = (
                    "옵션 API/DOM에서 찾지 못함. 단일 옵션 상품일 수 있음"
                )

        if not raw.detail_text:
            raw.detail_text = await self._dom_detail_text(page)
            if raw.detail_text:
                raw.raw_evidence["detail_text"] = "DOM selector fallback"
            else:
                raw.field_errors["detail_text"] = (
                    "API textContent 없음 + DOM selector 미매칭 "
                    "(이미지 기반 상세페이지로 텍스트 추출 제한)"
                )

        if not raw.category_path:
            raw.category_path = await self._dom_category_path(page)
            if not raw.category_path:
                raw.field_errors["category_path"] = "breadcrumb/category DOM selector 미매칭"

        if not product_api_data:
            raw.is_soldout = await self._dom_soldout(page)

        # 오류 페이지 상품명 필터
        if raw.name and _is_invalid_product_name(raw.name):
            raw.name = None
            raw.crawl_error = "상품 상세 페이지가 존재하지 않거나 비정상 응답"
        elif not raw.name:
            raw.crawl_error = "상품명 추출 실패"

        return raw

    def _apply_product_api_fields(self, raw: RawProduct, data: dict[str, Any]) -> None:
        """product detail API(/n/v2/ 또는 /i/v2/ channels) 응답에서 결정적 필드 추출."""
        # 상품명
        name = data.get("name") or data.get("channelProductName") or data.get("dispName")
        if name and isinstance(name, str):
            raw.name = name.strip()
            raw.raw_evidence["name"] = "product detail API"

        # 가격
        # salePrice = 정가/소비자가(consumer_price)
        # discountedSalePrice = 실제 할인 판매가(sales_price)
        sale_price = data.get("salePrice")
        discounted = data.get("discountedSalePrice")

        sale_int = int(sale_price) if isinstance(sale_price, (int, float)) and sale_price > 0 else None
        disc_int = int(discounted) if isinstance(discounted, (int, float)) and discounted > 0 else None

        if disc_int and _PRICE_RANGE[0] <= disc_int <= _PRICE_RANGE[1]:
            raw.sales_price = disc_int
            raw.sales_price_text = f"{disc_int:,}원"
        elif sale_int and _PRICE_RANGE[0] <= sale_int <= _PRICE_RANGE[1]:
            raw.sales_price = sale_int
            raw.sales_price_text = f"{sale_int:,}원"

        if (
            sale_int and disc_int
            and sale_int != disc_int
            and _PRICE_RANGE[0] <= sale_int <= _PRICE_RANGE[1]
        ):
            raw.consumer_price = sale_int
            raw.consumer_price_text = f"{sale_int:,}원"
        elif raw.sales_price and not raw.consumer_price:
            raw.field_errors["consumer_price"] = (
                "할인 전 정가와 판매가가 동일하거나 정가 필드가 없어 null 처리"
            )

        raw.raw_evidence["price_api"] = {
            "method": "product detail API",
            "salePrice": sale_price,
            "discountedSalePrice": discounted,
            "note": "salePrice=정가(consumer), discountedSalePrice=할인가(sales)",
        }

        # 이미지 — representImage + galleryImages, 중복 제거, // 보정
        images: list[str] = []
        repr_img = data.get("representImage")
        if isinstance(repr_img, dict):
            url = repr_img.get("url")
            if url and isinstance(url, str):
                images.append(_normalize_image_url(url))

        for g in data.get("galleryImages", []):
            if isinstance(g, dict):
                url = g.get("url")
                if url and isinstance(url, str):
                    url = _normalize_image_url(url)
                    if url not in images:
                        images.append(url)

        if not images:
            for img in data.get("productImages", []):
                if isinstance(img, dict):
                    url = img.get("url") or img.get("src")
                    if url and isinstance(url, str):
                        url = _normalize_image_url(url)
                        if url not in images:
                            images.append(url)

        if images:
            raw.image_urls = images
            raw.raw_evidence["image_url"] = f"product detail API: {len(images)}개"

        # 옵션 — optionCombinations 우선, fallback으로 simpleOptions/textOptions
        opt_set: dict[str, None] = {}

        for combo in data.get("optionCombinations", []):
            if isinstance(combo, dict):
                parts = [
                    combo.get("optionName1"),
                    combo.get("optionName2"),
                    combo.get("optionName3"),
                ]
                parts = [p for p in parts if p and isinstance(p, str)]
                if parts:
                    opt_set[" / ".join(parts)] = None

        if not opt_set:
            for item in data.get("simpleOptions", []) + data.get("textOptions", []):
                if isinstance(item, dict):
                    val = item.get("value") or item.get("name")
                    if val:
                        opt_set[str(val)] = None

        if opt_set:
            raw.option_texts = list(opt_set)
            raw.raw_evidence["option_texts"] = "product detail API: optionCombinations"

        # 품절 여부
        soldout = data.get("soldout")
        if soldout is not None:
            raw.is_soldout = bool(soldout)

        # 카테고리 — category1~4Name 계층 구조
        category = data.get("category")
        if isinstance(category, dict):
            cat_parts = [
                category.get("category1Name"),
                category.get("category2Name"),
                category.get("category3Name"),
                category.get("category4Name"),
            ]
            cat_parts = [p for p in cat_parts if p and isinstance(p, str)]
            if cat_parts:
                raw.category_path = " > ".join(dict.fromkeys(cat_parts))
                raw.raw_evidence["category_path"] = "product detail API: category hierarchy"

    def _apply_contents_api_fields(self, raw: RawProduct, data: dict[str, Any]) -> None:
        """contents API(/products/<id>/contents/<cid>/) 응답에서 상세설명 추출."""
        text_content = data.get("textContent")
        render_content = data.get("renderContent")

        if isinstance(text_content, str) and len(text_content.strip()) > 10:
            raw.detail_text = text_content.strip()
            raw.raw_evidence["detail_text"] = (
                f"contents API: textContent (len={len(text_content.strip())})"
            )
            return

        if isinstance(render_content, str) and render_content.strip():
            clean = re.sub(r"<[^>]+>", " ", render_content)
            clean = re.sub(r"\s+", " ", clean).strip()
            if len(clean) > 10:
                raw.detail_text = clean
                raw.raw_evidence["detail_text"] = (
                    f"contents API: renderContent HTML stripped "
                    f"(len={len(clean)})"
                )
            else:
                raw.field_errors["detail_text"] = (
                    "이미지 기반 상세페이지로 텍스트 추출 제한"
                )

    async def _fields_from_next_data(self, page: Page) -> dict[str, Any]:
        try:
            text: str | None = await page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__'); "
                "return el ? el.textContent : null; }"
            )
            if not text:
                return {}
            return _extract_fields_from_json(json.loads(text))
        except Exception:
            return {}

    def _apply_json_fields(self, raw: RawProduct, fields: dict[str, Any]) -> None:
        """__NEXT_DATA__ 기반 추출 결과를 raw에 병합한다. 기존 값을 덮어쓰지 않는다."""
        if fields.get("name") and not raw.name:
            raw.name = fields["name"]
            raw.raw_evidence["name"] = "__NEXT_DATA__에서 추출"

        has_price = fields.get("sales_price") or fields.get("consumer_price")
        if has_price:
            raw.raw_evidence["price_json"] = {
                "method": "__NEXT_DATA__ recursive key extraction",
                "sales_price": fields.get("sales_price"),
                "consumer_price": fields.get("consumer_price"),
            }

        if fields.get("sales_price") and not raw.sales_price:
            raw.sales_price = fields["sales_price"]
            raw.sales_price_text = str(fields["sales_price"])
        if fields.get("consumer_price") and not raw.consumer_price:
            raw.consumer_price = fields["consumer_price"]
            raw.consumer_price_text = str(fields["consumer_price"])

        if fields.get("image_url") and not raw.image_urls:
            raw.image_urls = [fields["image_url"]]
            raw.raw_evidence["image_url"] = "__NEXT_DATA__ representativeImage"

        if fields.get("options") and not raw.option_texts:
            raw.option_texts = fields["options"]
            raw.raw_evidence["option_texts"] = "__NEXT_DATA__ optionItems"

    # ── DOM selector fallback 메서드들 ──────────────────────────────

    async def _dom_name(self, page: Page) -> str | None:
        selectors = [
            "._3oDjSvLwozF4 span",
            "[class*='productTitle'] span",
            "[class*='_2-I30XS1lA'] span",
            "h3._2-I30XS1lA",
            ".product_title",
            "h2.product-name",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 1:
                        return text
            except Exception:
                continue

        try:
            h3_texts: list[str] = await page.evaluate(
                "() => Array.from(document.querySelectorAll('h3'))"
                ".map(el => el.textContent.trim())"
                ".filter(t => t.length > 5)"
            )
            if h3_texts:
                return max(h3_texts, key=len)
        except Exception:
            pass

        try:
            title = await page.title()
            if " : " in title:
                candidate = title.split(" : ")[0].strip()
                if len(candidate) > 2:
                    return candidate
            elif title and len(title) > 2:
                return title
        except Exception:
            pass

        return None

    async def _dom_prices(self, raw: RawProduct, page: Page) -> None:
        """구조화 selector로 가격 추출. 실패 시 본문 텍스트 fallback."""
        dom_evidence: dict[str, Any] = {"method": "DOM selector price extraction"}

        sale_selectors = [
            ("[class*='salePrice'] strong", "salePrice.strong"),
            ("[class*='discountedSalePrice'] strong", "discountedSalePrice.strong"),
            ("[class*='finalPrice'] strong", "finalPrice.strong"),
            ("[class*='price_now']", "price_now"),
            ("[class*='price_num']", "price_num"),
            ("[class*='_1LY7DqCnwR']", "_1LY7DqCnwR"),
            ("em[class*='price']", "em.price"),
            ("[class*='price'] strong", "price.strong"),
        ]
        for sel, label in sale_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    price = _parse_price(text)
                    if price:
                        raw.sales_price = price
                        raw.sales_price_text = text
                        dom_evidence["sales_selector"] = label
                        dom_evidence["sales_price_text"] = text
                        break
            except Exception:
                continue

        consumer_selectors = [
            ("[class*='consumerPrice'] span", "consumerPrice.span"),
            ("[class*='originalPrice'] span", "originalPrice.span"),
            ("[class*='regularPrice'] span", "regularPrice.span"),
            ("[class*='_3p6oNb'] span", "_3p6oNb.span"),
            (".price_cancel", "price_cancel"),
            ("del", "del"),
            ("s", "s"),
        ]
        for sel, label in consumer_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    price = _parse_price(text)
                    if price:
                        raw.consumer_price = price
                        raw.consumer_price_text = text
                        dom_evidence["consumer_selector"] = label
                        dom_evidence["consumer_price_text"] = text
                        break
            except Exception:
                continue

        if raw.sales_price or raw.consumer_price:
            raw.raw_evidence["price_dom"] = dom_evidence
            _validate_and_fix_prices(raw)

        if not raw.sales_price:
            await self._text_price_fallback(raw, page)

    async def _text_price_fallback(self, raw: RawProduct, page: Page) -> None:
        """페이지 본문에서 'N원' 패턴 가격 후보를 수집하는 최후 fallback.

        가격 전후 context에서 배송비/쿠폰/포인트 키워드가 발견되면 제외한다.
        """
        try:
            body_text: str = await page.evaluate("() => document.body.innerText")
        except Exception:
            if not raw.sales_price:
                raw.field_errors["sales_price"] = "본문 텍스트 가격 추출 실패"
            return

        candidates, excluded, consumer_price, sales_price, consistency_err = (
            _analyze_price_candidates(body_text)
        )

        if consistency_err:
            raw.field_errors["price_consistency"] = consistency_err

        raw.raw_evidence["price_fallback"] = {
            "method": "DOM text price fallback",
            "candidates": candidates,
            "excluded_candidates": excluded,
            "selected_consumer_price": consumer_price,
            "selected_sales_price": sales_price,
        }

        if sales_price and not raw.sales_price:
            raw.sales_price = sales_price
            raw.sales_price_text = f"{sales_price:,}원"
        elif not sales_price and not raw.sales_price:
            raw.field_errors["sales_price"] = "DOM selector 및 본문 fallback 모두 실패"

        if consumer_price and not raw.consumer_price:
            raw.consumer_price = consumer_price
            raw.consumer_price_text = f"{consumer_price:,}원"
        elif not consumer_price and not raw.consumer_price:
            raw.field_errors["consumer_price"] = (
                "할인 전 정가와 판매가가 동일하거나 정가 필드가 없어 null 처리"
            )

    async def _dom_image(self, page: Page) -> str | None:
        selectors = [
            "[class*='productImageArea'] img",
            "[class*='_2M87qN3cLO'] img",
            ".product_img img",
            ".img_photo",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    src = await el.get_attribute("src")
                    if src and (src.startswith("http") or src.startswith("//")):
                        return src
            except Exception:
                continue
        return None

    async def _dom_options(self, page: Page) -> list[str]:
        try:
            opts: list[str] = await page.evaluate("""
                () => {
                    const texts = [];
                    document.querySelectorAll('select option').forEach(o => {
                        const t = o.textContent.trim();
                        if (t && !t.includes('선택') && !t.startsWith('-')) texts.push(t);
                    });
                    return texts;
                }
            """)
            if opts:
                return list(dict.fromkeys(opts))[:20]
        except Exception:
            pass
        try:
            btns: list[str] = await page.evaluate("""
                () => Array.from(document.querySelectorAll('[class*="option"] button'))
                          .map(b => b.textContent.trim())
                          .filter(t => t.length > 0)
            """)
            return list(dict.fromkeys(btns))[:20]
        except Exception:
            return []

    async def _dom_detail_text(self, page: Page) -> str | None:
        selectors = [
            "[class*='detailContents']",
            "#product-detail",
            ".product_detail",
            "[class*='detail_area']",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 10:
                        return text
            except Exception:
                continue
        return None

    async def _dom_category_path(self, page: Page) -> str | None:
        try:
            path: str = await page.evaluate("""
                () => Array.from(
                    document.querySelectorAll('[class*="breadcrumb"] a, nav[aria-label] a')
                ).map(a => a.textContent.trim()).filter(t => t.length > 0).join(' > ')
            """)
            return path or None
        except Exception:
            return None

    async def _dom_soldout(self, page: Page) -> bool:
        try:
            result: bool = await page.evaluate("""
                () => !!document.querySelector(
                    '[class*="soldOut"], [class*="sold-out"], .sold_out, [class*="SoldOut"]'
                )
            """)
            return bool(result)
        except Exception:
            return False


def _validate_and_fix_prices(raw: RawProduct) -> None:
    """DOM selector로 추출한 가격의 정합성을 검사한다."""
    cp = raw.consumer_price
    sp = raw.sales_price

    if cp is not None and sp is not None:
        if cp == sp:
            raw.consumer_price = None
            raw.consumer_price_text = None
            raw.field_errors["consumer_price"] = (
                "할인 전 정가와 판매가가 동일하거나 정가 필드가 없어 null 처리"
            )
        elif sp > cp:
            raw.field_errors["price_consistency"] = (
                f"DOM selector sales({sp}) > consumer({cp}) "
                "— consumer_price 신뢰도 낮아 null 처리"
            )
            raw.consumer_price = None
            raw.consumer_price_text = None
