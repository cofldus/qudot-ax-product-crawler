from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"
    llm_timeout: int = 30
    detail_text_limit: int = 2000

    # 크롤링 제한
    max_products: int = 10
    request_delay_min: float = 0.5
    request_delay_max: float = 1.5
    crawl_retry_count: int = 2
    max_consecutive_failures: int = 5

    # Playwright
    playwright_headless: bool = True
    playwright_timeout: int = 30_000

    # Supabase (선택 — 설정 시 크롤링 결과를 DB에 저장)
    supabase_url: str = ""
    supabase_key: str = ""

    # 가산점 기능 (모두 기본 비활성 — 명시적으로 켜야 동작)
    enable_lowest_price: bool = False   # 네이버 쇼핑 최저가 실크롤링
    enable_recovery: bool = False       # 페이지 텍스트에서 option_texts 보조 분석 (LLM 사용)
    incremental: bool = False           # 증분 재크롤 (24h 이내 수집분 건너뜀)

    # 출력
    output_dir: str = "outputs"

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def require_api_key(self) -> str:
        """AI 호출 직전에 키 존재 여부를 확인한다. 없으면 즉시 실패."""
        if not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY가 설정되지 않았습니다. "
                ".env 파일에 키를 추가하거나 환경변수로 전달하세요."
            )
        return self.anthropic_api_key


settings = Settings()


# ── URL → 크롤러 타입 분류 ──────────────────────────────────────────

_NAVER_STORE_HOSTS = frozenset(
    {
        "brand.naver.com",
        "smartstore.naver.com",
    }
)


def detect_crawler_type(url: str) -> str:
    """URL의 실제 호스트(netloc) 기준으로 사용할 크롤러 종류를 반환한다.

    Returns:
        "naver_store" | "generic"
    """
    host = urlparse(url).netloc.lower()
    if host in _NAVER_STORE_HOSTS:
        return "naver_store"
    return "generic"
