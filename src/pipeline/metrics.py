"""Metrics — run tracking, performance calculation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.models import (
    ArticleResult,
    ArticleStatus,
    DataOrigin,
    RunSummary,
)

logger = logging.getLogger(__name__)


class MetricsTracker:
    """Tracks run metrics, timing, and article results."""

    def __init__(self, articles_total: int, data_origin: DataOrigin = DataOrigin.LIVE) -> None:
        self._started_at: Optional[datetime] = None
        self._finished_at: Optional[datetime] = None
        self._articles_total = articles_total
        self._data_origin = data_origin
        self._article_results: list[ArticleResult] = []
        self._listings_scanned = 0
        self._valid_result_rows = 0
        self._browser_requests = 0
        self._run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    def start(self) -> None:
        """Mark run start."""
        self._started_at = datetime.now(timezone.utc)
        logger.info("Run %s started at %s", self._run_id, self._started_at.isoformat())

    def finish(self) -> None:
        """Mark run finish."""
        self._finished_at = datetime.now(timezone.utc)
        logger.info("Run %s finished at %s", self._run_id, self._finished_at.isoformat())

    def record_article(
        self,
        article: str,
        status: ArticleStatus,
        elapsed_seconds: float,
        listings_scanned: int = 0,
        valid_result_rows: int = 0,
        error_code: Optional[str] = None,
    ) -> None:
        """Record result for a single article."""
        self._article_results.append(ArticleResult(
            article=article,
            status=status,
            elapsed_seconds=elapsed_seconds,
            listings_scanned=listings_scanned,
            valid_result_rows=valid_result_rows,
            error_code=error_code,
        ))
        self._listings_scanned += listings_scanned
        self._valid_result_rows += valid_result_rows

    def add_browser_requests(self, count: int = 1) -> None:
        """Increment browser request counter."""
        self._browser_requests += count

    @property
    def elapsed_seconds(self) -> float:
        """Total elapsed time in seconds."""
        if self._started_at is None:
            return 0.0
        end = self._finished_at or datetime.now(timezone.utc)
        return (end - self._started_at).total_seconds()

    def build_summary(self) -> RunSummary:
        """Build the final RunSummary object."""
        elapsed = self.elapsed_seconds

        articles_success = sum(
            1 for ar in self._article_results
            if ar.status == ArticleStatus.SUCCESS
        )
        articles_no_matches = sum(
            1 for ar in self._article_results
            if ar.status == ArticleStatus.NO_MATCHES
        )
        articles_error = sum(
            1 for ar in self._article_results
            if ar.status == ArticleStatus.ERROR
        )
        articles_completed = articles_success + articles_no_matches + articles_error

        aps = (articles_completed / elapsed * 3600) if elapsed > 0 else 0.0
        saps = (
            (articles_success + articles_no_matches) / elapsed * 3600
            if elapsed > 0 else 0.0
        )

        summary = RunSummary(
            run_id=self._run_id,
            data_origin=self._data_origin,
            started_at=self._started_at,
            finished_at=self._finished_at,
            elapsed_seconds=round(elapsed, 2),
            articles_total=self._articles_total,
            articles_completed=articles_completed,
            articles_success=articles_success,
            articles_no_matches=articles_no_matches,
            articles_error=articles_error,
            listings_scanned=self._listings_scanned,
            valid_result_rows=self._valid_result_rows,
            http_requests=0,   # Not tracked separately (all via browser)
            browser_requests=self._browser_requests,
            articles_per_hour=round(aps, 2),
            successful_articles_per_hour=round(saps, 2),
            article_results=self._article_results,
        )

        logger.info(
            "Run summary: %d articles, %.0fs elapsed, %.1f art/hr",
            articles_completed, elapsed, aps,
        )
        return summary
