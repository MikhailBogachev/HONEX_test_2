"""Article processor — unified pipeline for processing a single OEM article.

Extracted from main.py during refactoring. Both feed and cards modes share
the same filter → enrich → collect loop; only the listing *source* differs:
    - feed:  single page.evaluate() call returning Listing objects
    - cards: async generator that visits each card page individually
"""

from __future__ import annotations

import logging
import time

from src.pipeline.browser import BrowserManager
from src.parsers.card_parser import CardParser
from src.config import ParserConfig
from src.pipeline.filters import passes_all_filters
from src.parsers.listing_parser import ListingParser
from src.models import ArticleStatus, EnrichmentStatus, Listing, ParseMode
from src.output.seller_cache import SellerCache
from src.parsers.seller_parser import enrich_seller

logger = logging.getLogger("avito_parser")


# ---------------------------------------------------------------------------
# Seller enrichment helper
# ---------------------------------------------------------------------------

async def _enrich_listing_seller(
    listing: Listing,
    browser: BrowserManager,
    seller_cache: SellerCache,
    config: ParserConfig,
) -> None:
    """Enrich seller for a listing that passed filters.

    In cards mode, ``_extract_seller_preview`` already got seller_type from the
    card page; this adds rating/reviews/items_count/since via profile visit.
    In feed mode, everything is fetched from the seller profile.
    """
    if (
        not config.skip_seller_enrichment
        and listing.seller and listing.seller.seller_profile_url
    ):
        seller_key = listing.seller.seller_id or listing.seller.seller_profile_url
        try:
            enriched_seller = await seller_cache.get_or_fetch(
                seller_key,
                lambda key: enrich_seller(browser, listing.seller),  # type: ignore
            )
            listing.seller = enriched_seller
        except Exception as e:
            logger.warning("Seller enrichment failed for %s: %s", seller_key, e)
            listing.seller.seller_enrichment_status = EnrichmentStatus.ERROR
            listing.seller.seller_enrichment_error = str(e)[:200]
    elif config.skip_seller_enrichment and listing.seller:
        listing.seller.seller_enrichment_status = EnrichmentStatus.PARTIAL
        listing.seller.seller_enrichment_error = "enrichment_skipped"


# ---------------------------------------------------------------------------
# Listing sources — async generators yielding Listing from each mode
# ---------------------------------------------------------------------------

async def _feed_listing_source(
    article: str,
    listing_parser: ListingParser,
    region: str,
):
    """Yield listings from the search feed (single page.evaluate)."""
    listings = await listing_parser.search_article(article, region=region)
    for listing in listings:
        yield listing


async def _cards_listing_source(
    article: str,
    listing_parser: ListingParser,
    card_parser: CardParser,
    region: str,
):
    """Yield listings by visiting each card page individually."""
    urls = await listing_parser.search_article_urls(article, region=region)

    if not urls:
        return  # yield nothing

    for url in urls:
        listing: Listing | None = None
        try:
            listing = await card_parser.parse_listing(url, article)
        except Exception as e:
            logger.debug("Card parse failed for %s: %s", url, e)

        if listing:
            yield listing


# ---------------------------------------------------------------------------
# Shared processing pipeline
# ---------------------------------------------------------------------------

async def process_article(
    article: str,
    listing_parser: ListingParser,
    seller_cache: SellerCache,
    browser: BrowserManager,
    config: ParserConfig,
    card_parser: CardParser | None = None,
) -> tuple[list[Listing], ArticleStatus, str | None, int]:
    """Process a single OEM article through the full pipeline.

    The only difference between feed and cards mode is the *listing source*:
    an async generator that yields ``Listing`` objects. The rest of the
    pipeline (filter → enrich → collect) is shared.

    Returns:
        (valid_listings, status, error_code, listings_scanned)
    """
    t0 = time.monotonic()
    listings_scanned = 0
    is_cards = config.parse_mode == ParseMode.CARDS.value or config.parse_mode == "cards"

    try:
        max_needed = config.max_results_per_article
        region_miss_streak = 0
        REGION_MISS_LIMIT = 3
        valid_listings: list[Listing] = []

        # --- Choose listing source based on mode ---
        if is_cards:
            assert card_parser is not None, "card_parser required in cards mode"
            source = _cards_listing_source(
                article, listing_parser, card_parser, config.region,
            )
        else:
            source = _feed_listing_source(
                article, listing_parser, config.region,
            )

        # --- Unified filter → enrich → collect loop ---
        needs_log = True
        async for listing in source:
            if needs_log:
                if is_cards:
                    logger.info(
                        "Article %s: entering cards mode", article,
                    )
                else:
                    logger.info(
                        "Article %s: listings from feed", article,
                    )
                needs_log = False

            listings_scanned += 1

            # Apply business filters
            passed, reason = passes_all_filters(listing, article)
            if not passed:
                logger.debug("Listing %s rejected: %s", listing.avito_id, reason)
                if reason == "not_moscow_mo":
                    region_miss_streak += 1
                    if region_miss_streak >= REGION_MISS_LIMIT:
                        logger.info(
                            "Article %s: %d consecutive listings outside Moscow/MO, "
                            "stopping scan",
                            article, region_miss_streak,
                        )
                        break
                else:
                    region_miss_streak = 0
                continue

            # Passed all filters — reset streak
            region_miss_streak = 0
            listing.oem_match = True

            # Seller enrichment
            await _enrich_listing_seller(listing, browser, seller_cache, config)

            valid_listings.append(listing)

            # Early exit: enough results
            if len(valid_listings) >= max_needed:
                logger.info("Reached %d valid listings, stopping early", max_needed)
                break

            # Delay between seller enrichments
            await browser.random_delay()

        if not valid_listings:
            status = ArticleStatus.NO_MATCHES
            elapsed = time.monotonic() - t0
            logger.info(
                "Article %s: 0 valid / %d scanned → 0 (%.1fs)",
                article, listings_scanned, elapsed,
            )
            return [], status, None, listings_scanned

        # Already sorted by price (s=1), just take top-N
        valid_listings.sort(key=lambda l: l.price or 0)
        top_listings = valid_listings[:max_needed]

        elapsed = time.monotonic() - t0
        status = ArticleStatus.SUCCESS if top_listings else ArticleStatus.NO_MATCHES
        logger.info(
            "Article %s: %d valid / %d scanned → top %d (%.1fs)",
            article, len(valid_listings), listings_scanned, len(top_listings), elapsed,
        )
        return top_listings, status, None, listings_scanned

    except Exception as e:
        elapsed = time.monotonic() - t0
        error_code = type(e).__name__
        logger.error("Article %s failed after %.1fs: %s", article, elapsed, e)
        return [], ArticleStatus.ERROR, error_code, listings_scanned
