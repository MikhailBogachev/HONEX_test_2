"""Main entry point — Avito parser for HONEX test task.

Orchestrates the full pipeline:
1. Load articles from CSV
2. Launch Playwright browser with stealth
3. For each article: search → filter → enrich sellers → dedup → top-5
4. Output CSV, JSON, XLSX, run_summary.json

Parse modes (--mode feed|cards):
    feed  — single page.evaluate() per article (fast, seller_type from feed only)
    cards — visit each listing card page individually (slower, full seller data)

Multi-run mode (--runs N):
    Runs the full pipeline N times back-to-back, saves each run summary
    independently (run_summary_{run_id}.json), and produces a timestamped
    multi_run_summary_{ts}.json with per-run details, averages, and medians.

Usage:
    python -m src.main                          # default: feed mode, 1 run
    python -m src.main --mode cards             # visit each card page
    python -m src.main --articles articles.csv  # custom CSV
    python -m src.main --headless               # headless mode
    python -m src.main --runs 3                 # 3 consecutive control runs
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from src.pipeline.article_processor import process_article
from src.articles import load_articles
from src.pipeline.browser import BrowserManager
from src.parsers.card_parser import CardParser
from src.config import ParserConfig
from src.output.dedup import Deduplicator
from src.parsers.listing_parser import ListingParser
from src.pipeline.metrics import MetricsTracker
from src.models import DataOrigin, Listing, RunSummary
from src.output.output import (
    write_csv,
    write_json,
    write_multi_run_summary,
    write_run_summary,
    write_xlsx,
)
from src.output.seller_cache import SellerCache

logger = logging.getLogger("avito_parser")


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

async def run(config: ParserConfig, articles_csv: Path | None = None) -> RunSummary:
    """Main run: process all articles and write output.

    Returns the RunSummary for potential aggregation across multiple runs.
    """
    # Load articles
    articles = load_articles(articles_csv)
    logger.info("Loaded %d articles (mode=%s)", len(articles), config.parse_mode)

    # Prepare output dir — mode-specific subdirectory
    mode = config.mode_label
    output_dir = config.output_dir / mode
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize components
    browser = BrowserManager(config)
    listing_parser = ListingParser(browser)
    card_parser = CardParser(browser) if mode == "cards" else None
    seller_cache = SellerCache()
    dedup = Deduplicator()
    metrics = MetricsTracker(
        articles_total=len(articles),
        data_origin=DataOrigin(config.data_origin),
    )

    all_valid_listings: list[Listing] = []

    try:
        # Start browser
        await browser.start()
        metrics.start()

        # Process each article
        for idx, article in enumerate(articles, 1):
            logger.info("=" * 60)
            logger.info("[%d/%d] Processing article: %s (mode=%s)", idx, len(articles), article, mode)
            logger.info("=" * 60)

            t0 = time.monotonic()

            valid, status, error, scanned = await process_article(
                article=article,
                listing_parser=listing_parser,
                seller_cache=seller_cache,
                browser=browser,
                config=config,
                card_parser=card_parser,
            )

            elapsed = time.monotonic() - t0

            # Deduplicate
            deduped: list[Listing] = []
            for listing in valid:
                if not dedup.is_duplicate(listing):
                    deduped.append(listing)

            all_valid_listings.extend(deduped)

            # Record metrics
            metrics.add_browser_requests(browser.browser_requests)
            metrics.record_article(
                article=article,
                status=status,
                elapsed_seconds=round(elapsed, 2),
                listings_scanned=scanned,
                valid_result_rows=len(deduped),
                error_code=error,
            )

            logger.info(
                "Article %s: status=%s, %d valid rows (%.1fs)",
                article, status.value, len(deduped), elapsed,
            )

        # Finalize
        metrics.finish()

    except KeyboardInterrupt:
        logger.warning("Interrupted! Saving partial results...")
        if not metrics.elapsed_seconds:
            metrics.finish()

    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        if not metrics.elapsed_seconds:
            metrics.finish()

    finally:
        await browser.stop()

    # ---------------------------------------------------------------
    # Write output files — mode label in filenames
    # ---------------------------------------------------------------
    summary = metrics.build_summary()
    ts = summary.run_id

    csv_path = output_dir / f"avito_results_{mode}_{ts}.csv"
    json_path = output_dir / f"avito_results_{mode}_{ts}.json"
    xlsx_path = output_dir / f"avito_results_{mode}_{ts}.xlsx"
    # Per-run summary — unique filename, not overwritten
    summary_path = output_dir / f"run_summary_{mode}_{ts}.json"

    write_csv(all_valid_listings, csv_path)
    write_json(all_valid_listings, json_path)
    write_xlsx(all_valid_listings, summary, xlsx_path)
    write_run_summary(summary, summary_path)

    # Print summary to console
    print("\n" + "=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)
    print(f"Run ID:           {summary.run_id}")
    print(f"Mode:             {mode}")
    print(f"Elapsed:          {summary.elapsed_seconds:.1f}s")
    print(f"Articles total:   {summary.articles_total}")
    print(f"  Success:        {summary.articles_success}")
    print(f"  No matches:     {summary.articles_no_matches}")
    print(f"  Error:          {summary.articles_error}")
    print(f"Listings scanned: {summary.listings_scanned}")
    print(f"Valid rows:       {summary.valid_result_rows}")
    print(f"Art/hour:         {summary.articles_per_hour:.1f}")
    print(f"Success art/hr:   {summary.successful_articles_per_hour:.1f}")
    print(f"Sellers cached:   {seller_cache.size}")
    print()
    print(f"Output CSV:  {csv_path}")
    print(f"Output JSON: {json_path}")
    print(f"Output XLSX: {xlsx_path}")
    print(f"Summary:     {summary_path}")
    print("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# Multi-run orchestrator
# ---------------------------------------------------------------------------


def _computing_aggregates(summaries: list[RunSummary]) -> dict:
    """Compute per-run metrics, averages, and medians from a list of RunSummary."""

    def _safe_mean(values: list[float]) -> float:
        return round(statistics.mean(values), 2) if values else 0.0

    def _safe_median(values: list[float]) -> float:
        return round(statistics.median(values), 2) if values else 0.0

    # Per-run metrics
    per_run: list[dict] = []
    for s in summaries:
        # Статья-успех = success + no_matches (артикул обработан, просто нет данных)
        success_articles = s.articles_success + s.articles_no_matches
        total_processed = s.articles_completed
        success_rate = (
            round(success_articles / s.articles_total * 100, 1)
            if s.articles_total > 0
            else 0.0
        )
        error_rate = (
            round(s.articles_error / s.articles_total * 100, 1)
            if s.articles_total > 0
            else 0.0
        )
        per_run.append({
            "run_id": s.run_id,
            "elapsed_seconds": s.elapsed_seconds,
            "articles_total": s.articles_total,
            "articles_success": s.articles_success,
            "articles_no_matches": s.articles_no_matches,
            "articles_error": s.articles_error,
            "listings_scanned": s.listings_scanned,
            "valid_result_rows": s.valid_result_rows,
            "browser_requests": s.browser_requests,
            "articles_per_hour": s.articles_per_hour,
            "successful_articles_per_hour": s.successful_articles_per_hour,
            "success_rate_pct": success_rate,
            "error_rate_pct": error_rate,
        })

    # Collect arrays for aggregation
    elapsed_vals = [r["elapsed_seconds"] for r in per_run]
    aph_vals = [r["articles_per_hour"] for r in per_run]
    saph_vals = [r["successful_articles_per_hour"] for r in per_run]
    scanned_vals = [r["listings_scanned"] for r in per_run]
    valid_vals = [r["valid_result_rows"] for r in per_run]
    req_vals = [r["browser_requests"] for r in per_run]
    success_rate_vals = [r["success_rate_pct"] for r in per_run]
    error_rate_vals = [r["error_rate_pct"] for r in per_run]

    avg = {
        "elapsed_seconds": _safe_mean(elapsed_vals),
        "articles_per_hour": _safe_mean(aph_vals),
        "successful_articles_per_hour": _safe_mean(saph_vals),
        "listings_scanned": _safe_mean(scanned_vals),
        "valid_result_rows": _safe_mean(valid_vals),
        "browser_requests": _safe_mean(req_vals),
        "success_rate_pct": _safe_mean(success_rate_vals),
        "error_rate_pct": _safe_mean(error_rate_vals),
    }

    median = {
        "elapsed_seconds": _safe_median(elapsed_vals),
        "articles_per_hour": _safe_median(aph_vals),
        "successful_articles_per_hour": _safe_median(saph_vals),
        "listings_scanned": _safe_median(scanned_vals),
        "valid_result_rows": _safe_median(valid_vals),
        "browser_requests": _safe_median(req_vals),
        "success_rate_pct": _safe_median(success_rate_vals),
        "error_rate_pct": _safe_median(error_rate_vals),
    }

    return {
        "runs_total": len(summaries),
        "articles_total": summaries[0].articles_total if summaries else 0,
        "per_run": per_run,
        "average": avg,
        "median": median,
    }


async def multi_run(
    config: ParserConfig,
    n_runs: int,
    articles_csv: Path | None = None,
) -> list[RunSummary]:
    """Execute the full pipeline N times and produce aggregated results.

    Each run creates its own output files (CSV, JSON, XLSX) and
    run_summary_{run_id}.json in a mode-specific subdirectory.
    After all runs, writes a timestamped multi_run_summary_{ts}.json
    with per-run metrics, averages and medians.
    """
    mode = config.mode_label
    summaries: list[RunSummary] = []

    for run_idx in range(1, n_runs + 1):
        print("\n" + "#" * 60)
        print(f"#  CONTROL RUN {run_idx} / {n_runs}  (mode={mode})")
        print("#" * 60 + "\n")

        logger.info("Starting control run %d/%d (mode=%s)", run_idx, n_runs, mode)

        try:
            summary = await run(config, articles_csv=articles_csv)
            summaries.append(summary)
        except Exception as e:
            logger.error("Run %d failed with fatal error: %s", run_idx, e, exc_info=True)

        # Brief pause between runs (except after last)
        if run_idx < n_runs:
            logger.info("Pausing 10s before next run...")
            await asyncio.sleep(10)

    # ---------------------------------------------------------------
    # Write aggregated multi-run summary (timestamped, not overwritten)
    # ---------------------------------------------------------------
    if summaries:
        aggregates = _computing_aggregates(summaries)

        # Timestamped filename — never overwritten
        multi_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = config.output_dir / mode
        multi_path = output_dir / f"multi_run_summary_{mode}_{multi_ts}.json"
        write_multi_run_summary(aggregates, multi_path)

        # Also keep the latest single-run summary as run_summary.json
        last_summary = summaries[-1]
        latest_path = output_dir / f"run_summary_{mode}.json"
        write_run_summary(last_summary, latest_path)

        # Print multi-run table
        _print_multi_run_table(aggregates, mode=mode)

    return summaries


def _print_multi_run_table(aggregates: dict, mode: str = "feed") -> None:
    """Pretty-print the multi-run comparison table to console."""
    print("\n" + "=" * 80)
    print(f"MULTI-RUN RESULTS  (mode={mode})")
    print("=" * 80)

    per_run = aggregates.get("per_run", [])
    if not per_run:
        print("No runs completed.")
        return

    # Header
    hdr = (
        f"{'Run':<20} {'Elapsed':>8} {'Art/hr':>8} "
        f"{'Succ art/hr':>12} {'Success%':>9} {'Error%':>8} "
        f"{'Scanned':>8} {'Valid':>6} {'Req':>5}"
    )
    print(hdr)
    print("-" * len(hdr))

    for r in per_run:
        print(
            f"{r['run_id']:<20} {r['elapsed_seconds']:>7.1f}s "
            f"{r['articles_per_hour']:>8.1f} "
            f"{r['successful_articles_per_hour']:>12.1f} "
            f"{r['success_rate_pct']:>8.1f}% "
            f"{r['error_rate_pct']:>7.1f}% "
            f"{r['listings_scanned']:>8} "
            f"{r['valid_result_rows']:>6} "
            f"{r['browser_requests']:>5}"
        )

    print("-" * len(hdr))

    avg = aggregates.get("average", {})
    med = aggregates.get("median", {})

    print(
        f"{'AVERAGE':<20} {avg.get('elapsed_seconds', 0):>7.1f}s "
        f"{avg.get('articles_per_hour', 0):>8.1f} "
        f"{avg.get('successful_articles_per_hour', 0):>12.1f} "
        f"{avg.get('success_rate_pct', 0):>8.1f}% "
        f"{avg.get('error_rate_pct', 0):>7.1f}% "
        f"{avg.get('listings_scanned', 0):>8.0f} "
        f"{avg.get('valid_result_rows', 0):>6.0f} "
        f"{avg.get('browser_requests', 0):>5.0f}"
    )
    print(
        f"{'MEDIAN':<20} {med.get('elapsed_seconds', 0):>7.1f}s "
        f"{med.get('articles_per_hour', 0):>8.1f} "
        f"{med.get('successful_articles_per_hour', 0):>12.1f} "
        f"{med.get('success_rate_pct', 0):>8.1f}% "
        f"{med.get('error_rate_pct', 0):>7.1f}% "
        f"{med.get('listings_scanned', 0):>8.0f} "
        f"{med.get('valid_result_rows', 0):>6.0f} "
        f"{med.get('browser_requests', 0):>5.0f}"
    )

    print("=" * 80)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Avito OEM article parser — HONEX test task",
    )
    parser.add_argument(
        "--articles", type=Path, default=None,
        help="Path to articles CSV file (default: built-in 10 HONEX articles)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Output directory (default: output/)",
    )
    parser.add_argument(
        "--headless", action="store_true", default=False,
        help="Run browser in headless mode (less stable against detection)",
    )
    parser.add_argument(
        "--max-results", type=int, default=5,
        help="Max valid listings per article (default: 5)",
    )
    parser.add_argument(
        "--skip-seller-enrichment", action="store_true", default=False,
        help="Skip seller profile enrichment (use feed data only, faster)",
    )
    parser.add_argument(
        "--mode", choices=["feed", "cards"], default="feed",
        help="Parse mode: 'feed' (fast, single page.evaluate) or "
             "'cards' (visit each listing, full seller data). Default: feed.",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Number of consecutive control runs (default: 1). "
             "When >1, produces multi_run_summary with averages and medians.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="Enable debug logging",
    )
    return parser.parse_args()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    config = ParserConfig.from_env()
    if args.headless:
        config.headless = True
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.max_results:
        config.max_results_per_article = args.max_results
    if args.skip_seller_enrichment:
        config.skip_seller_enrichment = True
    config.parse_mode = args.mode

    n_runs = max(1, args.runs)

    logger.info("Starting Avito parser...")
    logger.info("Config: region=%s, headless=%s, max_results=%d, mode=%s, runs=%d",
                config.region, config.headless, config.max_results_per_article,
                config.parse_mode, n_runs)

    if n_runs > 1:
        asyncio.run(multi_run(config, n_runs=n_runs, articles_csv=args.articles))
    else:
        asyncio.run(run(config, articles_csv=args.articles))


if __name__ == "__main__":
    main()
