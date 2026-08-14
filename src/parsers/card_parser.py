"""Card parser — extracts data from a single Avito listing page.

Uses data-marker selectors for stability.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import Page

from src.pipeline.browser import BrowserManager
from src.domain_allowlist import assert_allowed_url
from src.models import DataOrigin, Listing, Seller, SellerType, EnrichmentStatus
from src.parsers.seller_utils import extract_seller_id_from_path, parse_seller_type, safe_first_text, strip_trailing_digits

logger = logging.getLogger(__name__)


def extract_avito_id(url: str) -> str:
    """Extract numeric Avito listing ID from URL."""
    # Try numeric ID at end of URL path
    match = re.search(r"_(\d+)$", url.rstrip("/").split("?")[0])
    if match:
        return match.group(1)

    # Try ID parameter
    match = re.search(r"/(\d{6,})", url.split("?")[0])
    if match:
        return match.group(1)

    # Fallback: full URL as ID (shouldn't happen)
    return url


async def _safe_text(locator) -> str:
    """Safely get inner text from locator, empty string on failure."""
    try:
        if await locator.count() > 0:
            return (await locator.first.inner_text()).strip()
    except Exception:
        pass
    return ""


async def _safe_attr(locator, attr: str) -> str:
    """Safely get attribute from locator, empty string on failure."""
    try:
        if await locator.count() > 0:
            value = await locator.first.get_attribute(attr)
            return (value or "").strip()
    except Exception:
        pass
    return ""


async def _extract_price(page: Page) -> Optional[float]:
    """Extract numeric price from the listing page.

    Tries the 'content' attribute first (clean number), then parses text.
    """
    price_loc = page.locator('[data-marker="item-view/item-price"]')
    if await price_loc.count() == 0:
        return None

    # Try content attribute (often contains clean number)
    content = await price_loc.first.get_attribute("content")
    if content:
        try:
            val = float(content.replace(" ", "").replace(",", "."))
            if val > 0:
                return val
        except ValueError:
            pass

    # Fallback: parse visible text
    text = await price_loc.first.inner_text()
    # Remove everything except digits
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        try:
            val = float(digits)
            if val > 0:
                return val
        except ValueError:
            pass

    return None


async def _extract_params(page: Page) -> list[str]:
    """Extract listing parameters (specs like condition, OEM number, etc.)."""
    params: list[str] = []
    # Try data-marker first
    param_items = page.locator('[data-marker="item-view/item-params"] li, '
                               'li.params-paramsList__item, '
                               '[data-marker="item-params"] li')
    count = await param_items.count()
    for i in range(count):
        try:
            text = await param_items.nth(i).inner_text()
            if text.strip():
                params.append(text.strip())
        except Exception:
            pass
    return params


async def _extract_condition(params: list[str], title: str, description: str) -> Optional[str]:
    """Detect condition from params, title, or description."""
    # Check params first — often has explicit "Состояние: Новое"
    for param in params:
        lower = param.lower()
        if "состояние" in lower or "состоян" in lower:
            if "нов" in lower:
                return "Новое"
            if "б/у" in lower or "б у" in lower:
                return "Б/у"
            # Return whatever the condition text is
            return param.split(":", 1)[-1].strip() if ":" in param else param

    return None


async def _extract_seller_preview(page: Page) -> Optional[Seller]:
    """Extract basic seller info available on the listing page itself.

    Full seller detail is handled by seller_parser.py on the profile page.
    """
    seller = Seller()

    # --- Seller name ---
    # Prefer data-marker selectors (TreeWalker extracts only first text node,
    # avoiding nested rating digits). Fall back to Schema.org meta.
    first_text = await safe_first_text(page, '[data-marker="seller-info/name"]')
    if first_text:
        seller.seller_name = first_text
    # Fallback: Avito meta item (may contain trailing digits, strip them)
    if not seller.seller_name:
        name_meta = page.locator('[itemprop="publisher"] meta[itemprop="name"]')
        if await name_meta.count() > 0:
            content = await name_meta.first.get_attribute("content")
            if content:
                seller.seller_name = strip_trailing_digits(content.strip())

    # --- Profile link via data-marker="seller-link/link" ---
    link_loc = page.locator(
        '[data-marker="seller-link/link"]'
        ', [data-marker="seller-info/link"]'
        ', [data-marker="seller-info"] a[href*="/user/"]',
    )
    if await link_loc.count() > 0:
        href = await link_loc.first.get_attribute("href")
        if href:
            if href.startswith("/"):
                href = f"https://www.avito.ru{href}"
            # Strip query params — keep only path
            seller.seller_profile_url = href.split("?")[0]
            # Extract seller_id — only hex-32 hashes from /user/<hex32>.
            # For /brands/slug we'll get the real hex32 ID later from ?sellerId=.
            seller.seller_id = extract_seller_id_from_path(href)

    # --- Seller type + optional "На Авито с ..." from label ---
    # <span data-marker="seller-info/label">Компания</span> inside a <p>
    # The <span> only contains the type; the parent <p> may contain "· На Авито с 2019 года"
    label_span = page.locator('[data-marker="seller-info/label"]')
    if await label_span.count() > 0:
        # Type is in the span
        type_text = (await label_span.first.inner_text()).strip()
        seller.seller_type = parse_seller_type(type_text)

        # "На Авито с ..." is in the parent <p> (whole text of the <p>)
        try:
            parent_text = await label_span.first.evaluate(
                "el => el.parentElement ? el.parentElement.textContent : ''"
            )
            since_match = re.search(
                r"на\s+авито\s+[сc]\s+([\w\s\d.]+?)(?:\.|,|\n|$)",
                parent_text, re.IGNORECASE,
            )
            if since_match:
                from src.parsers.seller_parser import _parse_since
                normalized, raw = _parse_since(since_match.group(1).strip())
                seller.seller_since = normalized
                seller.seller_since_raw = raw
        except Exception:
            pass

    # --- Rating (Schema.org: itemprop="ratingValue" content="4.9") ---
    rating_meta = page.locator('[itemprop="ratingValue"]')
    if await rating_meta.count() > 0:
        content = await rating_meta.first.get_attribute("content")
        if content:
            try:
                seller.seller_rating = float(content.replace(",", "."))
            except ValueError:
                pass

    # --- Reviews count (<a data-marker="rating-caption/rating">542 отзыва</a>) ---
    reviews_loc = page.locator('[data-marker="rating-caption/rating"]')
    if await reviews_loc.count() > 0:
        reviews_text = (await reviews_loc.first.inner_text()).strip()
        match = re.search(r"(\d[\d\s\xa0]*)", reviews_text)
        if match:
            try:
                seller.seller_reviews = int(
                    match.group(1).replace(" ", "").replace("\xa0", "")
                )
            except ValueError:
                pass

    if seller.seller_name or seller.seller_id:
        return seller
    return None


class CardParser:
    """Parses a single Avito listing page into a Listing model."""

    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    async def parse_listing(
        self,
        url: str,
        source_article: str,
    ) -> Optional[Listing]:
        """Fetch and parse a single listing.

        Returns Listing if successfully parsed, None on error.
        """
        assert_allowed_url(url)
        page = self.browser.page

        try:
            await self.browser.navigate(
                url,
                wait_selector='[data-marker="item-view/title-info"], '
                              '[data-marker="item-view/item-price"]',
                timeout=25_000,
            )
        except Exception as e:
            logger.warning("Failed to navigate to %s: %s", url, e)
            return None

        # Check for CAPTCHA on card page
        if await self.browser.detect_captcha():
            resolved = await self.browser.wait_for_captcha_resolution()
            if not resolved:
                logger.error("CAPTCHA not resolved on card page: %s", url)
                return None

        avito_id = extract_avito_id(url)

        # Title
        title = await _safe_text(
            page.locator('[data-marker="item-view/title-info"]')
        )

        # Price
        price = await _extract_price(page)

        # Address — try multiple selectors (most stable first)
        address = ""
        _address_selectors = [
            '[itemprop="address"]',                                    # Schema.org microdata (very stable)
            '[data-marker="item-view/item-address"]',                  # Avito data-marker (may rotate)
            'div[itemtype="http://schema.org/PostalAddress"] span',   # Schema.org full type
        ]
        for _sel in _address_selectors:
            address = await _safe_text(page.locator(_sel))
            if address:
                break

        # Description
        description = await _safe_text(
            page.locator('[data-marker="item-view/item-description"]')
        )

        # Parameters
        params = await _extract_params(page)

        # Condition
        condition = await _extract_condition(params, title, description)

        # Parse region/city from address
        city = ""
        region_str = ""
        if address:
            parts = [p.strip() for p in address.split(",") if p.strip()]
            if len(parts) >= 1:
                city = parts[0]
            if len(parts) >= 2:
                region_str = ", ".join(parts[:2])

        # Seller preview
        seller = await _extract_seller_preview(page)

        listing = Listing(
            source_article=source_article,
            avito_id=avito_id,
            url=url,
            title=title,
            price=price,
            condition=condition,
            region=region_str,
            city=city,
            address=address,
            description=description,
            params=params,
            data_origin=DataOrigin.LIVE,
            scraped_at=datetime.now(timezone.utc),
            seller=seller,
        )

        logger.debug(
            "Parsed listing %s: title=%s, price=%s, condition=%s, city=%s",
            avito_id, title[:50] if title else "", price, condition, city,
        )
        return listing
