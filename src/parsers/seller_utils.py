"""Shared seller utilities — deduplicated logic for seller-id extraction,
TreeWalker text extraction, seller-type detection, and name cleanup.

Used by card_parser.py, listing_parser.py, and seller_parser.py.
"""

from __future__ import annotations

import re

from src.models import SellerType


# ---------------------------------------------------------------------------
# Seller ID extraction
# ---------------------------------------------------------------------------

def extract_seller_id_from_path(url: str) -> str | None:
    """Extract hex-32 seller ID from URL path ``/user/<hex32>``.

    Returns the 32-char hex string or ``None`` if not found.
    Only matches ``/user/`` paths — ``/brands/slug`` is NOT a seller ID.
    """
    m = re.search(r"/user/([a-f0-9]{32})", url)
    return m.group(1) if m else None


def extract_seller_id_from_query(url: str) -> str | None:
    """Extract hex-32 seller ID from ``?sellerId=<hex32>`` query param.

    Returns the 32-char hex string or ``None`` if not found.
    """
    m = re.search(r"[?&]sellerId=([a-f0-9]{32})", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# TreeWalker-based text extraction (JavaScript snippet + async helpers)
# ---------------------------------------------------------------------------

# JavaScript string for use in page.evaluate() — extracts only the first
# TEXT_NODE of the matched element, skipping nested children like rating dots.
TREEWALKER_FIRST_TEXT_JS = """el => {
    const walker = document.createTreeWalker(
        el, NodeFilter.SHOW_TEXT, null
    );
    const node = walker.nextNode();
    return node ? node.textContent.trim() : '';
}"""


async def safe_first_text(page, selector: str) -> str:
    """Extract only the first text node from *selector* via TreeWalker.

    Unlike ``inner_text()`` (which returns text of the entire subtree),
    this returns only the first ``TEXT_NODE`` — avoiding nested elements
    like rating numbers or dots that Avito sometimes nests inside name
    containers.

    Returns empty string on any failure.
    """
    try:
        loc = page.locator(selector)
        if await loc.count() > 0:
            return await loc.first.evaluate(TREEWALKER_FIRST_TEXT_JS)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Seller type detection
# ---------------------------------------------------------------------------

def parse_seller_type(text: str) -> SellerType:
    """Detect seller type from label text (case-insensitive).

    Recognises Russian keywords: компания, магазин, бизнес, ИП → COMPANY;
    частн, частное → PRIVATE; everything else → UNKNOWN.
    """
    tl = text.strip().lower()
    if any(w in tl for w in ["компания", "магазин", "бизнес", "ип"]):
        return SellerType.COMPANY
    if any(w in tl for w in ["частн", "частное"]):
        return SellerType.PRIVATE
    return SellerType.UNKNOWN


# ---------------------------------------------------------------------------
# Name cleanup
# ---------------------------------------------------------------------------

def parse_since(text: str) -> tuple[str | None, str | None]:
    """Parse seller registration date from various formats.

    Returns ``(normalized, raw_text)`` where *normalized* is YYYY-MM-DD,
    YYYY-MM, or YYYY (or ``None`` if unparseable).
    """
    raw = text.strip()
    if not raw:
        return None, None

    month_map = {
        "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
        "мая": "05", "июня": "06", "июля": "07", "августа": "08",
        "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
    }

    # "12 марта 2020"
    match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if match:
        day, month_name, year = match.groups()
        month_num = month_map.get(month_name.lower())
        if month_num:
            return f"{year}-{month_num}-{day.zfill(2)}", raw

    # "2020-03-12"
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if match:
        return match.group(0), raw

    # "март 2020"
    match = re.search(r"(\w+)\s+(\d{4})", raw)
    if match:
        month_name, year = match.groups()
        month_num = month_map.get(month_name.lower())
        if month_num:
            return f"{year}-{month_num}", raw

    # just year "2020"
    match = re.search(r"(\d{4})", raw)
    if match:
        return match.group(1), raw

    return None, raw


def strip_trailing_digits(name: str) -> str:
    """Remove 5+ trailing digits glued by Avito to seller names.

    Examples: ``"Ирина8287054225"`` → ``"Ирина"``
              ``"Automania-shop3622040708"`` → ``"Automania-shop"``
    Returns the cleaned name, or the original if cleaning would leave it empty.
    """
    cleaned = re.sub(r"\d{5,}$", "", name).strip()
    return cleaned or name
