"""PartnerProductCreateInput 스키마 검증 단위 테스트."""
from __future__ import annotations

import pytest

from app.schemas.partner_product import CategoryGroup, PartnerProductCreateInput


class TestPartnerProductValidation:
    def test_basic_creation(self):
        pp = PartnerProductCreateInput(
            name="테스트 상품",
            source_url="https://example.com",
        )
        assert pp.name == "테스트 상품"
        assert pp.source_url == "https://example.com"
        assert pp.sales_price is None
        assert pp.discount_rate is None

    def test_discount_rate_auto_computed(self):
        pp = PartnerProductCreateInput(
            name="할인 상품",
            source_url="https://example.com",
            consumer_price=20000,
            sales_price=15000,
        )
        assert pp.discount_rate == 25.0

    def test_sales_price_equals_consumer_no_discount(self):
        pp = PartnerProductCreateInput(
            name="동일가 상품",
            source_url="https://example.com",
            consumer_price=10000,
            sales_price=10000,
        )
        # consumer == sales → discount_rate = 0.0
        assert pp.discount_rate == 0.0

    def test_sales_price_exceeds_consumer_price_raises(self):
        """sales_price > consumer_price이면 ValidationError."""
        with pytest.raises(Exception, match="consumer_price"):
            PartnerProductCreateInput(
                name="역전 상품",
                source_url="https://example.com",
                consumer_price=10000,
                sales_price=15000,
            )

    def test_ai_fields_forbidden_field_raises(self):
        """ai_fields에 결정적 필드(sales_price)가 들어가면 ValidationError."""
        with pytest.raises(Exception, match="허용되지 않은"):
            PartnerProductCreateInput(
                name="테스트",
                source_url="https://example.com",
                ai_fields=["sales_price"],
            )

    def test_ai_fields_forbidden_name_raises(self):
        """ai_fields에 name이 들어가면 ValidationError."""
        with pytest.raises(Exception, match="허용되지 않은"):
            PartnerProductCreateInput(
                name="테스트",
                source_url="https://example.com",
                ai_fields=["name"],
            )

    def test_ai_fields_allowed_passes(self):
        pp = PartnerProductCreateInput(
            name="테스트",
            source_url="https://example.com",
            brand_name="테스트 브랜드",
            ai_fields=["brand_name", "usp", "hashtags"],
        )
        assert "brand_name" in pp.ai_fields

    def test_name_empty_string_raises(self):
        """name이 빈 문자열이면 ValidationError (min_length=1)."""
        with pytest.raises(Exception):
            PartnerProductCreateInput(
                name="",
                source_url="https://example.com",
            )

    def test_category_group_enum_validation(self):
        pp = PartnerProductCreateInput(
            name="유아 상품",
            source_url="https://example.com",
            category_group=[CategoryGroup.INFANT_HEALTH],
        )
        assert CategoryGroup.INFANT_HEALTH in pp.category_group

    def test_usp_max_length(self):
        long_usp = "A" * 301
        with pytest.raises(Exception):
            PartnerProductCreateInput(
                name="테스트",
                source_url="https://example.com",
                usp=long_usp,
            )

    def test_hashtags_max_10(self):
        with pytest.raises(Exception):
            PartnerProductCreateInput(
                name="테스트",
                source_url="https://example.com",
                hashtags=["tag"] * 11,
            )
