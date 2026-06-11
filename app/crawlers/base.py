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

                raw = await self._extract_with_retry(page, url)

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
