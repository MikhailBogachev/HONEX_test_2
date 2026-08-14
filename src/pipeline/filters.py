"""Business logic filters for Avito listings.

Filters:
1. OEM article match (exact, normalized)
2. Condition: only 'Новое'
3. Region: Moscow and Moscow Oblast
4. Price: positive numeric
"""

from __future__ import annotations

import re
from typing import Optional

from src.config import MOSCOW_MO_LOCATIONS
from src.models import Listing


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_article(article: str) -> str:
    """Normalize OEM article: uppercase, strip spaces/dashes/separators."""
    return re.sub(r"[\s\-_./]+", "", article.strip().upper())


def is_oem_match(listing: Listing, target_article: str) -> bool:
    """Check if the listing matches the target OEM article exactly.

    Searches across title, description, and params fields.
    Normalizes both sides before comparison.
    """
    target = normalize_article(target_article)
    # Check all text fields
    texts = [
        listing.title or "",
        listing.description or "",
        " ".join(listing.params),
    ]
    combined = " ".join(texts)
    # Normalize the combined text
    normalized = normalize_article(combined)
    return target in normalized


# ---------------------------------------------------------------------------
# Condition filter
# ---------------------------------------------------------------------------

def is_new_condition(listing: Listing) -> bool:
    """Accept only listings with condition 'Новое'.

    Checks the condition field AND params/title as fallback.
    """
    # Direct condition field
    if listing.condition:
        if re.search(r"\bнов(ое|ая|ые|ый)\b", listing.condition, re.IGNORECASE):
            return True
        # If condition is explicitly Б/у or other — reject
        if re.search(r"\bб/?у\b", listing.condition, re.IGNORECASE):
            return False

    # Fallback: check title and params
    texts = [listing.title or "", " ".join(listing.params)]
    combined = " ".join(texts).lower()

    # If explicitly Б/у — reject
    if re.search(r"\bб/?у\b", combined):
        return False

    # If explicitly "новое" in params — accept
    if "нов" in combined:
        return True

    # Default for auto parts: if no condition specified, reject
    return False


# ---------------------------------------------------------------------------
# Region filter
# ---------------------------------------------------------------------------

_MOSCOW_MO_PATTERNS = [
    re.compile(re.escape(loc), re.IGNORECASE) for loc in MOSCOW_MO_LOCATIONS
]

# Broader patterns for Moscow Oblast
_MO_BROAD_PATTERNS = [
    re.compile(r"московская\s+обл", re.IGNORECASE),
    re.compile(r"московская\s+область", re.IGNORECASE),
    re.compile(r"\bмосква\b", re.IGNORECASE),
    re.compile(r"\bновая\s+москва\b", re.IGNORECASE),
]


def is_moscow_mo(listing: Listing) -> bool:
    """Check if listing location is Moscow or Moscow Oblast.

    Uses the address field. Does NOT use fragile patterns like 'мо' in text.
    """
    address = (listing.address or "").strip()
    if not address:
        return False

    # Check broad patterns first
    for pattern in _MO_BROAD_PATTERNS:
        if pattern.search(address):
            return True

    # Check city field
    city = (listing.city or "").strip()
    if city:
        for pattern in _MO_BROAD_PATTERNS:
            if pattern.search(city):
                return True

    # Check location patterns
    for pattern in _MOSCOW_MO_PATTERNS:
        if pattern.search(address):
            return True

    return False


# ---------------------------------------------------------------------------
# Price filter
# ---------------------------------------------------------------------------

def is_valid_price(listing: Listing) -> bool:
    """Accept only listings with a positive numeric price."""
    if listing.price is None:
        return False
    return listing.price > 0


# ---------------------------------------------------------------------------
# Article-in-params filter (stricter OEM check)
# ---------------------------------------------------------------------------

def is_article_in_params(listing: Listing, target_article: str) -> bool:
    """Check if the normalized target article appears in listing params.

    This is stricter than is_oem_match — it requires the article to be in
    the structured params (e.g. OEM field), not just anywhere in the
    title/description. Prevents false positives from kit/vehicle listings
    where the article is mentioned only in the description.
    """
    target = normalize_article(target_article)
    if not target:
        return False
    normalized_params = normalize_article(" ".join(listing.params))
    return target in normalized_params


# ---------------------------------------------------------------------------
# Combined filter
# ---------------------------------------------------------------------------

def passes_all_filters(listing: Listing, target_article: str) -> tuple[bool, str]:
    """Apply all business filters. Returns (passed, reason)."""
    if not is_oem_match(listing, target_article):
        return False, "oem_mismatch"
    if not is_article_in_params(listing, target_article):
        return False, "article_not_in_params"
    if not is_new_condition(listing):
        return False, "condition_not_new"
    if not is_moscow_mo(listing):
        return False, "not_moscow_mo"
    if not is_valid_price(listing):
        return False, "invalid_price"
    return True, "ok"
