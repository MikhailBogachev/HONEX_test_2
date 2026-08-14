"""Tests for seller cache with concurrent-safe locking."""

from __future__ import annotations

import asyncio

import pytest

from src.models import Seller, SellerType, EnrichmentStatus
from src.output.seller_cache import SellerCache


@pytest.mark.asyncio
async def test_cache_miss_then_hit():
    """First call fetches, second call returns cached."""
    cache = SellerCache()
    fetch_count = 0

    async def fake_fetch(key: str) -> Seller:
        nonlocal fetch_count
        fetch_count += 1
        return Seller(seller_id=key, seller_name="Test Seller")

    seller1 = await cache.get_or_fetch("s1", fake_fetch)
    seller2 = await cache.get_or_fetch("s1", fake_fetch)

    assert seller1.seller_id == "s1"
    assert seller2.seller_id == "s1"
    assert fetch_count == 1  # fetched only once


@pytest.mark.asyncio
async def test_cache_different_keys():
    """Different keys produce separate cache entries."""
    cache = SellerCache()

    async def fake_fetch(key: str) -> Seller:
        return Seller(seller_id=key, seller_name=f"Seller {key}")

    s1 = await cache.get_or_fetch("a", fake_fetch)
    s2 = await cache.get_or_fetch("b", fake_fetch)

    assert s1.seller_name == "Seller a"
    assert s2.seller_name == "Seller b"
    assert cache.size == 2


@pytest.mark.asyncio
async def test_concurrent_inflight_requests():
    """Concurrent requests for the same key should only fetch once."""
    cache = SellerCache()
    fetch_count = 0

    async def slow_fetch(key: str) -> Seller:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.05)  # simulate network
        return Seller(seller_id=key, seller_name="Concurrent Seller")

    # Fire 5 concurrent requests for same key
    results = await asyncio.gather(
        cache.get_or_fetch("c1", slow_fetch),
        cache.get_or_fetch("c1", slow_fetch),
        cache.get_or_fetch("c1", slow_fetch),
        cache.get_or_fetch("c1", slow_fetch),
        cache.get_or_fetch("c1", slow_fetch),
    )

    assert all(r.seller_id == "c1" for r in results)
    assert fetch_count == 1  # only one actual fetch


@pytest.mark.asyncio
async def test_fetch_error_returns_error_seller():
    """If fetch fails, cache stores error seller."""
    cache = SellerCache()

    async def failing_fetch(key: str) -> Seller:
        raise ConnectionError("Network error")

    seller = await cache.get_or_fetch("fail_key", failing_fetch)
    assert seller.seller_enrichment_status == EnrichmentStatus.ERROR
    assert "Network error" in (seller.seller_enrichment_error or "")
    assert cache.size == 1


@pytest.mark.asyncio
async def test_get_cached_returns_none_for_miss():
    cache = SellerCache()
    assert cache.get_cached("nonexistent") is None


@pytest.mark.asyncio
async def test_get_cached_returns_seller_for_hit():
    cache = SellerCache()

    async def fake_fetch(key: str) -> Seller:
        return Seller(seller_id=key, seller_name="Cached")

    await cache.get_or_fetch("c", fake_fetch)
    cached = cache.get_cached("c")
    assert cached is not None
    assert cached.seller_name == "Cached"
