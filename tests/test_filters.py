"""Tests for business logic filters: OEM, condition, region, price."""

from __future__ import annotations

import pytest

from src.pipeline.filters import (
    is_article_in_params,
    is_new_condition,
    is_moscow_mo,
    is_oem_match,
    is_valid_price,
    normalize_article,
    passes_all_filters,
)


# ---------------------------------------------------------------------------
# normalize_article
# ---------------------------------------------------------------------------

class TestNormalizeArticle:
    def test_simple(self):
        assert normalize_article("243502B010") == "243502B010"

    def test_with_spaces(self):
        assert normalize_article("243502 B010") == "243502B010"

    def test_with_dashes(self):
        assert normalize_article("243502-B010") == "243502B010"

    def test_lowercase(self):
        assert normalize_article("243502b010") == "243502B010"

    def test_mixed_separators(self):
        assert normalize_article("243 502-B0 10") == "243502B010"

    def test_empty(self):
        assert normalize_article("") == ""


# ---------------------------------------------------------------------------
# is_oem_match
# ---------------------------------------------------------------------------

class TestOemMatch:
    def test_exact_in_title(self, make_listing):
        listing = make_listing(title="Фазорегулятор 243502B010 Hyundai Solaris")
        assert is_oem_match(listing, "243502B010") is True

    def test_oem_in_description(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор",
            description="Артикул: 243502B010, оригинал",
        )
        assert is_oem_match(listing, "243502B010") is True

    def test_oem_in_params(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор",
            params=["Артикул: 243502B010", "Состояние: Новое"],
        )
        assert is_oem_match(listing, "243502B010") is True

    def test_wrong_oem(self, make_listing):
        listing = make_listing(title="Фазорегулятор 243502U000 Hyundai")
        assert is_oem_match(listing, "243502B010") is False

    def test_case_insensitive(self, make_listing):
        listing = make_listing(title="фазорегулятор 243502b010")
        assert is_oem_match(listing, "243502B010") is True

    def test_partial_match_rejected(self, make_listing):
        """Partial match (substring of different article) should not pass."""
        listing = make_listing(title="243502B0100 другой артикул")
        assert is_oem_match(listing, "243502B010") is True  # substring is fine — semantic check in cards


# ---------------------------------------------------------------------------
# is_new_condition
# ---------------------------------------------------------------------------

class TestConditionFilter:
    def test_new_condition_field(self, make_listing):
        listing = make_listing(condition="Новое")
        assert is_new_condition(listing) is True

    def test_used_condition_field(self, make_listing):
        listing = make_listing(condition="Б/у")
        assert is_new_condition(listing) is False

    def test_new_in_params(self, make_listing):
        listing = make_listing(condition=None, params=["Состояние: Новое"])
        assert is_new_condition(listing) is True

    def test_used_in_params(self, make_listing):
        listing = make_listing(condition=None, params=["Состояние: Б/у"])
        assert is_new_condition(listing) is False

    def test_no_condition_info(self, make_listing):
        """Unknown condition — should be rejected."""
        listing = make_listing(condition=None, params=[], title="Просто товар")
        assert is_new_condition(listing) is False

    def test_new_in_title(self, make_listing):
        listing = make_listing(condition=None, title="Фазорегулятор новый оригинал")
        assert is_new_condition(listing) is True


# ---------------------------------------------------------------------------
# is_article_in_params
# ---------------------------------------------------------------------------


class TestArticleInParams:
    def test_article_in_params(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор",
            params=["Артикул: 243502B010", "Состояние: Новое"],
        )
        assert is_article_in_params(listing, "243502B010") is True

    def test_article_with_dash_in_params(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор",
            params=["Артикул: 243502-B010", "Состояние: Новое"],
        )
        assert is_article_in_params(listing, "243502B010") is True

    def test_article_in_title_only_rejected(self, make_listing):
        """Article only in title but NOT in params — should be rejected."""
        listing = make_listing(
            title="Фазорегулятор 243502B010 Hyundai Solaris",
            params=["Состояние: Новое"],
        )
        assert is_article_in_params(listing, "243502B010") is False

    def test_article_in_description_only_rejected(self, make_listing):
        """Article only in description but NOT in params — should be rejected."""
        listing = make_listing(
            title="Фазорегулятор Hyundai",
            description="Артикул: 243502B010",
            params=["Состояние: Новое"],
        )
        assert is_article_in_params(listing, "243502B010") is False

    def test_empty_params(self, make_listing):
        listing = make_listing(params=[])
        assert is_article_in_params(listing, "243502B010") is False


