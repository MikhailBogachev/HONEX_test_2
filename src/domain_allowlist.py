"""Domain allowlist check — only Avito domains are allowed."""

from __future__ import annotations

from urllib.parse import urlparse

from src.config import AVITO_ALLOWED_DOMAINS


def is_allowed_url(url: str) -> bool:
    """Return True if the URL belongs to an allowed Avito domain."""
    try:
        parsed = urlparse(url)
        return parsed.hostname in AVITO_ALLOWED_DOMAINS if parsed.hostname else False
    except Exception:
        return False


def assert_allowed_url(url: str) -> None:
    """Raise ValueError if URL is not on an allowed Avito domain."""
    if not is_allowed_url(url):
        raise ValueError(f"URL is not on an allowed Avito domain: {url}")
