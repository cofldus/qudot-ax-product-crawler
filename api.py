from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl


class CrawlRequest(BaseModel):
    url: str
    max_products: int = 10


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


@app.post("/crawl", summary="스토어 크롤링 및 AI 정규화")
async def crawl(req: CrawlRequest) -> dict[str, Any]:
    """스토어 URL을 받아 전 상품을 크롤링하고 PartnerProductCreateInput 목록을 반환한다.

    - `url`: 대상 스토어 URL (네이버 브랜드스토어·스마트스토어·일반 브랜드몰)
    - `max_products`: 최대 수집 상품 수 (기본 10)
    """
    try:
        from app.services.crawl_service import run_crawl
        return await run_crawl(req.url, max_products=req.max_products)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
