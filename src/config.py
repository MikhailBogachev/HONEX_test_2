"""Configuration for Avito parser."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Avito domains allowlist
# ---------------------------------------------------------------------------

AVITO_ALLOWED_DOMAINS: set[str] = {
    "www.avito.ru",
    "avito.ru",
    "m.avito.ru",
}


# ---------------------------------------------------------------------------
# Moscow & MO cities/regions (for reliable location matching)
# ---------------------------------------------------------------------------

MOSCOW_MO_LOCATIONS: list[str] = [
    # Federal cities & oblast keywords
    "москва",
    "московская область",
    "московская обл",
    "московский",
    # Major MO cities
    "подольск",
    "люберцы",
    "мытищи",
    "красногорск",
    "балашиха",
    "химки",
    "одинцово",
    "домодедово",
    "серпухов",
    "электросталь",
    "щиблино",
    "реутов",
    "люберцы",
    "железнодорожный",
    "жуковский",
    "раменское",
    "коломна",
    "сергиев посад",
    "пушкино",
    "ивантеевка",
    "фрязино",
    "лобня",
    "долгопрудный",
    "королёв",
    "мытищи",
    "красноармейск",
    "наро-фоминск",
    "озёры",
    "ступино",
    "чехов",
    "видное",
    "дзержинский",
    "котельники",
    "томилино",
    "малаховка",
    "краснознаменск",
    "голицыно",
    "кубинка",
    "опалиха",
    "нахабино",
    "дедовск",
    "солнечногорск",
    "клин",
    "истра",
    "волоколамск",
    "шаховская",
    "луховицы",
    "егорьевск",
    "шатура",
    "орехово-зуево",
    "павловский посад",
    "электрогорск",
    "воротынск",
    "рождествен",
    "тучково",
    "рассказовка",
    "коммунарка",
    "новая москва",
    "с. беседы",
    "беседы",
]


# ---------------------------------------------------------------------------
# Default articles (10 required HONEX articles)
# ---------------------------------------------------------------------------

DEFAULT_ARTICLES: list[str] = [
    "243502B010",
    # "243502U000",
    # "243512F000",
    # "243702R000",
    # "243702U003",
    # "243802R000",
    # "223112R020",
    # "233002F700",
    # "252802M000",
    # "252812U120",
]


# ---------------------------------------------------------------------------
# Delay config
# ---------------------------------------------------------------------------

@dataclass
class DelayConfig:
    min_seconds: float = 2.0
    max_seconds: float = 4.0


# ---------------------------------------------------------------------------
# Full config
# ---------------------------------------------------------------------------

@dataclass
class ParserConfig:
    region: str = "moskva_i_mo"
    headless: bool = False
    output_dir: Path = Path("output")
    max_results_per_article: int = 5
    delay: DelayConfig = field(default_factory=DelayConfig)
    data_origin: str = "live"
    view_width: int = 1920
    view_height: int = 1080
    locale: str = "ru-RU"
    skip_seller_enrichment: bool = False
    parse_mode: str = "feed"  # "feed" or "cards"

    @property
    def mode_label(self) -> str:
        """Short label used in output filenames and subdirectories."""
        return "cards" if self.parse_mode == "cards" else "feed"

    @staticmethod
    def from_env() -> "ParserConfig":
        """Build config from environment variables / defaults."""
        return ParserConfig(
            region=os.getenv("AVITO_REGION", "moskva_i_mo"),
            headless=os.getenv("HEADLESS", "false").lower() == "true",
            output_dir=Path(os.getenv("OUTPUT_DIR", "output")),
            delay=DelayConfig(
                min_seconds=float(os.getenv("MIN_DELAY", "2")),
                max_seconds=float(os.getenv("MAX_DELAY", "3")),
            ),
            parse_mode=os.getenv("PARSE_MODE", "feed"),
        )


# ---------------------------------------------------------------------------
# User-Agent
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
