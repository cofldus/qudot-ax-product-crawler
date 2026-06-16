from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

_KST = ZoneInfo("Asia/Seoul")

# AI가 채울 수 있는 필드 목록 — 결정적 필드는 절대 포함 불가
_AI_ALLOWED_FIELDS = frozenset(
    {"brand_name", "option1", "option2", "hashtags", "usp", "category_group"}
)


def _now_kst() -> datetime:
    return datetime.now(tz=_KST)


class CategoryGroup(str, Enum):
    """큐닷 상품제안서 카테고리 그룹 — 허용 값 7개."""

    INFANT_FOOD = "유아 식품"
    INFANT_HEALTH = "유아 건강"
    INFANT_PLAY_EDU = "유아 놀이 교육"
    INFANT_LIFE = "유아 생활"
    ETC_FOOD = "기타 식품"
    ETC_TRAVEL = "기타 여행"
    ETC_LIVING = "기타 리빙"


class PartnerProductCreateInput(BaseModel):
    """큐닷 상품제안서 최종 출력 스키마.

    결정적 필드 (크롤링 원본): name, image_url, consumer_price, sales_price, source_url
    AI 생성 필드 (ai_fields에 표기): brand_name, option1, option2, hashtags, usp, category_group
    """

    # ── 결정적 필드 (LLM 미개입) ────────────────────────────────────
    name: str = Field(..., min_length=1, description="상품명 (크롤링 원본)")
    source_url: str = Field(..., description="상품 상세 페이지 URL")
    image_url: str | None = Field(None, description="대표 이미지 URL (크롤링 원본)")
    consumer_price: int | None = Field(None, ge=0, description="정가 (원)")
    sales_price: int | None = Field(None, ge=0, description="판매가 (원)")

    # ── 파생 필드 ────────────────────────────────────────────────────
    lowest_price: int | None = Field(None, ge=0, description="최저가 (--lowest-price 활성화 시 실조회, 실패·미활성화 시 null)")
    discount_rate: float | None = Field(None, ge=0.0, le=100.0, description="할인율 (%)")

    # ── AI 생성 필드 ─────────────────────────────────────────────────
    brand_name: str | None = None
    option1: str | None = None
    option2: str | None = None
    hashtags: list[str] = Field(default_factory=list, max_length=10)
    usp: str | None = Field(None, max_length=300)
    category_group: list[CategoryGroup] = Field(default_factory=list)

    # ── 메타 필드 ────────────────────────────────────────────────────
    # 원본 근거: AI 결과 수동 검수를 위해 추출 원본을 보존
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    # 채우지 못한 필드와 그 사유 ("정보 없음" 금지 — 구체적 이유 필수)
    missing_reasons: dict[str, str] = Field(default_factory=dict)
    # AI가 채운 필드 목록 (결정적 필드와 구분)
    ai_fields: list[str] = Field(default_factory=list)
    crawled_at: datetime = Field(default_factory=_now_kst)

    # ── 검증 ─────────────────────────────────────────────────────────

    @field_validator("ai_fields")
    @classmethod
    def validate_ai_fields(cls, v: list[str]) -> list[str]:
        """ai_fields에 결정적 필드가 포함되면 즉시 거부한다."""
        disallowed = set(v) - _AI_ALLOWED_FIELDS
        if disallowed:
            raise ValueError(
                f"ai_fields에 허용되지 않은 필드가 포함되어 있습니다: {disallowed}. "
                f"허용 필드: {_AI_ALLOWED_FIELDS}"
            )
        return v

    @model_validator(mode="after")
    def compute_and_validate_prices(self) -> "PartnerProductCreateInput":
        cp = self.consumer_price
        sp = self.sales_price

        # 가격 역전 방지
        if cp is not None and sp is not None and sp > cp:
            raise ValueError(
                f"sales_price({sp})가 consumer_price({cp})를 초과할 수 없습니다."
            )

        # discount_rate 자동 계산 (미입력 시)
        if self.discount_rate is None and cp and cp > 0 and sp is not None:
            self.discount_rate = round((cp - sp) / cp * 100, 2)

        return self
