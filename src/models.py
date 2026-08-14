"""Data models for Avito parser — HONEX test task.

All fields use strict types. Unknown values are None (not 0).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SellerType(str, Enum):
    COMPANY = "company"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class ParseMode(str, Enum):
    """Parsing mode: how listing data is extracted."""
    FEED = "feed"      # single page.evaluate() — fast, seller_type always UNKNOWN
    CARDS = "cards"    # visit each card page — slower, full seller data from cards


class ArticleStatus(str, Enum):
    SUCCESS = "success"
    NO_MATCHES = "no_matches"
    ERROR = "error"


class DataOrigin(str, Enum):
    LIVE = "live"
    SYNTHETIC = "synthetic"


class EnrichmentStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Seller
# ---------------------------------------------------------------------------

class Seller(BaseModel):
    """Seller profile data — one per unique seller per run."""

    seller_id: Optional[str] = None
    seller_profile_url: Optional[str] = None
    seller_type: SellerType = SellerType.UNKNOWN
    seller_name: Optional[str] = None
    seller_rating: Optional[float] = None
    seller_reviews: Optional[int] = None
    seller_items_count: Optional[int] = None
    seller_since: Optional[str] = None          # YYYY-MM-DD / YYYY-MM / YYYY
    seller_since_raw: Optional[str] = None       # original text
    seller_enrichment_status: EnrichmentStatus = EnrichmentStatus.UNAVAILABLE
    seller_enrichment_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class Listing(BaseModel):
    """Single validated Avito listing."""

    source_article: str                           # OEM article being searched
    avito_id: str                                 # unique Avito listing id
    url: str                                      # listing URL
    title: str = ""
    price: Optional[float] = None                 # numeric, positive
    currency: str = "RUB"
    condition: Optional[str] = None               # expected "Новое"
    region: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    params: list[str] = Field(default_factory=list)
    oem_match: bool = False                       # verified exact OEM
    data_origin: DataOrigin = DataOrigin.LIVE
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Seller — nested
    seller: Optional[Seller] = None


# ---------------------------------------------------------------------------
# Article run result
# ---------------------------------------------------------------------------

class ArticleResult(BaseModel):
    """Result per single article search."""

    article: str
    status: ArticleStatus = ArticleStatus.NO_MATCHES
    elapsed_seconds: float = 0.0
    listings_scanned: int = 0
    valid_result_rows: int = 0
    error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

class RunSummary(BaseModel):
    """Performance metrics for the whole run."""

    run_id: str = ""
    data_origin: DataOrigin = DataOrigin.LIVE
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    elapsed_seconds: float = 0.0
    articles_total: int = 0
    articles_completed: int = 0
    articles_success: int = 0
    articles_no_matches: int = 0
    articles_error: int = 0
    listings_scanned: int = 0
    valid_result_rows: int = 0
    http_requests: int = 0
    browser_requests: int = 0
    articles_per_hour: float = 0.0
    successful_articles_per_hour: float = 0.0
    article_results: list[ArticleResult] = Field(default_factory=list)
    notes: str = ""
