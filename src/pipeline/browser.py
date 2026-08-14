"""Playwright browser manager with stealth plugin and random delays."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright_stealth import Stealth

from src.config import USER_AGENT, DelayConfig, ParserConfig
from src.domain_allowlist import assert_allowed_url

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages a persistent Playwright browser session with stealth."""

    def __init__(self, config: ParserConfig) -> None:
        self.config = config
        self.delay = config.delay
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._request_count: int = 0
        self._browser_requests: int = 0

    async def start(self) -> None:
        """Launch browser, create context, apply stealth."""
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.config.headless,
        )
        self._context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={
                "width": self.config.view_width,
                "height": self.config.view_height,
            },
            locale=self.config.locale,
        )
        self._page = await self._context.new_page()
        await Stealth().apply_stealth_async(self._page)
        logger.info("Browser started (headless=%s)", self.config.headless)

    async def stop(self) -> None:
        """Close browser and cleanup."""
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        logger.info("Browser stopped. Total requests: %d", self._request_count)

    @property
    def page(self) -> Page:
        assert self._page is not None, "Browser not started"
        return self._page

    @property
    def browser_requests(self) -> int:
        return self._browser_requests

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    async def navigate(self, url: str, wait_selector: Optional[str] = None,
                       timeout: float = 20_000, skip_delay: bool = False) -> None:
        """Navigate to URL with safety guards. Only Avito domains allowed."""
        assert_allowed_url(url)
        self._request_count += 1
        self._browser_requests += 1

        logger.debug("Navigating to %s (request #%d)", url, self._request_count)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        if wait_selector:
            try:
                await self.page.wait_for_selector(wait_selector, timeout=12_000)
            except Exception:
                logger.warning("Selector %s not found on %s", wait_selector, url)

        if not skip_delay:
            await self.random_delay()

    async def scroll_page(self, scrolls: int = 3) -> None:
        """Emulate human-like scrolling."""
        for _ in range(scrolls):
            await self.page.mouse.wheel(0, random.randint(300, 700))
            await asyncio.sleep(random.uniform(0.5, 1.5))

    # ------------------------------------------------------------------
    # Delays
    # ------------------------------------------------------------------

    async def random_delay(self) -> None:
        """Short random delay between actions."""
        delay = random.uniform(self.delay.min_seconds, self.delay.max_seconds)
        logger.debug("Short delay: %.1fs", delay)
        await asyncio.sleep(delay)


    # ------------------------------------------------------------------
    # CAPTCHA handling
    # ------------------------------------------------------------------

    async def wait_for_captcha_resolution(
        self,
        timeout: float = 20_000,
        success_selector: str | None = None,
    ) -> bool:
        """Pause and wait for human to solve CAPTCHA in visible browser.

        Args:
            timeout: Maximum wait in ms.
            success_selector: CSS selector(s) that appear after CAPTCHA is solved.
                Defaults to feed result page selectors.

        Returns True if resolved, False on timeout.
        """
        logger.warning(
            "CAPTCHA detected! Please solve it manually in the browser window..."
        )
        selector = success_selector or (
            '[data-marker="catalog-serp"], '
            '[data-marker="profilePage/profile"], '
            '[data-marker="userPage/userName"]'
        )
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            logger.info("CAPTCHA resolved, continuing.")
            return True
        except Exception:
            logger.error("CAPTCHA resolution timed out.")
            return False

    async def detect_captcha(self) -> bool:
        """Check if current page shows a CAPTCHA or access block.

        Only checks URL and visible page indicators — does NOT search
        full HTML content (which has false positives from inline JS/CSS).
        """
        url = self.page.url.lower()

        # URL-based indicators
        for indicator in ("captcha", "access-limited", "restricted"):
            if indicator in url:
                return True

        # Check for a visible CAPTCHA iframe or container on the page
        # (SmartCaptcha, reCAPTCHA, etc.)
        captcha_visible_selectors = [
            'iframe[src*="captcha"]',
            'iframe[src*="recaptcha"]',
            'iframe[src*="smartcaptcha"]',
            '[class*="Captcha"]',
            '[class*="captcha"]',
            '[data-marker="captcha"]',
            '[id="captcha"]',
            '[id="captcha-container"]',
        ]
        for sel in captcha_visible_selectors:
            loc = self.page.locator(sel)
            try:
                if await loc.count() > 0:
                    # Verify it's actually visible, not just in the DOM
                    if await loc.first.is_visible():
                        return True
            except Exception:
                pass

        # Check page title for access block indicators
        try:
            title = await self.page.title()
            if "доступ" in title.lower() or "blocked" in title.lower():
                return True
        except Exception:
            pass

        return False
