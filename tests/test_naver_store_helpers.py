"""app/crawlers/naver_store.py 헬퍼 함수 단위 테스트."""
from __future__ import annotations

import pytest

from app.crawlers.naver_store import (
    _extract_product_id,
    _is_invalid_product_name,
    _parse_price,
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
