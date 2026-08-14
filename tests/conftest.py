"""Shared pytest fixtures for the parser test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure parser/src is importable
PARSER_DIR = Path(__file__).resolve().parent.parent
if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

# Re-export key fixtures from fixtures.py
from tests.fixtures import make_listing, make_seller  # noqa: F401, E402
