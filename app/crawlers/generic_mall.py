from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page

from app.crawlers.base import BaseCrawler
from app.crawlers.naver_store import _normalize_image_url, _parse_price
from app.schemas.raw_product import RawProduct

# 일반 커머스몰에서 상품 상세 URL로 판단할 경로 패턴
_PRODUCT_URL_RE = re.compile(
    r"/(?:product|goods|item|shop|detail)[_/]?\d+"
    r"|[?&](?:no|id|goods_no|product_no|goodsNo)=\d+",
    re.IGNORECASE,
)


class GenericMallCrawler(BaseCrawler):
    """범용 브랜드몰 크롤러.

    happylandmall.com 등 일반 커머스몰에서 상품 URL을 발견하고
    기본 필드(상품명·이미지·가격·상세설명)를 추출한다.
    가격은 DOM 원본에서만 추출한다 — AI 추측 금지.
    """

    async def discover_product_urls(self, store_url: str) -> list[str]:
        assert self._context is not None
        page = await self._context.new_page()
        try:
            return await self._discover(page, store_url)
        finally:
            await page.close()

    async def _discover(self, page: Page, store_url: str) -> list[str]:
        parsed = urlparse(store_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        ok = await self._goto_and_wait(page, store_url)
        if not ok:
            return []

        try:
            hrefs: list[str] = await page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
            )
        except Exception:
            hrefs = []

        seen: dict[str, None] = {}
        for href in hrefs:
            href = href.split("#")[0]
            if not href.startswith("http"):
                href = urljoin(base, href)
            if (
                _PRODUCT_URL_RE.search(href)
                and urlparse(href).netloc == parsed.netloc
            ):
                seen[href] = None

        return list(seen)

    async def extract_product_detail(self, page: Page, product_url: str) -> RawProduct:
        raw = RawProduct(source_url=product_url)

        ok = await self._goto_and_wait(page, product_url)
        if not ok:
            raw.crawl_error = f"페이지 로딩 실패: {product_url}"
            return raw

        # 상품명
        name = await self._extract_name(page)
        if name:
            raw.name = name
            raw.raw_evidence["name"] = "DOM selector"
        else:
            raw.field_errors["name"] = "DOM에서 상품명을 찾지 못함"
            raw.crawl_error = "상품명 추출 실패"

        # 이미지
        img = await self._extract_image(page)
        if img:
            raw.image_urls = [_normalize_image_url(img)]
            raw.raw_evidence["image_url"] = "DOM img"
        else:
            raw.field_errors["image_url"] = "DOM에서 대표 이미지를 찾지 못함"

        # 가격 — 결정적 추출만, AI 추측 불가
        await self._extract_prices(raw, page)

        # 상세 텍스트
        detail = await self._extract_detail(page)
        if detail:
            raw.detail_text = detail
            raw.raw_evidence["detail_text"] = f"DOM selector (len={len(detail)})"
        else:
            raw.field_errors["detail_text"] = "DOM에서 상세설명을 찾지 못함"

        return raw

    async def _extract_name(self, page: Page) -> str | None:
        selectors = [
            # Godomall(NHN Commerce) 모바일
            "#prd_name", ".prd_name", "#goods_prd_name",
            ".item_detail_tit", ".prd_detail .prd_name",
            # 공통 커머스 패턴
            "h1.goods_name", "h1.product_name", ".goods_name h1",
            "h2.product_name", ".product_title h1", ".product_title h2",
            "#goods_name", "#product_name", ".item_name",
            ".goods_name", ".product_name",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 2:
                        return text
            except Exception:
                continue
        # 페이지 타이틀 fallback — "|" 또는 "-" 앞 부분을 상품명으로 사용
        try:
            title = await page.title()
            if title and len(title) > 2:
                for sep in ("|", "-", "–", "::"):
                    if sep in title:
                        candidate = title.split(sep)[0].strip()
                        if len(candidate) > 2:
                            return candidate
                return title.strip()
        except Exception:
            pass
        return None

    async def _extract_image(self, page: Page) -> str | None:
        selectors = [
            "#goods_image img", "#product_image img",
            ".goods_image img", ".product_img img",
            ".item_photo img", ".thumb_image img",
            "img[id*='main']", "img[class*='main']",
            ".swiper-slide img",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    src = (
                        await el.get_attribute("src")
                        or await el.get_attribute("data-src")
                    )
                    if src and len(src) > 5:
                        return src
            except Exception:
                continue
        return None

    async def _extract_prices(self, raw: RawProduct, page: Page) -> None:
        """판매가를 DOM에서 결정적으로 추출한다. AI 추측 금지."""
        sale_selectors = [
            (".price_sale strong", "price_sale.strong"),
            ("#span_sale_price", "span_sale_price"),
            (".sale_price", "sale_price"),
            ("#goods_price", "goods_price"),
            (".goods_price", "goods_price_class"),
            ("[class*='sale_price']", "sale_price_class"),
            ("[class*='price_sale']", "price_sale_class"),
            ("[class*='price'] strong", "price.strong"),
            ("[class*='price']", "price_class"),
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
                        raw.raw_evidence["price_dom"] = {
                            "method": "DOM selector",
                            "selector": label,
                            "text": text,
                        }
                        break
            except Exception:
                continue

        if not raw.sales_price:
            raw.field_errors["sales_price"] = "DOM 가격 selector 미매칭"

    async def _extract_detail(self, page: Page) -> str | None:
        selectors = [
            "#goods_detail", "#product_detail",
            ".goods_desc", ".product_desc",
            "[class*='detail_content']", "[class*='goods_detail']",
            "[class*='product_detail']",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 10:
                        return text[:3000]
            except Exception:
                continue
        return None
