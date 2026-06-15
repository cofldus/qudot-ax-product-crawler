"""app/crawlers/naver_store.py 헬퍼 함수 단위 테스트."""
from __future__ import annotations

import asyncio
import unittest.mock as mock

import pytest

from app.crawlers.naver_store import (
    NaverStoreCrawler,
    _analyze_price_candidates,
    _extract_product_id,
    _is_invalid_product_name,
    _parse_price,
    _response_matches_product_id,
    _validate_and_fix_prices,
)
from app.schemas.raw_product import RawProduct


# ── _extract_product_id ──────────────────────────────────────────────

class TestExtractProductId:
    def test_normal_url(self):
        assert _extract_product_id("https://brand.naver.com/kefii/products/9285518735") == "9285518735"

    def test_trailing_slash(self):
        assert _extract_product_id("https://brand.naver.com/kefii/products/9285518735/") == "9285518735"

    def test_with_query_string(self):
        assert _extract_product_id(
            "https://brand.naver.com/kefii/products/9285518735?from=search"
        ) == "9285518735"

    def test_smartstore_url(self):
        assert _extract_product_id(
            "https://smartstore.naver.com/phytonutri/products/1234567890"
        ) == "1234567890"

    def test_non_numeric_segment(self):
        assert _extract_product_id("https://brand.naver.com/kefii/products/abc123") is None

    def test_no_products_segment(self):
        assert _extract_product_id("https://brand.naver.com/kefii") is None

    def test_empty_string(self):
        assert _extract_product_id("") is None


# ── _is_invalid_product_name ─────────────────────────────────────────

class TestIsInvalidProductName:
    def test_not_exist(self):
        assert _is_invalid_product_name("상품이 존재하지 않습니다.") is True

    def test_not_exist_no_period(self):
        assert _is_invalid_product_name("상품이 존재하지 않습니다") is True

    def test_page_not_found(self):
        assert _is_invalid_product_name("페이지를 찾을 수 없습니다.") is True

    def test_temp_unavailable(self):
        assert _is_invalid_product_name("일시적으로 상품 정보를 불러올 수 없습니다.") is True

    def test_discontinued(self):
        assert _is_invalid_product_name("판매중지된 상품") is True

    def test_no_access(self):
        assert _is_invalid_product_name("접근할 수 없는 상품") is True

    def test_valid_name(self):
        assert _is_invalid_product_name("케피 버블클렌저 200ml") is False

    def test_partial_match_not_invalid(self):
        # 부분 일치는 무효로 처리하지 않는다
        assert _is_invalid_product_name("이 상품이 존재하지 않습니다 할인중") is False


# ── _parse_price ─────────────────────────────────────────────────────

class TestParsePrice:
    def test_basic(self):
        assert _parse_price("21,500원") == 21500

    def test_no_comma(self):
        assert _parse_price("5000원") == 5000

    def test_with_spaces(self):
        assert _parse_price("21,500 원") == 21500

    def test_percent_ignored(self):
        # "49%" 앞부분은 가격으로 보지 않아야 한다
        assert _parse_price("49%") is None

    def test_mixed_text_takes_first_price(self):
        # 여러 가격이 있으면 첫 번째만 반환
        result = _parse_price("49% 42,500원 21,500원")
        assert result == 42500

    def test_below_range(self):
        assert _parse_price("500원") is None

    def test_above_range(self):
        assert _parse_price("20,000,000원") is None

    def test_none_input(self):
        assert _parse_price(None) is None

    def test_empty_string(self):
        assert _parse_price("") is None


# ── _validate_and_fix_prices ──────────────────────────────────────────

class TestValidateAndFixPrices:
    def test_equal_prices_clears_consumer(self):
        raw = RawProduct(source_url="https://example.com")
        raw.sales_price = 10000
        raw.consumer_price = 10000
        _validate_and_fix_prices(raw)
        assert raw.consumer_price is None
        assert "consumer_price" in raw.field_errors

    def test_sales_greater_than_consumer_clears_consumer(self):
        raw = RawProduct(source_url="https://example.com")
        raw.sales_price = 15000
        raw.consumer_price = 10000
        _validate_and_fix_prices(raw)
        assert raw.consumer_price is None
        assert "price_consistency" in raw.field_errors

    def test_normal_discount_unchanged(self):
        raw = RawProduct(source_url="https://example.com")
        raw.sales_price = 8000
        raw.consumer_price = 10000
        _validate_and_fix_prices(raw)
        assert raw.consumer_price == 10000
        assert raw.sales_price == 8000

    def test_only_sales_price_unchanged(self):
        raw = RawProduct(source_url="https://example.com")
        raw.sales_price = 10000
        _validate_and_fix_prices(raw)
        assert raw.sales_price == 10000
        assert raw.consumer_price is None


