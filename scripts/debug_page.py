"""페이지 구조 즉시 확인용 스크립트."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright
from app.config import settings


async def debug(url: str) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.playwright_headless)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            },
        )
        page = await ctx.new_page()

        print(f"[debug] goto: {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"[debug] networkidle timeout, fallback: {e}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

        final_url = page.url
        title = await page.title()
        print(f"[debug] final URL: {final_url}")
        print(f"[debug] title: {title}")

        # __NEXT_DATA__ 존재 여부
        next_data = await page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); "
            "return el ? el.textContent.slice(0, 300) : null; }"
        )
        print(f"[debug] __NEXT_DATA__ exists: {next_data is not None}")
        if next_data:
            print(f"[debug] __NEXT_DATA__ preview: {next_data[:200]}")

        # /products/ href 수집
        hrefs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href*=\"/products/\"]'))"
            ".map(a => a.href).slice(0, 10)"
        )
        print(f"[debug] /products/ hrefs found: {len(hrefs)}")
        for h in hrefs[:5]:
            print(f"  {h}")

        # 전체 a 태그 href 샘플
        all_hrefs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => a.href).filter(h => h.startsWith('http')).slice(0, 20)"
        )
        print(f"\n[debug] all hrefs sample (first 20):")
        for h in all_hrefs:
            print(f"  {h}")

        # 페이지 스크린샷 저장 (선택)
        await page.screenshot(path="outputs/debug_screenshot.png", full_page=False)
        print("\n[debug] screenshot -> outputs/debug_screenshot.png")

        await browser.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://brand.naver.com/kefii"
    asyncio.run(debug(url))
