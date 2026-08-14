"""Seller parser — enriches seller profile with full data from the profile page.

Extracts 11 mandatory fields as defined in HONEX ТЗ:
  seller_id, seller_profile_url, seller_type, seller_name,
  seller_rating, seller_reviews, seller_items_count,
  seller_since, seller_since_raw,
  seller_enrichment_status, seller_enrichment_error
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.pipeline.browser import BrowserManager
from src.domain_allowlist import assert_allowed_url
from src.models import EnrichmentStatus, Seller, SellerType
from src.parsers.seller_utils import (
    extract_seller_id_from_query,
    parse_seller_type,
    parse_since as _parse_since,
    safe_first_text,
    strip_trailing_digits,
)

logger = logging.getLogger(__name__)


async def _safe_text(page, selector: str) -> str:
    """Safely extract inner text from a selector."""
    try:
        loc = page.locator(selector)
        if await loc.count() > 0:
            return (await loc.first.inner_text()).strip()
    except Exception:
        pass
    return ""




def _parse_rating(text: str) -> Optional[float]:
    """Try to extract numeric rating from text like '4.8' or 'Рейтинг: 4,8'."""
    match = re.search(r"(\d+[.,]\d+)", text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def _parse_int(text: str) -> Optional[int]:
    """Extract first integer from text."""
    match = re.search(r"(\d[\d\s]*)", text.replace("\xa0", ""))
    if match:
        return int(match.group(1).replace(" ", ""))
    return None


# _parse_since now lives in seller_utils and is imported as _parse_since above

async def enrich_seller(
    browser: BrowserManager,
    seller: Seller,
) -> Seller:
    """Enrich seller profile by visiting the profile page.

    Args:
        browser: BrowserManager instance
        seller: Partially filled Seller (must have seller_profile_url)

    Returns:
        Fully enriched Seller with enrichment_status.
    """
    url = seller.seller_profile_url
    if not url:
        seller.seller_enrichment_status = EnrichmentStatus.UNAVAILABLE
        seller.seller_enrichment_error = "no_profile_url"
        return seller

    if not url.startswith("http"):
        url = f"https://www.avito.ru{url}"

    # Strip query params from URL
    url = url.split("?")[0]

    try:
        assert_allowed_url(url)
    except ValueError:
        seller.seller_enrichment_status = EnrichmentStatus.ERROR
        seller.seller_enrichment_error = "url_not_allowed"
        return seller

    page = browser.page

    try:
        await browser.navigate(
            url,
            wait_selector='[data-marker="profilePage/profile"], '
                          '[data-marker="seller-info"], '
                          '[data-marker="userPage/userName"], '
                          'body',
            timeout=20_000,
            skip_delay=True,  # caller handles delay in main loop
        )
    except Exception as e:
        logger.warning("Seller page load failed for %s: %s", url, e)
        seller.seller_enrichment_status = EnrichmentStatus.ERROR
        seller.seller_enrichment_error = f"navigation_error: {e}"[:200]
        return seller

    # Wait for JS redirect that appends ?sellerId=<hex32> to URL.
    # Avito redirects /brands/slug → /brands/slug/all/...?sellerId=<hex>.
    # This happens via client-side JS, not server-side, so wait_until="domcontentloaded"
    # may fire before the redirect completes.
    if not re.search(r"[?&]sellerId=", page.url):
        try:
            # Wait up to 4s for sellerId to appear in URL
            await page.wait_for_url(
                re.compile(r"[?&]sellerId="), timeout=4_000,
            )
        except Exception:
            # No redirect appeared — that's fine, seller_id will stay None
            pass

    # CAPTCHA check
    if await browser.detect_captcha():
        # Wait for CAPTCHA resolution with seller-specific selectors
        seller_success = (
            '[data-marker="profilePage/profile"], '
            '[data-marker="userPage/userName"], '
            '[data-marker="seller-info"], '
            '[data-marker="userPage/userType"]'
        )
        resolved = await browser.wait_for_captcha_resolution(
            success_selector=seller_success,
            timeout=20_000,
        )
        if not resolved:
            seller.seller_enrichment_status = EnrichmentStatus.ERROR
            seller.seller_enrichment_error = "captcha_failed"
            return seller
        # CAPTCHA resolved — re-navigate to ensure clean page state
        try:
            await browser.navigate(
                url,
                wait_selector=seller_success,
                timeout=15_000,
                skip_delay=True,
            )
        except Exception:
            logger.warning("Re-navigation after CAPTCHA failed for %s", url)

    errors: list[str] = []

    # --- Extract fields ---
    # Seller ID: ONLY from ?sellerId= param in current page URL (most reliable).
    # Avito redirects /brands/slug → /user/<hex32>?sellerId=<hex32>.
    # If ?sellerId= is absent, seller_id stays None (no slug fallback).
    seller.seller_id = extract_seller_id_from_query(page.url)

    # Canonical profile URL (already stripped query params)
    seller.seller_profile_url = url

    # Seller name
    # Use TreeWalker for all name selectors to avoid picking up nested
    # rating numbers/dots that Avito sometimes nests.
    name = (
        await safe_first_text(page, '[data-marker="userPage/userName"]')
        or await safe_first_text(page, '[data-marker="profilePage/name"]')
        or await safe_first_text(page, '[data-marker="seller-info/name"]')
    ) or (
        await _safe_text(page, '[data-marker="userPage/userName"]')
        or await _safe_text(page, '[data-marker="profilePage/name"]')
        or await _safe_text(page, '[data-marker="seller-info/name"]')
    )
    if name:
        # Strip trailing digits glued by Avito: "Ирина8287054225" → "Ирина"
        seller.seller_name = strip_trailing_digits(name)
    elif not seller.seller_name:
        # Only flag error if name wasn't already set from card preview
        errors.append("name_not_found")

    # Seller type (company vs private)
    type_text = (
        await _safe_text(page, '[data-marker="userPage/userType"]')
        or await _safe_text(page, '[data-marker="profilePage/type"]')
        or await _safe_text(page, '[data-marker="seller-info/badge"]')
    )
    if type_text:
        seller.seller_type = parse_seller_type(type_text)

    # Rating
    rating_text = (
        await _safe_text(page, '[data-marker="userPage/rating"]')
        or await _safe_text(page, '[data-marker="profilePage/rating"]')
        or await _safe_text(page, '[data-marker="seller-info/rating"]')
    )
    if rating_text:
        seller.seller_rating = _parse_rating(rating_text)

    # Reviews count — data-marker, Schema.org link, or text search
    reviews_text = (
        await _safe_text(page, '[data-marker="userPage/reviewsCount"]')
        or await _safe_text(page, '[data-marker="profilePage/reviews"]')
        or await _safe_text(page, '[data-marker="seller-info/reviews"]')
        or await _safe_text(page, '[data-marker="rating-caption/rating"]')
    )
    if reviews_text:
        seller.seller_reviews = _parse_int(reviews_text)
    if not seller.seller_reviews:
        # Fallback: search for "отзыв" text near the rating
        try:
            review_links = page.locator('a:has-text("отзыв"), a:has-text("Отзыв")')
            if await review_links.count() > 0:
                text = await review_links.first.inner_text()
                seller.seller_reviews = _parse_int(text)
        except Exception:
            pass
    if not seller.seller_reviews:
        errors.append("reviews_not_found")

    # Active items count — two cases on seller profile:
    # 1) data-marker="extended_profile_tabs/tab(active)" → "Активные" tab with counter (preferred)
    # 2) "Объявления" tab with counter (fallback)
    items_text = (
        await _safe_text(page, '[data-marker="userPage/itemsCount"]')
        or await _safe_text(page, '[data-marker="profilePage/items"]')
    )
    if items_text:
        seller.seller_items_count = _parse_int(items_text)
    if not seller.seller_items_count:
        # Primary: active tab with data-marker
        try:
            active_tab = page.locator(
                '[data-marker="extended_profile_tabs/tab(active)"]'
            )
            if await active_tab.count() > 0:
                tab_text = await active_tab.first.inner_text()
                seller.seller_items_count = _parse_int(tab_text)
        except Exception:
            pass
    if not seller.seller_items_count:
        # Fallback: button with "Активные" or "Объявления"
        try:
            for tab_label in ["Активные", "Объявления"]:
                tab_btn = page.locator(f'button:has-text("{tab_label}")')
                if await tab_btn.count() > 0:
                    btn_text = await tab_btn.first.inner_text()
                    count = _parse_int(btn_text)
                    if count:
                        seller.seller_items_count = count
                        break
        except Exception:
            pass

    # Registration date ("На Авито с ...")
    # Try to find in order: data-marker, label on card, body text on seller page
    since_full = ""  # e.g. "На Авито с марта 2012"
    since_source = (
        await _safe_text(page, '[data-marker="userPage/registrationDate"]')
        or await _safe_text(page, '[data-marker="profilePage/since"]')
    )
    if since_source and re.search(r"на\s+авито", since_source, re.IGNORECASE):
        since_full = since_source.strip()
    if not since_full:
        # Try label span (from card page) and its parent <p>
        try:
            label_loc = page.locator('[data-marker="seller-info/label"]')
            if await label_loc.count() > 0:
                parent_text = await label_loc.first.evaluate(
                    "el => el.parentElement ? el.parentElement.textContent : ''"
                )
                if re.search(r"на\s+авито", parent_text, re.IGNORECASE):
                    since_full = parent_text.strip()
        except Exception:
            pass
    if not since_full:
        # Search body text for "На Авито с ..." (with ∙ or • as terminator)
        try:
            body = await page.inner_text("body")
            match = re.search(
                r"(На\s+Авито\s+[сc]\s+[\w\s\d.]+?)(?:\s*[∙•·\u00b7]|\.|,|\n|$)",
                body, re.IGNORECASE,
            )
            if match:
                since_full = match.group(1).strip()
        except Exception:
            pass

    if since_full:
        # Extract just the date part after "На Авито с" for normalization
        date_part_match = re.search(
            r"на\s+авито\s+[сc]\s+(.+)", since_full, re.IGNORECASE,
        )
        date_part = date_part_match.group(1).strip() if date_part_match else since_full
        normalized, _ = _parse_since(date_part)
        seller.seller_since = normalized
        seller.seller_since_raw = since_full  # e.g. "На Авито с марта 2012"
    # If card page already set seller_since from _extract_seller_preview, keep it
    elif seller.seller_since_raw:
        pass  # already set from card preview

    # Determine enrichment status
    critical_missing = not seller.seller_name and not seller.seller_id
    if critical_missing:
        seller.seller_enrichment_status = EnrichmentStatus.UNAVAILABLE
        seller.seller_enrichment_error = "no_name_or_id"
    elif errors:
        seller.seller_enrichment_status = EnrichmentStatus.PARTIAL
        seller.seller_enrichment_error = "; ".join(errors)
    else:
        seller.seller_enrichment_status = EnrichmentStatus.OK
        seller.seller_enrichment_error = None

    logger.debug(
        "Seller enriched: id=%s, name=%s, status=%s",
        seller.seller_id, seller.seller_name, seller.seller_enrichment_status,
    )
    return seller
