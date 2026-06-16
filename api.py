from __future__ import annotations

import html as _html
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class CrawlRequest(BaseModel):
    url: str
    max_products: int = 10
    lowest_price: bool = False
    incremental: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Supabase 연결 정리
    from app.db.client import close_client
    await close_client()


app = FastAPI(
    title="Qudot AX Crawler API",
    description="브랜드몰 URL로 전 상품을 수집하고 큐닷 PartnerProductCreateInput으로 정규화한다.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/products", summary="수집된 상품 목록 조회")
async def products(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    """Supabase partner_products 테이블에서 수집된 상품 목록을 반환한다."""
    try:
        from app.config import settings
        if not settings.supabase_url:
            return {"products": [], "note": "Supabase 미설정"}
        from app.db.client import get_client
        client = await get_client(settings.supabase_url, settings.supabase_key)
        query = client.table("partner_products").select("*").order("crawled_at", desc=True).limit(limit)
        if status:
            query = query.eq("status", status)
        res = await query.execute()
        return {"products": res.data, "count": len(res.data)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/review", summary="검수 UI", response_class=None)
async def review(limit: int = 50):
    """수집된 상품을 HTML 테이블로 시각화하는 검수 UI."""
    from fastapi.responses import HTMLResponse

    try:
        from app.config import settings
        rows: list[dict] = []
        if settings.supabase_url:
            from app.db.client import get_client
            client = await get_client(settings.supabase_url, settings.supabase_key)
            res = await (
                client.table("partner_products")
                .select("*")
                .order("crawled_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = res.data
    except Exception:
        rows = []

    def _row_html(r: dict) -> str:
        # 모든 문자열 값에 html.escape 적용
        def esc(v: object) -> str:
            return _html.escape(str(v)) if v is not None else ""

        name = esc(r.get("name"))
        status = esc(r.get("status"))
        sp = r.get("sales_price")
        cp = r.get("consumer_price")
        lp = r.get("lowest_price")
        img = esc(r.get("image_url"))
        src = esc(r.get("source_url"))
        brand = esc(r.get("brand_name"))
        cat = esc(r.get("category_group"))
        crawled = esc((r.get("crawled_at") or "")[:19])
        badge_color = "#2ecc71" if r.get("status") == "normalized" else "#e67e22"
        img_tag = f'<img src="{img}" style="height:50px;object-fit:contain;" />' if img else "-"
        src_link = f'<a href="{src}" target="_blank" rel="noopener">링크</a>' if src else "-"
        sp_str = f"{sp:,}원" if sp else "-"
        cp_str = f"{cp:,}원" if cp else "-"
        lp_str = f"{lp:,}원" if lp else "-"
        return (
            f"<tr>"
            f"<td>{img_tag}</td>"
            f"<td><a href='{src}' target='_blank' rel='noopener'>{name[:40]}</a></td>"
            f"<td><span style='background:{badge_color};color:#fff;padding:2px 6px;"
            f"border-radius:3px;font-size:11px'>{status}</span></td>"
            f"<td>{sp_str}</td>"
            f"<td>{cp_str}</td>"
            f"<td>{lp_str}</td>"
            f"<td>{brand}</td>"
            f"<td>{cat}</td>"
            f"<td>{crawled}</td>"
            f"</tr>"
        )

    rows_html = "\n".join(_row_html(r) for r in rows) if rows else (
        "<tr><td colspan='9' style='text-align:center;color:#888'>데이터 없음</td></tr>"
    )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>Qudot AX — 검수 UI</title>
<style>
  body {{ font-family: 'Noto Sans KR', sans-serif; margin: 0; background: #f4f6f9; color: #333; }}
  header {{ background: #1a1a2e; color: #fff; padding: 16px 24px; }}
  header h1 {{ margin: 0; font-size: 18px; }}
  .meta {{ font-size: 12px; color: #aaa; margin-top: 4px; }}
  main {{ padding: 20px 24px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  th {{ background: #1a1a2e; color: #fff; padding: 10px 12px; text-align: left; font-size: 12px; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 13px; vertical-align: middle; }}
  tr:hover td {{ background: #f8f9ff; }}
</style>
</head>
<body>
<header>
  <h1>Qudot AX — 파트너 상품 검수</h1>
  <div class="meta">최근 {_html.escape(str(limit))}건 / <a href="/products" style="color:#7ec8e3">JSON API</a></div>
</header>
<main>
<table>
  <thead>
    <tr>
      <th>이미지</th><th>상품명</th><th>상태</th>
      <th>판매가</th><th>정가</th><th>최저가</th>
      <th>브랜드</th><th>카테고리</th><th>수집일시</th>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>
</main>
</body>
</html>"""

    return HTMLResponse(content=html)


@app.post("/crawl", summary="스토어 크롤링 및 AI 정규화")
async def crawl(req: CrawlRequest) -> dict[str, Any]:
    """스토어 URL을 받아 전 상품을 크롤링하고 PartnerProductCreateInput 목록을 반환한다.

    - `url`: 대상 스토어 URL (네이버 브랜드스토어·스마트스토어·일반 브랜드몰)
    - `max_products`: 최대 수집 상품 수 (기본 10)
    - `lowest_price`: 네이버 쇼핑 최저가 실크롤링 활성화 (기본 false)
    - `incremental`: 24h 이내 수집 URL 건너뜀 (Supabase 필요, 기본 false)
    """
    try:
        from app.config import settings
        from app.services.crawl_service import run_crawl
        # 전역 settings를 변경하지 않고 요청별 복사본을 생성한다
        req_cfg = settings.model_copy(update={
            "enable_lowest_price": req.lowest_price,
            "incremental": req.incremental,
        })
        return await run_crawl(req.url, max_products=req.max_products, cfg=req_cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
