"""Article loader — reads articles from CSV or returns defaults."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_articles(csv_path: Optional[Path] = None) -> list[str]:
    """Load article list from CSV file.

    CSV format: article,name (name is optional)
    Falls back to DEFAULT_ARTICLES if no file given.

    Returns list of article strings.
    """
    from src.config import DEFAULT_ARTICLES

    if csv_path is None:
        logger.info("No articles CSV provided, using default %d articles", len(DEFAULT_ARTICLES))
        return list(DEFAULT_ARTICLES)

    if not csv_path.exists():
        logger.warning("Articles file %s not found, using defaults", csv_path)
        return list(DEFAULT_ARTICLES)

    articles: list[str] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            art = row.get("article", "").strip()
            if art:
                articles.append(art)

    if not articles:
        logger.warning("No articles found in %s, using defaults", csv_path)
        return list(DEFAULT_ARTICLES)

    logger.info("Loaded %d articles from %s", len(articles), csv_path)
    return articles
