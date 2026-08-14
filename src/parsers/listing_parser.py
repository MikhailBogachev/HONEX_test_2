"""Parser for Avito search results page.

Extracts Listing objects directly from the search feed cards using
data-marker selectors — no need to visit individual card pages.

Performance optimisation: all card data is extracted in a single
``page.evaluate()`` JavaScript call instead of hundreds of individual
Playwright locator round-trips (which cost ~5–10 ms each).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from src.domain_allowlist import assert_allowed_url
from src.models import DataOrigin, Listing, Seller, SellerType
from src.parsers.seller_utils import extract_seller_id_from_path, parse_seller_type
from src.pipeline.browser import BrowserManager

logger = logging.getLogger(__name__)

# Base URL template for article search (Новые, sorted by price)
SEARCH_URL_TEMPLATE = (
    "https://www.avito.ru/{region}/zapchasti_i_aksessuary?q={query}&s=1&f=110056_418153"
)

# ---------------------------------------------------------------------------
# JavaScript injected into the browser via page.evaluate().
# Extracts all data from every feed card in one call (~1 CDP round-trip
# instead of ~15 round-trips per card).
# ---------------------------------------------------------------------------
JS_EXTRACT_CARDS = r"""
() => {
  const cards = document.querySelectorAll('[data-marker="item"]');
  const results = [];

  for (const card of cards) {
    try {
      const d = {};

      // --- ID from data-item-id attribute ---
      const dataId = (card.getAttribute('data-item-id') || '').trim();
      d.data_item_id = /^\d+$/.test(dataId) ? dataId : '';

      // --- Title link ---
      const titleLink = card.querySelector('[data-marker="item-title"]');
      if (!titleLink) continue;  // skip cards without title
      d.href = titleLink.getAttribute('href') || '';
      d.title = (titleLink.textContent || '').trim();
      if (!d.href) continue;

      // --- Price ---
      // Method 1: <meta itemprop="price" content="4750">
      const priceMeta = card.querySelector('[itemprop="price"]');
      d.price_content = priceMeta
        ? (priceMeta.getAttribute('content') || '')
        : '';
      // Method 2: [data-marker="item-price-value"] text
      const priceVal = card.querySelector('[data-marker="item-price-value"]');
      d.price_text = priceVal ? (priceVal.textContent || '').trim() : '';
      // Method 3: [data-marker="item-price-block"] text
      d.price_block = '';
      if (!d.price_content && !d.price_text) {
        const priceBlock = card.querySelector(
          '[data-marker="item-price-block"]'
        );
        d.price_block = priceBlock
          ? (priceBlock.textContent || '').trim()
          : '';
      }

      // --- Params (OEM number, brand, etc.) ---
      const spParams = card.querySelector('[data-marker="item-sp-params"]');
      d.params_text = spParams ? (spParams.textContent || '').trim() : '';

      // --- Location ---
      const locEl = card.querySelector('[data-marker="item-location"]');
      d.location = locEl ? (locEl.textContent || '').trim() : '';

      // --- Condition badges ---
      d.badge_texts = [];
      const badgeSelectors = [
        '[data-marker="item-type"]',
        '[data-marker*="badge"]',
      ];
      for (const sel of badgeSelectors) {
        card.querySelectorAll(sel).forEach((el) => {
          const t = (el.textContent || '').trim();
          if (t && t.length <= 30) d.badge_texts.push(t);
        });
      }
      // Also scan spans with "badge" / "Badge" in class
      card
        .querySelectorAll('span[class*="badge"], span[class*="Badge"]')
        .forEach((el) => {
          const t = (el.textContent || '').trim();
          if (t && t.length <= 30) d.badge_texts.push(t);
        });

      // --- Description (best effort) ---
      d.description = '';
      const descSelectors = [
        '[data-marker="item-description"]',
        '[data-marker="item-snippet"]',
        '.iva-item-bottomBlock-VewGa p',
        '.styles-module-ellipsis-HCaiF',
      ];
      for (const sel of descSelectors) {
        const els = card.querySelectorAll(sel);
        for (const el of els) {
          const t = (el.textContent || '').trim();
          if (t && t.length > 10) {
            d.description = t;
            break;
          }
        }
        if (d.description) break;
      }

      // --- Seller info ---
      d.seller_profile_href = '';
      d.seller_name_text = '';
      d.seller_rating_text = '';
      d.seller_reviews_text = '';
      d.seller_type_text = '';
      d.seller_label_full = '';

      // Profile link — href only
      const sellerLink =
        card.querySelector('[data-marker="seller-info"] a[href]') ||
        card.querySelector('a[href*="/brands/"]') ||
        card.querySelector('a[href*="/user/"]');

      if (sellerLink) {
        d.seller_profile_href =
          sellerLink.getAttribute('href') || '';
      }

      // Name — prefer dedicated name element to avoid
      // accidentally including rating/reviews text
      const nameEl = card.querySelector(
        '[data-marker="seller-info/name"]'
      );
      if (nameEl) {
        d.seller_name_text = (nameEl.textContent || '').trim();
      } else if (sellerLink) {
        // Walk first text node only — skip nested <span> with
        // rating, dot, reviews etc.
        const walker = document.createTreeWalker(
          sellerLink,
          NodeFilter.SHOW_TEXT,
          null
        );
        const firstText = walker.nextNode();
        if (firstText) {
          d.seller_name_text = (firstText.textContent || '').trim();
        }
      }

      // Rating
      const scoreEl = card.querySelector(
        '[data-marker="seller-info/score"]'
      );
      d.seller_rating_text = scoreEl
        ? (scoreEl.textContent || '').trim()
        : '';

      // Reviews
      const summaryEl = card.querySelector(
        '[data-marker="seller-info/summary"]'
      );
      d.seller_reviews_text = summaryEl
        ? (summaryEl.textContent || '').trim()
        : '';

      // Seller type label + "На Авито с" — read parent's full text
      const labelEl = card.querySelector(
        '[data-marker="seller-info/label"]'
      );
      if (labelEl) {
        d.seller_type_text = (
          labelEl.textContent || ''
        ).trim();
        d.seller_label_full = labelEl.parentElement
          ? labelEl.parentElement.textContent
          : '';
      }

      results.push(d);
    } catch (_) {
      // Skip malformed cards silently
    }
  }

  return results;
}
""".strip()


def build_search_url(article: str, region: str = "moskva_i_mo") -> str:
    """Build Avito search URL for an OEM article."""
    url = SEARCH_URL_TEMPLATE.format(region=region, query=article)
    assert_allowed_url(url)
    return url


def extract_avito_id_from_url(url: str) -> str:
    """Extract numeric Avito listing ID from URL."""
    match = re.search(r"_(\d+)$", url.rstrip("/").split("?")[0])
    if match:
        return match.group(1)
    match = re.search(r"/(\d{6,})", url.split("?")[0])
    if match:
        return match.group(1)
    return url


def _build_listing_from_raw(raw: dict, source_article: str) -> Optional[Listing]:
    """Build a Listing object from a dict produced by JS_EXTRACT_CARDS.

    All heavy string parsing happens here in Python — no browser round-trips.
    """

    # --- href normalisation ---
    href: str = raw.get("href", "").strip()
    if not href:
        return None
    if href.startswith("/"):
        href = f"https://www.avito.ru{href}"
    if not href.startswith("http"):
        return None
    try:
        assert_allowed_url(href)
    except ValueError:
        logger.debug("URL not allowed: %s", href)
        return None

    title: str = (raw.get("title") or "").strip()
    avito_id = (raw.get("data_item_id") or "").strip()
    if not avito_id:
        avito_id = extract_avito_id_from_url(href)

    # --- Price (try 3 methods in priority order) ---
    price: Optional[float] = None
    content_raw = (raw.get("price_content") or "").strip()
    if content_raw:
        try:
            val = float(content_raw.replace(" ", "").replace(",", "."))
            if val > 0:
                price = val
        except ValueError:
            pass

    if price is None:
        price_text_raw = (raw.get("price_text") or "").strip()
        if price_text_raw:
            digits = re.sub(r"[^\d]", "", price_text_raw)
            if digits:
                try:
                    val = float(digits)
                    if val > 0:
                        price = val
                except ValueError:
                    pass

    if price is None:
        price_block_raw = (raw.get("price_block") or "").strip()
        if price_block_raw:
            digits = re.sub(r"[^\d]", "", price_block_raw)
            if digits:
                try:
                    val = float(digits)
                    if val > 0:
                        price = val
                except ValueError:
                    pass

    # --- Params ---
    params_text = (raw.get("params_text") or "").strip()
    params: list[str] = (
        [p.strip() for p in re.split(r"\s*[•·∙]\s*", params_text) if p.strip()]
        if params_text
        else []
    )

    # --- Condition ---
    condition: Optional[str] = None
    t_lower = title.lower() if title else ""
    for badge in (raw.get("badge_texts") or []):
        bl = badge.lower()
        if "нов" in bl:
            condition = "Новое"
            break
        if "б/у" in bl or re.search(r"\bб\s*у\b", bl):
            condition = "Б/у"
            break
    if condition is None:
        if "б/у" in t_lower or re.search(r"\bб\s*у\b", t_lower):
            condition = "Б/у"
        elif "нов" in t_lower:
            condition = "Новое"
    if condition is None:
        condition = "Новое"

    # --- Location ---
    location_text = (raw.get("location") or "").strip()
    parts = [p.strip() for p in location_text.split(",") if p.strip()]
    city = parts[0] if parts else ""
    address = location_text

    # --- Description ---
    description = (raw.get("description") or "").strip()

    # --- Seller ---
    seller: Optional[Seller] = None
    if raw.get("seller_profile_href") or raw.get("seller_name_text"):
        seller = Seller()
        raw_href = (raw.get("seller_profile_href") or "").strip()
        if raw_href:
            if raw_href.startswith("/"):
                raw_href = f"https://www.avito.ru{raw_href}"
            seller.seller_profile_url = raw_href.split("?")[0]
            # Extract seller_id — only hex-32 hashes. Slugs stay as None
            # and will be resolved later from ?sellerId= in seller_parser.
            seller.seller_id = extract_seller_id_from_path(raw_href)

        name_text = (raw.get("seller_name_text") or "").strip()
        if name_text:
            seller.seller_name = name_text.split("\n")[0].strip()

        # Rating
        rating_text = (raw.get("seller_rating_text") or "").strip()
        if rating_text:
            m = re.search(r"(\d+[.,]\d+)", rating_text)
            if m:
                try:
                    seller.seller_rating = float(m.group(1).replace(",", "."))
                except ValueError:
                    pass

        # Reviews
        reviews_text = (raw.get("seller_reviews_text") or "").strip()
        if reviews_text:
            m = re.search(r"(\d[\d\s\xa0]*)", reviews_text)
            if m:
                try:
                    seller.seller_reviews = int(
                        m.group(1).replace(" ", "").replace("\xa0", "")
                    )
                except ValueError:
                    pass

        # Seller type
        type_text = (raw.get("seller_type_text") or "").strip()
        if type_text:
            seller.seller_type = parse_seller_type(type_text)

        # "На Авито с ..."
        label_full = (raw.get("seller_label_full") or "").strip()
        if label_full:
            since_match = re.search(
                r"на\s+авито\s+[сc]\s+([\w\s\d.]+?)(?:\.|,|\n|$)",
                label_full,
                re.IGNORECASE,
            )
            if since_match:
                try:
                    from src.parsers.seller_parser import _parse_since

                    normalized, raw_since = _parse_since(
                        since_match.group(1).strip()
                    )
                    seller.seller_since = normalized
                    seller.seller_since_raw = raw_since
                except Exception:
                    pass

        if not (seller.seller_name or seller.seller_id):
            seller = None

    return Listing(
        source_article=source_article,
        avito_id=avito_id,
        url=href.split("?")[0],
        title=title,
        price=price,
        condition=condition,
        city=city,
        address=address,
        description=description,
        params=params,
        data_origin=DataOrigin.LIVE,
        scraped_at=datetime.now(timezone.utc),
        seller=seller,
    )


class ListingParser:
    """Parses search results page — extracts Listing objects from feed cards.

    All card data is extracted in a single ``page.evaluate()`` call,
    giving a massive speed-up over per-card locator round-trips.
    """

    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_article(
        self, article: str, region: str = "moskva_i_mo"
    ) -> list[Listing]:
        """Search for an article and return Listing objects parsed from feed.

        Uses a single ``page.evaluate()`` JavaScript call to extract all
        card data from the DOM in one CDP round-trip, then builds Listing
        objects in Python.
        """
        url = build_search_url(article, region)
        logger.info("Searching for article %s at %s", article, url)

        await self.browser.navigate(
            url, wait_selector='[data-marker="catalog-serp"]'
        )

        # Check for CAPTCHA
        if await self.browser.detect_captcha():
            resolved = await self.browser.wait_for_captcha_resolution()
            if not resolved:
                logger.error("CAPTCHA not resolved for %s", article)
                return []

        # Scroll once to trigger lazy loading of cards
        await self.browser.scroll_page(scrolls=1)

        page = self.browser.page

        # --- Single JavaScript call to extract all card data ---
        try:
            raw_cards: list[dict] = await page.evaluate(JS_EXTRACT_CARDS)
        except Exception as e:
            logger.warning(
                "page.evaluate failed for %s: %s — falling back to locator approach",
                article,
                e,
            )
            return await self._fallback_collect_listings(page, article)

        logger.debug("Found %d feed cards (JS)", len(raw_cards))

        if not raw_cards:
            logger.warning(
                "No cards found via JS for %s — trying fallback", article
            )
            return await self._fallback_collect_listings(page, article)

        listings: list[Listing] = []
        for raw in raw_cards:
            try:
                listing = _build_listing_from_raw(raw, article)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.debug("Failed to build Listing from card data: %s", e)

        logger.info(
            "Article %s: parsed %d listings from feed", article, len(listings)
        )
        return listings

    async def search_article_urls(
        self, article: str, region: str = "moskva_i_mo"
    ) -> list[str]:
        """Search for an article and return listing URL strings from feed.

        Does NOT build full Listing objects — just collects hrefs for
        the ``cards`` parse mode, where each card is visited individually.
        """
        url = build_search_url(article, region)
        logger.info("Searching for article %s (URLs only)", article)

        await self.browser.navigate(
            url, wait_selector='[data-marker="catalog-serp"]'
        )

        if await self.browser.detect_captcha():
            resolved = await self.browser.wait_for_captcha_resolution()
            if not resolved:
                logger.error("CAPTCHA not resolved for %s", article)
                return []

        await self.browser.scroll_page(scrolls=1)

        page = self.browser.page

        # Collect URLs from title links
        urls: list[str] = []
        items = page.locator('[data-marker="item-title"]')
        count = await items.count()
        for i in range(count):
            try:
                href = await items.nth(i).get_attribute("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.avito.ru{href}"
                if href.startswith("http"):
                    assert_allowed_url(href)
                    urls.append(href.split("?")[0])
            except Exception:
                pass

        logger.info("Article %s: found %d listing URLs in feed", article, len(urls))
        return urls

    async def get_total_results_count(self) -> int | None:
        """Try to read total result count from the page. Returns None if not found."""
        try:
            count_el = self.browser.page.locator(
                '[data-marker="page-title/count"], '
                '[data-marker="search-heading-count"]'
            )
            if await count_el.count() > 0:
                text = await count_el.first.inner_text()
                match = re.search(
                    r"(\d[\d\s]*)", text.replace("\xa0", "").replace(" ", "")
                )
                if match:
                    return int(match.group(1).replace(" ", ""))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Fallback: title-link based collection (old behaviour)
    # ------------------------------------------------------------------

    async def _fallback_collect_listings(
        self,
        page,
        source_article: str,
    ) -> list[Listing]:
        """Fallback when JS extraction returns no cards.

        Collects URLs from title links and returns minimal Listing objects.
        Still uses individual locator calls — keep as a safety net.
        """
        listings: list[Listing] = []
        items = page.locator('[data-marker="item-title"]')
        count = await items.count()
        logger.debug("Fallback: found %d item-title elements", count)

        for i in range(count):
            try:
                href = await items.nth(i).get_attribute("href")
                title = (await items.nth(i).inner_text()).strip()
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.avito.ru{href}"
                if not href.startswith("http"):
                    continue

                assert_allowed_url(href)
                avito_id = extract_avito_id_from_url(href)

                listings.append(
                    Listing(
                        source_article=source_article,
                        avito_id=avito_id,
                        url=href.split("?")[0],
                        title=title,
                        data_origin=DataOrigin.LIVE,
                        scraped_at=datetime.now(timezone.utc),
                    )
                )
            except Exception as e:
                logger.debug(
                    "Fallback: failed to extract item %d: %s", i, e
                )

        logger.info(
            "Fallback: collected %d listings from feed", len(listings)
        )
        return listings
