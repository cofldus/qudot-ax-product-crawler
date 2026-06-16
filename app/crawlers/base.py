from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from app.config import Settings, settings as default_settings
from app.schemas.raw_product import RawProduct


@dataclass
class CrawlResult:
    store_url: str
    products: list[RawProduct] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    discovered_count: int = 0
    skipped_count: int = 0  # 증분 재크롤로 건너뛴 URL 수

    @property
    def success_count(self) -> int:
        return sum(1 for p in self.products if p.is_valid)

    @property
    def failed_count(self) -> int:
        return len(self.errors)


class BaseCrawler(ABC):
    """모든 크롤러의 추상 기반 클래스.

    공통 제공:
    - Playwright 컨텍스트 생명주기 관리
    - 상품 간 요청 지연 (요청 부하 최소화)
    - 상품별 retry + exponential backoff
    - per-product 실패 격리
    - 연속 실패 감지 및 조기 종료
    - 부분 결과 보존 (중단 시 수집분 반환)
    """

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "BaseCrawler":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.cfg.playwright_headless,
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"},
        )
        self._context.set_default_timeout(self.cfg.playwright_timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @abstractmethod
    async def discover_product_urls(self, store_url: str) -> list[str]:
        """스토어 URL에서 상품 상세 URL 목록을 수집한다."""
        ...

    @abstractmethod
    async def extract_product_detail(self, page: Page, product_url: str) -> RawProduct:
        """상품 상세 페이지에서 RawProduct를 추출한다."""
        ...

    async def crawl(self, store_url: str, max_products: int | None = None) -> CrawlResult:
        """URL 디스커버리 → 순차 추출 → per-product 실패 격리 루프.

        한 상품 실패가 전체 수집을 중단시키지 않는다.
        연속 실패가 임계값을 초과하면 조기 종료하고 부분 결과를 반환한다.
        """
        if self._context is None:
            raise RuntimeError("Crawler must be used with 'async with'.")

        limit = max_products or self.cfg.max_products
        result = CrawlResult(store_url)
        consecutive_failures = 0

        product_urls = await self.discover_product_urls(store_url)

        # 순서를 유지하면서 중복 URL 제거
        product_urls = list(dict.fromkeys(product_urls))
        result.discovered_count = len(product_urls)

        if not product_urls:
            result.errors.append({
                "url": store_url,
                "reason": "상품 URL을 한 개도 수집하지 못했습니다.",
                "error_type": "DISCOVERY_FAILED",
            })
            return result

        page = await self._context.new_page()
        try:
            for url in product_urls[:limit]:
                if consecutive_failures >= self.cfg.max_consecutive_failures:
                    result.errors.append({
                        "url": url,
                        "reason": (
                            f"연속 실패 {consecutive_failures}회 도달 — "
                            "부분 결과를 보존하고 수집을 종료합니다."
                        ),
                        "error_type": "CONSECUTIVE_FAILURES_LIMIT",
                    })
                    break

                # 증분 재크롤 — 최근 24h 이내 수집한 URL은 건너뜀
                if self.cfg.incremental and self.cfg.supabase_url:
                    if await self._is_recently_crawled(url):
                        result.skipped_count += 1
                        continue

                raw = await self._extract_with_retry(page, url)

                # 옵션 보조 분석 — 크롤 성공 후 option_texts가 없을 때만 LLM으로 보완
                if self.cfg.enable_recovery and not raw.crawl_error and not raw.option_texts:
                    try:
                        body_text = await page.evaluate("() => document.body.innerText")
                        from app.ai.recovery import recover_missing_fields
                        await recover_missing_fields(raw, body_text, self.cfg)
                    except Exception:
                        pass

                # 최저가 실크롤링 (네이버 쇼핑)
                if self.cfg.enable_lowest_price and not raw.crawl_error and raw.name:
                    await self._enrich_lowest_price(raw)

                if raw.crawl_error:
                    consecutive_failures += 1
                    result.errors.append({
                        "url": url,
                        "reason": raw.crawl_error,
                        "error_type": "EXTRACT_FAILED",
                    })
                else:
                    consecutive_failures = 0
                    result.products.append(raw)

                await self._random_delay()
        finally:
            await page.close()

        return result

    async def _extract_with_retry(self, page: Page, url: str) -> RawProduct:
        last_error = ""
        for attempt in range(self.cfg.crawl_retry_count + 1):
            try:
                return await self.extract_product_detail(page, url)
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.cfg.crawl_retry_count:
                    await asyncio.sleep(2 ** attempt)

        raw = RawProduct(source_url=url)
        raw.crawl_error = (
            f"재시도 {self.cfg.crawl_retry_count}회 후 실패: {last_error[:200]}"
        )
        return raw

    async def _enrich_lowest_price(self, raw: RawProduct) -> None:
        """별도 페이지에서 네이버 쇼핑 최저가를 조회해 raw에 기록한다.

        오탐 방지 3단계:
        1. sales_price 미추출 시 → 비교 기준 없음, 즉시 중단
        2. 유사도 < 0.35 → 다른 상품 검색 결과로 판단, None 반환
        3. lowest_price < sales_price × 0.1 → 비정상 저가, 반영 안 함
        """
        if raw.sales_price is None:
            raw.field_errors["lowest_price"] = "판매가 미추출로 오탐 검증 불가 — 최저가 건너뜀"
            return

        shopping_page = await self._context.new_page()
        try:
            from app.crawlers.naver_shopping import fetch_lowest_price
            result = await fetch_lowest_price(raw.name or "", shopping_page)
            if result:
                lp = result["price"]
                # 판매가 대비 비정상적으로 낮은 최저가는 오탐으로 간주
                if lp < raw.sales_price * 0.1:
                    raw.field_errors["lowest_price"] = (
                        f"최저가({lp:,}원)가 판매가({raw.sales_price:,}원) 대비 10% 미만 — 오탐 제외"
                    )
                    raw.raw_evidence["lowest_price"] = result
                else:
                    raw.lowest_price = lp
                    raw.raw_evidence["lowest_price"] = result
            else:
                raw.field_errors["lowest_price"] = (
                    "네이버 쇼핑 최저가 조회 실패 — 가격 미검출 또는 유사도 미달"
                )
        except Exception as exc:
            raw.field_errors["lowest_price"] = f"최저가 조회 오류: {exc}"
        finally:
            await shopping_page.close()

    async def _is_recently_crawled(self, url: str, hours: int = 24) -> bool:
        """Supabase에서 해당 URL이 최근 hours 시간 이내 수집됐는지 확인한다."""
        try:
            from app.db.client import get_client
            client = await get_client(self.cfg.supabase_url, self.cfg.supabase_key)
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            res = await (
                client.table("partner_products")
                .select("source_url, crawled_at")
                .eq("source_url", url)
                .gt("crawled_at", cutoff)
                .limit(1)
                .execute()
            )
            return bool(res.data)
        except Exception:
            return False

    async def _random_delay(self) -> None:
        delay = random.uniform(self.cfg.request_delay_min, self.cfg.request_delay_max)
        await asyncio.sleep(delay)

    async def _goto_and_wait(self, page: Page, url: str) -> bool:
        """페이지 이동 후 networkidle 대기. 동적 렌더링 대응을 위해 두 단계로 시도한다."""
        try:
            await page.goto(url, wait_until="networkidle", timeout=self.cfg.playwright_timeout)
            return True
        except Exception:
            pass
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.cfg.playwright_timeout)
            await page.wait_for_timeout(2000)
            return True
        except Exception:
            return False
