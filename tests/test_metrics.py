"""Tests for metrics tracker."""

from __future__ import annotations

import time

import pytest

from src.pipeline.metrics import MetricsTracker
from src.models import ArticleStatus, DataOrigin


class TestMetricsTracker:
    def test_start_finish_calculates_elapsed(self):
        tracker = MetricsTracker(articles_total=3)
        tracker.start()
        time.sleep(0.1)
        tracker.finish()

        assert tracker.elapsed_seconds > 0.05

    def test_record_article_updates_counts(self):
        tracker = MetricsTracker(articles_total=2)
        tracker.start()

        tracker.record_article("A1", ArticleStatus.SUCCESS, 1.0, listings_scanned=5, valid_result_rows=3)
        tracker.record_article("A2", ArticleStatus.NO_MATCHES, 0.5, listings_scanned=2, valid_result_rows=0)

        tracker.finish()
        summary = tracker.build_summary()

        assert summary.articles_completed == 2
        assert summary.articles_success == 1
        assert summary.articles_no_matches == 1
        assert summary.articles_error == 0
        assert summary.listings_scanned == 7
        assert summary.valid_result_rows == 3

    def test_articles_per_hour(self):
        tracker = MetricsTracker(articles_total=10)
        tracker.start()
        tracker._started_at = tracker._started_at  # keep reference
        # Simulate 360 seconds elapsed by recording finish in the future
        import datetime
        tracker._started_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=360)
        tracker.finish()

        for i in range(8):
            tracker.record_article(f"A{i}", ArticleStatus.SUCCESS, 1.0)
        tracker.record_article("A8", ArticleStatus.NO_MATCHES, 1.0)
        tracker.record_article("A9", ArticleStatus.ERROR, 1.0, error_code="timeout")

        summary = tracker.build_summary()

        # articles_per_hour = 10 / 360 * 3600 = 100
        assert summary.articles_per_hour > 90
        assert summary.articles_per_hour < 110

        # successful = (8 + 1) / 360 * 3600 = 90
        assert summary.successful_articles_per_hour > 80
        assert summary.successful_articles_per_hour < 100

    def test_article_results_list(self):
        tracker = MetricsTracker(articles_total=2)
        tracker.start()
        tracker.record_article("A1", ArticleStatus.SUCCESS, 2.5, listings_scanned=10, valid_result_rows=5)
        tracker.record_article("A2", ArticleStatus.ERROR, 0.5, error_code="access_limited")
        tracker.finish()

        summary = tracker.build_summary()

        assert len(summary.article_results) == 2
        assert summary.article_results[0].article == "A1"
        assert summary.article_results[1].error_code == "access_limited"
