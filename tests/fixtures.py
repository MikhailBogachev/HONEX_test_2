"""Shared fixtures for Avito parser tests."""

from __future__ import annotations

import pytest

from src.models import Listing, Seller, SellerType, EnrichmentStatus, DataOrigin


# ---------------------------------------------------------------------------
# Listing fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def make_listing():
    """Factory fixture to create Listing objects with defaults."""
    from datetime import datetime

    def _make(
        source_article: str = "243502B010",
        avito_id: str = "1234567890",
        url: str = "https://www.avito.ru/moskva_i_mo/zapchasti/test_1234567890",
        title: str = "Фазорегулятор Solaris Creta 1.6 243502B010 Новое",
        price: float = 5000.0,
        condition: str = "Новое",
        city: str = "Москва",
        address: str = "Москва, ул. Тестовая, 1",
        description: str = "",
        params: list[str] | None = None,
        seller: Seller | None = None,
        **kwargs,
    ) -> Listing:
        return Listing(
            source_article=source_article,
            avito_id=avito_id,
            url=url,
            title=title,
            price=price,
            condition=condition,
            city=city,
            address=address,
            description=description,
            params=params or [],
            seller=seller,
            data_origin=DataOrigin.LIVE,
            scraped_at=datetime(2026, 8, 12, 12, 0, 0),
            **kwargs,
        )

    return _make


@pytest.fixture
def make_seller():
    """Factory fixture to create Seller objects."""
    def _make(
        seller_id: str = "seller_001",
        seller_profile_url: str = "https://www.avito.ru/user/seller_001/profile",
        seller_type: SellerType = SellerType.PRIVATE,
        seller_name: str = "Иван И.",
        seller_rating: float = 4.8,
        seller_reviews: int = 25,
        seller_items_count: int = 10,
        seller_since: str = "2020-03-12",
        seller_since_raw: str = "12 марта 2020",
        seller_enrichment_status: EnrichmentStatus = EnrichmentStatus.OK,
        seller_enrichment_error: str | None = None,
        **kwargs,
    ) -> Seller:
        return Seller(
            seller_id=seller_id,
            seller_profile_url=seller_profile_url,
            seller_type=seller_type,
            seller_name=seller_name,
            seller_rating=seller_rating,
            seller_reviews=seller_reviews,
            seller_items_count=seller_items_count,
            seller_since=seller_since,
            seller_since_raw=seller_since_raw,
            seller_enrichment_status=seller_enrichment_status,
            seller_enrichment_error=seller_enrichment_error,
            **kwargs,
        )

    return _make
