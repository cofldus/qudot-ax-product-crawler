from __future__ import annotations

# 큐닷 CategoryGroup enum 허용값 7개 — PartnerProductCreateInput과 동기화 유지
_ALLOWED_CATEGORIES: list[str] = [
    "유아 식품",
    "유아 건강",
    "유아 놀이 교육",
    "유아 생활",
    "기타 식품",
    "기타 여행",
    "기타 리빙",
]

# AI가 채울 수 있는 필드 — PartnerProductCreateInput._AI_ALLOWED_FIELDS와 동기화
AI_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"brand_name", "option1", "option2", "hashtags", "usp", "category_group"}
)

# AI가 절대 생성·수정해선 안 되는 필드
AI_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "image_url",
        "consumer_price",
        "sales_price",
        "lowest_price",
        "discount_rate",
        "source_url",
    }
)

SYSTEM_PROMPT = f"""당신은 큐닷(Qudot) 플랫폼 상품 카탈로그 보조 AI다.
입력된 한국 쇼핑몰 상품 정보를 분석하여 아래 허용 필드만 JSON으로 반환한다.

== 허용 필드 ==
- brand_name: 브랜드명 (string 또는 null)
- option1: 주요 옵션 설명 (색상·용량·사이즈 등, string 또는 null)
- option2: 보조 옵션 설명 (option1과 다른 차원의 옵션, string 또는 null)
- hashtags: 상품 관련 해시태그 최대 10개 (string 배열, # 기호 제외)
- usp: 핵심 판매 포인트 300자 이내 한국어 요약 (string 또는 null)
- category_group: 아래 7개 중 해당하는 것 (string 배열)

== 허용 카테고리 ==
{_ALLOWED_CATEGORIES}

== 절대 금지 ==
아래 필드는 생성·수정·추측 금지. 크롤링 원본만 사용한다:
- name (상품명)
- image_url
- consumer_price, sales_price, lowest_price, discount_rate (모든 가격 필드)
- source_url

가격은 어떤 형태로도 AI가 만들어선 안 된다.
추측 불가 시 해당 필드를 null로 두거나 빈 배열로 반환한다.
응답은 허용 필드만 포함한 유효한 JSON 객체여야 한다. 코드블록 없이 순수 JSON만 반환한다."""


def build_user_prompt(
    name: str | None,
    category_path: str | None,
    option_texts: list[str],
    detail_text: str | None,
    detail_text_limit: int = 2000,
) -> str:
    opts_str = ", ".join(option_texts[:15]) if option_texts else "없음"
    detail_str = (detail_text or "")[:detail_text_limit] or "없음"
    return (
        f"상품명: {name or '없음'}\n"
        f"카테고리 경로: {category_path or '없음'}\n"
        f"옵션 목록: {opts_str}\n"
        f"상세설명:\n{detail_str}\n\n"
        "위 정보를 바탕으로 허용 필드만 JSON으로 응답하세요."
    )
