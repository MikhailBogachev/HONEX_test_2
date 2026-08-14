"""Tests for deduplication."""

from __future__ import annotations

import pytest

from src.output.dedup import Deduplicator


class TestDeduplicator:
    def test_first_item_not_duplicate(self, make_listing):
        dedup = Deduplicator()
        listing = make_listing(source_article="243502B010", avito_id="123")
        assert dedup.is_duplicate(listing) is False

    def test_same_item_is_duplicate(self, make_listing):
        dedup = Deduplicator()
        listing1 = make_listing(source_article="243502B010", avito_id="123")
        listing2 = make_listing(source_article="243502B010", avito_id="123")
        dedup.is_duplicate(listing1)  # register
        assert dedup.is_duplicate(listing2) is True

    def test_different_article_not_duplicate(self, make_listing):
        dedup = Deduplicator()
        listing1 = make_listing(source_article="243502B010", avito_id="123")
        listing2 = make_listing(source_article="243502U000", avito_id="123")
        dedup.is_duplicate(listing1)
        assert dedup.is_duplicate(listing2) is False

    def test_different_id_not_duplicate(self, make_listing):
        dedup = Deduplicator()
        listing1 = make_listing(source_article="243502B010", avito_id="123")
        listing2 = make_listing(source_article="243502B010", avito_id="456")
        dedup.is_duplicate(listing1)
        assert dedup.is_duplicate(listing2) is False

    def test_reset_clears_seen(self, make_listing):
        dedup = Deduplicator()
        listing = make_listing(source_article="243502B010", avito_id="123")
        dedup.is_duplicate(listing)
        dedup.reset()
        assert dedup.is_duplicate(listing) is False

    def test_seen_count(self, make_listing):
        dedup = Deduplicator()
        assert dedup.seen_count == 0
        dedup.is_duplicate(make_listing(avito_id="1"))
        assert dedup.seen_count == 1
        dedup.is_duplicate(make_listing(avito_id="2"))
        assert dedup.seen_count == 2
