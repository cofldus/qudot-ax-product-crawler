from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


@dataclass
class RawProduct:
    """크롤링 직후 원본 데이터를 보존하는 중간 표현.

    검증 없이 추출된 값 그대로 담는다.
    None은 "추출 시도했으나 찾지 못함"을 뜻한다.
    최종 검증은 PartnerProductCreateInput에서 수행한다.
    """

    # 식별
    source_url: str

    # 결정적 필드 (LLM 미개입)
    name: str | None = None
    image_urls: list[str] = field(default_factory=list)

    # 가격 — 원본 텍스트와 파싱된 정수를 모두 보존
    consumer_price_text: str | None = None   # 정가 원본 텍스트 (예: "35,000원")
    sales_price_text: str | None = None      # 판매가 원본 텍스트
    consumer_price: int | None = None        # 파싱된 정가 (원)
    sales_price: int | None = None           # 파싱된 판매가 (원)
    lowest_price: int | None = None          # 네이버 쇼핑 최저가 (--lowest-price 플래그 시 채움)

    # 옵션 — 원본 텍스트 목록 (AI가 정규화 예정)
    option_texts: list[str] = field(default_factory=list)

    # 상세 설명 — 크롤러가 추출한 원본 텍스트 (truncate는 AI normalizer에서 수행)
    detail_text: str | None = None

    # 카테고리 경로 — 네이버 breadcrumb 등 (AI 분류 보조)
    category_path: str | None = None

    # 품절 여부
    is_soldout: bool = False

    # 수집 메타 (KST timezone-aware)
    crawled_at: datetime = field(default_factory=lambda: datetime.now(_KST))

    # 원본 근거 — 각 필드를 어디서 추출했는지 기록
    # 문자열 또는 중첩 dict/list로 상세 evidence를 담을 수 있다.
    # 최종 PartnerProductCreateInput.raw_evidence로 그대로 전달된다.
    raw_evidence: dict[str, Any] = field(default_factory=dict)

    # 개별 필드 추출 실패 사유 (key: 필드명, value: 실패 이유)
    field_errors: dict[str, str] = field(default_factory=dict)

    # 수집 자체가 실패한 경우 (페이지 접근 불가 등)
    crawl_error: str | None = None

    @property
    def is_valid(self) -> bool:
        """최소한의 수집 성공 조건: 상품명과 source_url이 있어야 유효."""
        return bool(self.name and self.source_url and not self.crawl_error)

    @property
    def primary_image_url(self) -> str | None:
        return self.image_urls[0] if self.image_urls else None
