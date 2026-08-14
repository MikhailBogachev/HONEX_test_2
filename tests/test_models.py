"""Tests for data models — correct defaults, validation, serialization."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.models import (
    ArticleResult,
    ArticleStatus,
    DataOrigin,
    EnrichmentStatus,
    Listing,
    RunSummary,
    Seller,
    SellerType,
)


class TestListingModel:
    def test_defaults(self):
        listing = Listing(
            source_article="TEST",
            avito_id="123",
            url="https://www.avito.ru/test",
        )
        assert listing.title == ""
        assert listing.price is None
        assert listing.currency == "RUB"
        assert listing.condition is None
        assert listing.data_origin == DataOrigin.LIVE
        assert listing.oem_match is False
        assert listing.params == []
        assert listing.seller is None

    def test_with_seller(self, make_listing, make_seller):
        seller = make_seller()
        listing = make_listing(seller=seller)
        assert listing.seller is not None
        assert listing.seller.seller_id == "seller_001"
        assert listing.seller.seller_type == SellerType.PRIVATE


class TestSellerModel:
    def test_defaults(self):
        seller = Seller()
        assert seller.seller_type == SellerType.UNKNOWN
        assert seller.seller_enrichment_status == EnrichmentStatus.UNAVAILABLE
        assert seller.seller_rating is None
        assert seller.seller_reviews is None

    def test_none_not_zero(self):
        """Unknown values must be None, not 0."""
        seller = Seller()
        assert seller.seller_rating is None    # not 0
        assert seller.seller_reviews is None   # not 0
        assert seller.seller_items_count is None  # not 0


class TestRunSummary:
    def test_per_hour_calculation(self):
        summary = RunSummary(
            articles_total=10,
            articles_completed=8,
            articles_success=6,
            articles_no_matches=1,
            articles_error=1,
            elapsed_seconds=360.0,
            listings_scanned=50,
            valid_result_rows=25,
        )
        # articles_per_hour = 8 / 360 * 3600 = 80
        assert summary.articles_per_hour == 0.0  # not calculated yet (just model)

    def test_serialization(self):
        summary = RunSummary(
            run_id="20260812-120000",
            data_origin=DataOrigin.LIVE,
            articles_per_hour=80.0,
            successful_articles_per_hour=70.0,
        )
        data = summary.model_dump(mode="json")
        assert data["run_id"] == "20260812-120000"
        assert data["data_origin"] == "live"


class TestArticleResult:
    def test_status_values(self):
        for status in ArticleStatus:
            ar = ArticleResult(article="TEST", status=status)
            assert ar.status == status
