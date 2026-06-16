from __future__ import annotations

import json
import logging
from typing import Any

from app.schemas.raw_product import RawProduct

_LOG = logging.getLogger(__name__)

_SYSTEM = (
    "너는 쇼핑몰 상품 페이지 텍스트를 분석하는 전문가다. "
    "반드시 텍스트에서 읽을 수 있는 정보만 보고한다. 없는 정보는 절대 지어내지 않는다."
)

_PROMPT = """\
아래 상품 페이지 텍스트에서 옵션 목록만 추출해줘.
텍스트에 없는 정보는 null 또는 빈 배열로 표시하고, 절대 추측하거나 지어내지 말 것.

추출 대상:
- option_texts: 옵션 목록 (문자열 배열 또는 빈 배열)

페이지 텍스트:
{page_text}

JSON만 반환:"""


async def recover_missing_fields(
    raw: RawProduct,
    page_text: str,
    cfg,
) -> None:
    """페이지 텍스트에서 옵션 목록을 LLM으로 보조 추출한다.

    제약:
    - name / consumer_price / sales_price / image_url / source_url은
      절대 수정하지 않는다 (결정적 추출만 허용).
    - LLM이 찾은 힌트는 raw_evidence["recovery_hint"]에만 기록한다.
    - option_texts는 option1/option2 정규화 보조 목적으로만 사용한다.
    """
    if not cfg.anthropic_api_key:
        return
    if not page_text or len(page_text.strip()) < 30:
        return

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        message = await client.messages.create(
            model=cfg.llm_model,
            max_tokens=256,
            timeout=cfg.llm_timeout,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(page_text=page_text[:3000]),
            }],
        )
        raw_text = message.content[0].text.strip()

        if "```" in raw_text:
            parts = raw_text.split("```")
            raw_text = parts[1] if len(parts) > 1 else raw_text
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        data: dict[str, Any] = json.loads(raw_text.strip())

        hint: dict[str, Any] = {"method": "page_text_analysis"}

        # option_texts는 AI 정규화에서 option1/option2 보조용으로만 사용한다.
        if not raw.option_texts and isinstance(data.get("option_texts"), list):
            extracted = [str(o) for o in data["option_texts"] if o]
            if extracted:
                raw.option_texts = extracted
                hint["option_texts"] = extracted

        if hint.keys() - {"method"}:
            raw.raw_evidence["recovery_hint"] = hint
            _LOG.info("페이지 텍스트 보조 분석 완료: %s", raw.source_url)
        else:
            _LOG.info("보조 분석 결과 없음: %s", raw.source_url)

    except Exception as exc:
        _LOG.warning("페이지 텍스트 보조 분석 실패 (%s): %s", type(exc).__name__, exc)
