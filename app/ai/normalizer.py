from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.prompts import AI_ALLOWED_FIELDS, AI_FORBIDDEN_FIELDS, build_user_prompt, SYSTEM_PROMPT
from app.schemas.partner_product import CategoryGroup, PartnerProductCreateInput
from app.schemas.raw_product import RawProduct

_LOG = logging.getLogger(__name__)


def _build_partner_product(
    raw: RawProduct,
    ai_data: dict[str, Any] | None,
    ai_fields_filled: list[str],
    missing_reasons: dict[str, str],
) -> PartnerProductCreateInput:
    """RawProduct + AI 결과를 PartnerProductCreateInput으로 조합한다.

    결정적 필드(name/price/image)는 raw에서만 복사한다.
    AI 필드는 ai_data가 있을 때만 채운다.
    가격 역전 등 검증 실패 시 problematic 필드를 null로 fallback한다.
    """
    missing = dict(missing_reasons)
    missing.setdefault(
        "lowest_price",
        "시장 최저가 조회 미구현 — Naver 쇼핑 카탈로그 API 연동 필요",
    )

    kwargs: dict[str, Any] = {
        "name": (raw.name or "").strip() or "(상품명 없음)",
        "source_url": raw.source_url,
        "image_url": raw.primary_image_url,
        "consumer_price": raw.consumer_price,
        "sales_price": raw.sales_price,
        "lowest_price": None,
        "raw_evidence": raw.raw_evidence,
        "missing_reasons": missing,
        "ai_fields": list(ai_fields_filled),
    }

    if ai_data:
        if ai_data.get("brand_name"):
            kwargs["brand_name"] = str(ai_data["brand_name"])
        if ai_data.get("option1"):
            kwargs["option1"] = str(ai_data["option1"])
        if ai_data.get("option2"):
            kwargs["option2"] = str(ai_data["option2"])
        if isinstance(ai_data.get("hashtags"), list):
            kwargs["hashtags"] = [str(h) for h in ai_data["hashtags"][:10] if h]
        if ai_data.get("usp"):
            kwargs["usp"] = str(ai_data["usp"])[:300]
        if isinstance(ai_data.get("category_group"), list):
            valid_cats: list[CategoryGroup] = []
            for c in ai_data["category_group"]:
                try:
                    valid_cats.append(CategoryGroup(c))
                except ValueError:
                    missing["category_group"] = (
                        f"AI 반환값 '{c}'가 허용 enum에 없음"
                    )
                    kwargs["missing_reasons"] = missing
            kwargs["category_group"] = valid_cats

    # 가격 역전 방지 — 검증 실패 시 consumer_price만 null로 강등
    try:
        return PartnerProductCreateInput(**kwargs)
    except Exception as exc:
        _LOG.warning("PartnerProductCreateInput 생성 실패, consumer_price null 처리: %s", exc)
        kwargs["consumer_price"] = None
        missing["consumer_price"] = f"가격 검증 실패로 null 처리: {exc}"
        kwargs["missing_reasons"] = missing
        return PartnerProductCreateInput(**kwargs)


async def normalize(
    raw: RawProduct,
    cfg=None,
) -> tuple[PartnerProductCreateInput, str]:
    """RawProduct → PartnerProductCreateInput 변환.

    Returns:
        (partner_product, status)
        "normalized": AI 필드까지 정상 채움
        "raw_only":   결정적 필드만 채움 (API 키 없음 또는 AI 호출 실패)
    """
    from app.config import settings as _default_cfg

    cfg = cfg or _default_cfg
    missing_reasons: dict[str, str] = {}
    ai_fields_filled: list[str] = []

    # detail_text 없으면 AI 기반 hashtags/usp 사전 경고
    if raw.field_errors.get("detail_text"):
        missing_reasons["hashtags"] = "상세설명 추출 실패로 AI 생성 불가"
        missing_reasons["usp"] = "상세설명 추출 실패로 AI 생성 불가"

    if not cfg.anthropic_api_key:
        for field in ("brand_name", "option1", "option2", "category_group"):
            missing_reasons[field] = "ANTHROPIC_API_KEY 미설정"
        missing_reasons.setdefault("hashtags", "ANTHROPIC_API_KEY 미설정")
        missing_reasons.setdefault("usp", "ANTHROPIC_API_KEY 미설정")
        pp = _build_partner_product(raw, None, [], missing_reasons)
        return pp, "partial"

    user_prompt = build_user_prompt(
        name=raw.name,
        category_path=raw.category_path,
        option_texts=raw.option_texts,
        detail_text=raw.detail_text,
        detail_text_limit=cfg.detail_text_limit,
    )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        message = await client.messages.create(
            model=cfg.llm_model,
            max_tokens=1024,
            timeout=cfg.llm_timeout,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = message.content[0].text.strip()

        # 코드블록 제거
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        ai_data: dict[str, Any] = json.loads(raw_text)

        # 금지 필드 제거 — AI가 실수로 가격 등을 반환해도 무시
        for forbidden in AI_FORBIDDEN_FIELDS:
            ai_data.pop(forbidden, None)

        ai_fields_filled = [
            k for k in ai_data if k in AI_ALLOWED_FIELDS and ai_data[k]
        ]
        pp = _build_partner_product(raw, ai_data, ai_fields_filled, missing_reasons)
        return pp, "normalized"

    except Exception as exc:
        _LOG.warning("AI 정규화 실패 (%s): %s", type(exc).__name__, exc)
        for field in ("brand_name", "option1", "option2", "category_group"):
            missing_reasons.setdefault(field, f"AI 호출 실패: {type(exc).__name__}")
        missing_reasons.setdefault("hashtags", f"AI 호출 실패: {type(exc).__name__}")
        missing_reasons.setdefault("usp", f"AI 호출 실패: {type(exc).__name__}")
        pp = _build_partner_product(raw, None, [], missing_reasons)
        return pp, "partial"
