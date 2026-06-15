"""app/ai/normalizer.py 단위 테스트.

AI 호출 없이 결정적 필드 변환과 실패 경로를 검증한다.
"""
from __future__ import annotations

import asyncio
import unittest.mock as mock

import pytest

from app.ai.normalizer import _build_partner_product, normalize
from app.schemas.raw_product import RawProduct


def _raw(
    *,
    name: str | None = "테스트 상품",
    sales_price: int | None = 15000,
    consumer_price: int | None = 20000,
    image_url: str | None = "https://example.com/img.jpg",
    source_url: str = "https://example.com/products/123",
) -> RawProduct:
    raw = RawProduct(source_url=source_url)
    raw.name = name
    raw.sales_price = sales_price
    raw.consumer_price = consumer_price
    if image_url:
        raw.image_urls = [image_url]
    return raw


class TestBuildPartnerProduct:
    def test_deterministic_fields_from_raw(self):
        raw = _raw()
        pp = _build_partner_product(raw, None, [], {})
        assert pp.name == "테스트 상품"
        assert pp.sales_price == 15000
        assert pp.consumer_price == 20000
        assert pp.image_url == "https://example.com/img.jpg"
        assert pp.source_url == "https://example.com/products/123"
        assert pp.lowest_price is None

    def test_discount_rate_computed_automatically(self):
        raw = _raw(consumer_price=20000, sales_price=15000)
        pp = _build_partner_product(raw, None, [], {})
        assert pp.discount_rate == 25.0

    def test_lowest_price_always_null_with_missing_reason(self):
        raw = _raw()
        pp = _build_partner_product(raw, None, [], {})
        assert pp.lowest_price is None
        assert "lowest_price" in pp.missing_reasons

    def test_ai_data_fills_allowed_fields(self):
        raw = _raw()
        ai_data = {
            "brand_name": "테스트 브랜드",
            "usp": "최고의 상품입니다",
            "hashtags": ["유아", "건강"],
            "category_group": ["유아 건강"],
        }
        pp = _build_partner_product(raw, ai_data, ["brand_name", "usp"], {})
        assert pp.brand_name == "테스트 브랜드"
        assert pp.usp == "최고의 상품입니다"
        assert "유아" in pp.hashtags

    def test_ai_data_invalid_category_goes_to_missing(self):
        raw = _raw()
        ai_data = {"category_group": ["존재하지 않는 카테고리"]}
        pp = _build_partner_product(raw, ai_data, [], {})
        assert pp.category_group == []
        assert "category_group" in pp.missing_reasons

    def test_price_reversal_fallback_nulls_consumer(self):
        """sales > consumer이면 consumer_price를 null로 강등하고 실패하지 않는다."""
        raw = _raw(sales_price=20000, consumer_price=10000)
        # _validate_and_fix_prices 미호출이므로 raw 자체는 역전 상태
        pp = _build_partner_product(raw, None, [], {})
        # PartnerProductCreateInput validator가 역전을 차단 → consumer null fallback
        assert pp.consumer_price is None
        assert pp.sales_price == 20000

    def test_ai_forbidden_fields_not_in_output(self):
        """AI 응답에 금지 필드가 있어도 partner_product에 반영되지 않는다."""
        raw = _raw(sales_price=10000, consumer_price=None)
        ai_data = {
            "brand_name": "OK 브랜드",
            "sales_price": 99999,   # 금지 — 무시해야 함
            "name": "AI가 만든 이름",  # 금지 — 무시해야 함
        }
        # _build_partner_product는 ai_data를 그대로 받으므로
        # normalize()에서 금지 필드를 제거한 후 호출하는 흐름을 모사
        from app.ai.prompts import AI_FORBIDDEN_FIELDS
        for f in AI_FORBIDDEN_FIELDS:
            ai_data.pop(f, None)
        pp = _build_partner_product(raw, ai_data, ["brand_name"], {})
        assert pp.brand_name == "OK 브랜드"
        assert pp.sales_price == 10000   # raw 값 유지
        assert pp.name == "테스트 상품"  # raw 값 유지