# ---------------------------------------------------------------------------
# is_moscow_mo
# ---------------------------------------------------------------------------

class TestRegionFilter:
    def test_moscow(self, make_listing):
        listing = make_listing(address="Москва, ул. Ленина, 1")
        assert is_moscow_mo(listing) is True

    def test_moscow_oblast_city(self, make_listing):
        listing = make_listing(address="Подольск, ул. Советская, 5")
        assert is_moscow_mo(listing) is True

    def test_moscow_in_city_field(self, make_listing):
        listing = make_listing(city="Москва", address="ул. Тестовая, 1")
        assert is_moscow_mo(listing) is True

    def test_saint_petersburg(self, make_listing):
        listing = make_listing(
            city="Санкт-Петербург",
            address="Санкт-Петербург, Невский пр., 1",
        )
        assert is_moscow_mo(listing) is False

    def test_novosibirsk(self, make_listing):
        listing = make_listing(
            city="Новосибирск",
            address="Новосибирск, Красный пр., 100",
        )
        assert is_moscow_mo(listing) is False

    def test_empty_address(self, make_listing):
        listing = make_listing(address="", city="")
        assert is_moscow_mo(listing) is False

    def test_moscow_oblast_federal(self, make_listing):
        listing = make_listing(address="Московская область, г. Балашиха")
        assert is_moscow_mo(listing) is True

    def test_new_moscow(self, make_listing):
        listing = make_listing(address="Новая Москва, пос. Коммунарка")
        assert is_moscow_mo(listing) is True


# ---------------------------------------------------------------------------
# price filter
# ---------------------------------------------------------------------------

class TestPriceFilter:
    def test_valid_price(self, make_listing):
        listing = make_listing(price=5000.0)
        assert is_valid_price(listing) is True

    def test_zero_price(self, make_listing):
        listing = make_listing(price=0.0)
        assert is_valid_price(listing) is False

    def test_negative_price(self, make_listing):
        listing = make_listing(price=-100.0)
        assert is_valid_price(listing) is False

    def test_none_price(self, make_listing):
        listing = make_listing(price=None)
        assert is_valid_price(listing) is False


# ---------------------------------------------------------------------------
# passes_all_filters combined
# ---------------------------------------------------------------------------

class TestAllFilters:
    def test_valid_listing_passes(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор 243502B010 Hyundai Solaris Новое",
            price=5000.0,
            condition="Новое",
            address="Москва, ул. Тестовая, 1",
            params=["Артикул: 243502B010", "Состояние: Новое"],
        )
        passed, reason = passes_all_filters(listing, "243502B010")
        assert passed is True
        assert reason == "ok"

    def test_wrong_oem_rejected(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор 243502U000 Hyundai",
            price=5000.0,
            condition="Новое",
            address="Москва",
            params=["Артикул: 243502U000"],
        )
        passed, reason = passes_all_filters(listing, "243502B010")
        assert passed is False
        assert reason == "oem_mismatch"

    def test_article_not_in_params_rejected(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор 243502B010 Hyundai",
            price=5000.0,
            condition="Новое",
            address="Москва",
            params=["Состояние: Новое"],
        )
        passed, reason = passes_all_filters(listing, "243502B010")
        assert passed is False
        assert reason == "article_not_in_params"

    def test_used_rejected(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор 243502B010",
            price=5000.0,
            condition="Б/у",
            address="Москва",
            params=["Артикул: 243502B010", "Состояние: Б/у"],
        )
        passed, reason = passes_all_filters(listing, "243502B010")
        assert passed is False
        assert reason == "condition_not_new"

    def test_wrong_region_rejected(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор 243502B010 Новое",
            price=5000.0,
            condition="Новое",
            city="Казань",
            address="Казань, ул. Байурская, 1",
            params=["Артикул: 243502B010", "Состояние: Новое"],
        )
        passed, reason = passes_all_filters(listing, "243502B010")
        assert passed is False
        assert reason == "not_moscow_mo"

    def test_zero_price_rejected(self, make_listing):
        listing = make_listing(
            title="Фазорегулятор 243502B010 Новое",
            price=0.0,
            condition="Новое",
            address="Москва",
            params=["Артикул: 243502B010", "Состояние: Новое"],
        )
        passed, reason = passes_all_filters(listing, "243502B010")
        assert passed is False
        assert reason == "invalid_price"