# ── _response_matches_product_id ─────────────────────────────────────

class TestResponseMatchesProductId:
    def test_productNo_matches(self):
        assert _response_matches_product_id({"productNo": 12345}, "12345") is True

    def test_channelProductNo_matches(self):
        assert _response_matches_product_id({"channelProductNo": "9999"}, "9999") is True

    def test_id_matches(self):
        assert _response_matches_product_id({"id": 777}, "777") is True

    def test_no_matching_key(self):
        assert _response_matches_product_id({"name": "테스트"}, "12345") is False

    def test_wrong_value(self):
        assert _response_matches_product_id({"productNo": 99999}, "12345") is False

    def test_non_dict_returns_false(self):
        assert _response_matches_product_id([], "12345") is False  # type: ignore[arg-type]


# ── _analyze_price_candidates ─────────────────────────────────────────

class TestAnalyzePriceCandidates:
    def test_배송비_context_excluded(self):
        text = "판매가 15,000원\n배송비 3,000원"
        candidates, excluded, _, _, _ = _analyze_price_candidates(text)
        assert any(e["value"] == 3000 and "배송비" in e["excluded_reason"] for e in excluded)
        assert not any(c["value"] == 3000 for c in candidates)

    def test_쿠폰_context_excluded(self):
        text = "쿠폰 2,000원 할인 가능  판매가 12,000원"
        _, excluded, _, _, _ = _analyze_price_candidates(text)
        assert any("쿠폰" in e["excluded_reason"] for e in excluded)

    def test_적립_context_excluded(self):
        text = "적립 500원  판매가 8,000원"
        _, excluded, _, _, _ = _analyze_price_candidates(text)
        assert any("적립" in e["excluded_reason"] for e in excluded)

    def test_포인트_context_excluded(self):
        # "포인트"와 "적립" 두 키워드가 모두 있을 때 1,000원이 제외되는지 검증
        # excluded_reason은 먼저 매칭된 키워드로 설정되므로 특정 키워드 대신 value로 검증한다
        text = "포인트 1,000원 적립  판매가 20,000원"
        _, excluded, _, _, _ = _analyze_price_candidates(text)
        assert any(e["value"] == 1000 for e in excluded)

    def test_single_price_to_sales_only(self):
        text = "이 상품의 가격은 5,000원 입니다"
        candidates, _, consumer, sales, _ = _analyze_price_candidates(text)
        assert sales == 5000
        assert consumer is None

    def test_candidates_structure_fields(self):
        text = "정가 20,000원  판매가 15,000원"
        candidates, excluded, _, _, _ = _analyze_price_candidates(text)
        assert isinstance(candidates, list)
        assert isinstance(excluded, list)
        for c in candidates:
            assert "price_text" in c
            assert "value" in c
            assert "context" in c
            assert "role_hint" in c

    def test_no_prices_returns_empty(self):
        text = "아무 가격 정보가 없는 텍스트"
        candidates, excluded, consumer, sales, _ = _analyze_price_candidates(text)
        assert candidates == []
        assert sales is None
        assert consumer is None


# ── _text_price_fallback 통합 ─────────────────────────────────────────

class TestTextPriceFallbackEvidence:
    def test_candidates_in_raw_evidence(self):
        """price_fallback raw_evidence에 candidates·excluded_candidates 배열이 존재한다."""
        async def _inner():
            raw = RawProduct(source_url="https://example.com")
            page = mock.MagicMock()
            page.evaluate = mock.AsyncMock(return_value="판매가 15,000원  배송비 3,000원")
            await NaverStoreCrawler._text_price_fallback(None, raw, page)  # type: ignore[arg-type]
            return raw

        raw = asyncio.run(_inner())
        pf = raw.raw_evidence.get("price_fallback", {})
        assert "candidates" in pf
        assert isinstance(pf["candidates"], list)
        assert "excluded_candidates" in pf
        assert isinstance(pf["excluded_candidates"], list)

    def test_no_valid_price_sets_sales_field_error(self):
        """유효 가격이 없으면 field_errors['sales_price']가 설정된다."""
        async def _inner():
            raw = RawProduct(source_url="https://example.com")
            page = mock.MagicMock()
            # 모두 제외 키워드 context 내 가격
            page.evaluate = mock.AsyncMock(
                return_value="배송비 3,000원  쿠폰 2,000원  포인트 500원"
            )
            await NaverStoreCrawler._text_price_fallback(None, raw, page)  # type: ignore[arg-type]
            return raw

        raw = asyncio.run(_inner())
        assert "sales_price" in raw.field_errors