class TestNormalize:
    def test_no_api_key_returns_raw_only(self):
        """API 키 없으면 raw_only 반환, 결정적 필드는 유지된다."""
        raw = _raw()

        class _FakeCfg:
            anthropic_api_key = ""
            llm_model = "claude-sonnet-4-6"
            llm_timeout = 30
            detail_text_limit = 2000

        pp, status = asyncio.run(normalize(raw, _FakeCfg()))
        assert status == "raw_only"
        assert pp.name == "테스트 상품"
        assert pp.sales_price == 15000
        assert pp.consumer_price == 20000
        assert "brand_name" in pp.missing_reasons
        assert "ANTHROPIC_API_KEY" in pp.missing_reasons["brand_name"]

    def test_ai_failure_preserves_deterministic_fields(self):
        """AI 호출 실패해도 가격·상품명은 raw 값으로 유지된다."""
        raw = _raw()

        class _FakeCfg:
            anthropic_api_key = "sk-invalid-key"
            llm_model = "claude-sonnet-4-6"
            llm_timeout = 5
            detail_text_limit = 2000

        mock_anthropic = mock.MagicMock()
        mock_client = mock.AsyncMock()
        mock_client.messages.create.side_effect = RuntimeError("연결 실패")
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            pp, status = asyncio.run(normalize(raw, _FakeCfg()))

        assert status == "raw_only"
        assert pp.name == "테스트 상품"
        assert pp.sales_price == 15000
        assert pp.consumer_price == 20000
        assert "brand_name" in pp.missing_reasons
        assert "AI 호출 실패" in pp.missing_reasons.get("brand_name", "")

    def test_ai_success_fills_ai_fields(self):
        """AI 정상 응답 시 ai_fields가 채워지고 status=normalized."""
        raw = _raw()

        class _FakeCfg:
            anthropic_api_key = "sk-test"
            llm_model = "claude-sonnet-4-6"
            llm_timeout = 30
            detail_text_limit = 2000

        ai_response_json = (
            '{"brand_name": "테스트 브랜드", "usp": "최고 품질", '
            '"hashtags": ["유아", "건강"], "category_group": ["유아 건강"]}'
        )

        fake_content = mock.MagicMock()
        fake_content.text = ai_response_json
        fake_message = mock.MagicMock()
        fake_message.content = [fake_content]

        mock_anthropic = mock.MagicMock()
        mock_client = mock.AsyncMock()
        mock_client.messages.create = mock.AsyncMock(return_value=fake_message)
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        # 로컬 'import anthropic' 가 mock을 받도록 sys.modules를 패치
        with mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            pp, status = asyncio.run(normalize(raw, _FakeCfg()))

        assert status == "normalized"
        assert pp.brand_name == "테스트 브랜드"
        assert pp.usp == "최고 품질"
        assert "brand_name" in pp.ai_fields
        # 결정적 필드 유지
        assert pp.sales_price == 15000
        assert pp.consumer_price == 20000

    def test_ai_cannot_override_price(self):
        """AI 응답에 sales_price가 있어도 raw 가격이 유지된다."""
        raw = _raw(sales_price=10000, consumer_price=None)

        class _FakeCfg:
            anthropic_api_key = "sk-test"
            llm_model = "claude-sonnet-4-6"
            llm_timeout = 30
            detail_text_limit = 2000

        # AI가 금지 필드를 반환하는 시나리오
        ai_response_json = '{"brand_name": "AI 브랜드", "sales_price": 99999}'

        fake_content = mock.MagicMock()
        fake_content.text = ai_response_json
        fake_message = mock.MagicMock()
        fake_message.content = [fake_content]

        mock_anthropic = mock.MagicMock()
        mock_client = mock.AsyncMock()
        mock_client.messages.create = mock.AsyncMock(return_value=fake_message)
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with mock.patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            pp, status = asyncio.run(normalize(raw, _FakeCfg()))

        assert pp.sales_price == 10000  # raw 값
        assert pp.brand_name == "AI 브랜드"  # AI 허용 필드

    def test_detail_text_missing_sets_hashtags_usp_missing(self):
        """detail_text 추출 실패 시 hashtags·usp missing_reasons에 사유가 기록된다."""
        raw = _raw()
        raw.field_errors["detail_text"] = "이미지 기반 상세페이지"

        class _FakeCfg:
            anthropic_api_key = ""
            llm_model = "claude-sonnet-4-6"
            llm_timeout = 30
            detail_text_limit = 2000

        pp, status = asyncio.run(normalize(raw, _FakeCfg()))
        assert "hashtags" in pp.missing_reasons
        assert "usp" in pp.missing_reasons
