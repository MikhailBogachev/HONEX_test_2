"""Tests for domain allowlist enforcement."""

from __future__ import annotations

import pytest

from src.domain_allowlist import assert_allowed_url, is_allowed_url


class TestIsAllowedUrl:
    def test_valid_avito_url(self):
        assert is_allowed_url("https://www.avito.ru/moskva") is True

    def test_valid_with_subdomain(self):
        assert is_allowed_url("https://m.avito.ru/api/11/items") is True

    def test_valid_without_www(self):
        assert is_allowed_url("https://avito.ru/moskva/zapchasti") is True

    def test_blocked_google(self):
        assert is_allowed_url("https://google.com") is False

    def test_blocked_similar_domain(self):
        assert is_allowed_url("https://avito.com.ru") is False

    def test_blocked_empty(self):
        assert is_allowed_url("") is False

    def test_blocked_no_scheme(self):
        # urlparse without scheme returns path-only
        assert is_allowed_url("avito.ru/test") is False


class TestAssertAllowedUrl:
    def test_valid_passes(self):
        assert_allowed_url("https://www.avito.ru/test")  # no exception

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="not on an allowed"):
            assert_allowed_url("https://evil.com/steal?cookies=1")
