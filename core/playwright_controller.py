"""Autonomous Headless Playwright Controller for Google Colab.

Manages:
- Persistent browser contexts for Google session preservation (./colab_user_data)
- Anti-detection automation flags (--disable-blink-features=AutomationControlled)
- Triggering 'Run all cells' shortcuts (Ctrl+F9 / Cmd+F9)
- Real-time monitoring of modal dialogs for GPU quota depletion
- Tunnel URL discovery ([AEGIS_READY] BASE_URL=https://xxxx.trycloudflare.com/v1)
- Standardized exit codes:
    0 = Successfully booted & tunnel discovered
    1 = Timeout / General Failure
    2 = GPU Quota Limit Reached (Circuit-Breaker Trigger)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from enum import IntEnum
from pathlib import Path
from typing import Optional, Tuple

try:
    from playwright.async_api import BrowserContext, Page, async_playwright
except ImportError:
    async_playwright = None  # type: ignore

from .alerting import AlertDispatcher, AlertLevel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AegisRoute.Controller] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AegisRoute.Controller")


class ColabStatus(IntEnum):
    SUCCESS = 0
    ERROR = 1
    QUOTA_EXCEEDED = 2


# Regex to detect Cloudflare quick tunnel and Aegis Ready marker
AEGIS_READY_REGEX = re.compile(
    r"\[AEGIS_READY\]\s*BASE_URL=(https://[a-zA-Z0-9-]+\.trycloudflare\.com/v1)",
    re.IGNORECASE,
)
CLOUDFLARE_URL_REGEX = re.compile(
    r"https://[a-zA-Z0-9-]+\.trycloudflare\.com",
    re.IGNORECASE,
)

# Quota limit keywords in modal dialogs
QUOTA_KEYWORDS = [
    "gpu-limit",
    "gpu limit",
    "recheneinheiten",
    "usage limit",
    "no backend",
    "cannot connect to gpu backend",
    "compute units",
    "colab pro",
    "exceeded your assigned quota",
    "quota",
]


class ColabPlaywrightController:
    """Controls Google Colab notebook execution via headless Playwright."""

    def __init__(
        self,
        notebook_url: str,
        user_data_dir: Optional[str] = None,
        headless: bool = True,
        timeout_seconds: int = 420,
        tunnel_callback_url: Optional[str] = None,
    ) -> None:
        self.notebook_url = notebook_url
        self.user_data_dir = user_data_dir or os.getenv(
            "AEGIS_USER_DATA_DIR",
            str(Path.cwd() / "colab_user_data"),
        )
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.tunnel_callback_url = tunnel_callback_url or os.getenv("AEGIS_TUNNEL_CALLBACK_URL")
        self.discovered_tunnel_url: Optional[str] = None
        self.alert_dispatcher = AlertDispatcher()

    async def init_interactive_auth(self) -> int:
        """Launch a visible browser window for one-time Google login & session capture."""
        if async_playwright is None:
            logger.error("Playwright is not installed. Please run: pip install playwright && playwright install chromium")
            return ColabStatus.ERROR

        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        logger.info("Starting interactive browser session for Google Authentication...")
        logger.info("Profile directory: %s", self.user_data_dir)

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--start-maximized",
                ],
                viewport=None,
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://accounts.google.com/", wait_until="networkidle")

            logger.info("=" * 60)
            logger.info("PLEASE LOG IN TO YOUR GOOGLE ACCOUNT IN THE BROWSER WINDOW.")
            logger.info("Once logged in and redirected to Google Colab, you can close the browser or press Ctrl+C.")
            logger.info("=" * 60)

            try:
                await page.goto(self.notebook_url, wait_until="domcontentloaded")
                # Keep open until user closes or presses Enter in console
                await page.wait_for_timeout(180_000)  # Wait up to 3 minutes
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.info("Session window closed or finished: %s", exc)
            finally:
                await context.close()

        logger.info("Authentication profile successfully saved in: %s", self.user_data_dir)
        return ColabStatus.SUCCESS

    async def run_notebook_and_watch(self) -> Tuple[ColabStatus, Optional[str]]:
        """Start Colab notebook headless, trigger Run All Cells, and monitor dialogs & output."""
        if async_playwright is None:
            logger.error("Playwright is not installed.")
            return ColabStatus.ERROR, None

        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        logger.info("Launching Colab automation [Headless=%s]...", self.headless)
        logger.info("Target Notebook: %s", self.notebook_url)

        async with async_playwright() as pw:
            context: BrowserContext = await pw.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                ],
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            page: Page = context.pages[0] if context.pages else await context.new_page()

            # Anti-detection stealth script injection
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            try:
                logger.info("Navigating to Colab Notebook...")
                await page.goto(self.notebook_url, wait_until="networkidle", timeout=60_000)
            except Exception as exc:
                logger.warning("Initial page load timed out or had warnings: %s. Continuing...", exc)

            await page.wait_for_timeout(3_000)

            # Check if login is required
            if "accounts.google.com" in page.url or await page.locator("text='Sign in'").count() > 0:
                logger.error("Google session expired or not authenticated! Run 'aegis init-auth' first.")
                self.alert_dispatcher.dispatch_alert(
                    title="Colab Authentication Expired",
                    message="Session expired. Please run 'aegis init-auth' to re-authenticate with Google.",
                    level=AlertLevel.CRITICAL,
                )
                await context.close()
                return ColabStatus.ERROR, None

            logger.info("Sending 'Run All' shortcut (Control+F9 / Meta+F9)...")
            # Connect runtime if needed or trigger Run All
            await page.keyboard.press("Control+F9")
            await page.wait_for_timeout(1_000)
            await page.keyboard.press("Meta+F9")

            # Also click 'Run anyway' dialog if warning pops up for external code
            try:
                run_anyway_btn = page.locator('text="Run anyway"').or_(page.locator('text="Trotzdem ausführen"'))
                if await run_anyway_btn.count() > 0 and await run_anyway_btn.first.is_visible():
                    logger.info("Clicking 'Run anyway' warning dialog...")
                    await run_anyway_btn.first.click()
            except Exception:
                pass

            # Monitor loop for Quota Dialogs and Tunnel URL
            start_time = asyncio.get_event_loop().time()
            logger.info("Watching notebook output and dialogs for up to %d seconds...", self.timeout_seconds)

            while (asyncio.get_event_loop().time() - start_time) < self.timeout_seconds:
                # 1. Check for modal dialogs (mwc-dialog, paper-dialog, role=dialog)
                quota_detected, dialog_text = await self._check_for_quota_dialog(page)
                if quota_detected:
                    logger.error("🚫 GPU Quota Limit Encountered! Dialog message: %s", dialog_text)
                    self.alert_dispatcher.dispatch_alert(
                        title="Colab GPU Quota Limit Exceeded",
                        message=f"Google Colab denied GPU allocation. Reason: {dialog_text[:200]}",
                        level=AlertLevel.CRITICAL,
                        extra={"dialog_snippet": dialog_text[:150]},
                    )
                    await context.close()
                    return ColabStatus.QUOTA_EXCEEDED, None

                # 2. Check for Output Text / Tunnel URL in DOM output cells
                tunnel_url = await self._scan_for_tunnel_url(page)
                if tunnel_url:
                    self.discovered_tunnel_url = tunnel_url
                    logger.info("🎉 [AEGIS_READY] Inference endpoint online: %s", tunnel_url)

                    # Notify OmniRoute hot-update endpoint if configured
                    if self.tunnel_callback_url:
                        await self._notify_omniroute_tunnel_update(tunnel_url)

                    self.alert_dispatcher.dispatch_alert(
                        title="AegisRoute Inference Ready",
                        message=f"Colab GPU LLM bridge is ready. Tunnel: {tunnel_url}",
                        level=AlertLevel.RECOVERY,
                        extra={"base_url": tunnel_url},
                    )
                    await context.close()
                    return ColabStatus.SUCCESS, tunnel_url

                await asyncio.sleep(4)

            logger.error("Timeout reached without receiving [AEGIS_READY] marker.")
            self.alert_dispatcher.dispatch_alert(
                title="Colab Startup Timeout",
                message=f"Bootstrap did not finish within {self.timeout_seconds}s.",
                level=AlertLevel.ERROR,
            )
            await context.close()
            return ColabStatus.ERROR, None

    async def _check_for_quota_dialog(self, page: Page) -> Tuple[bool, str]:
        """Inspect modal dialog elements for GPU exhaustion text."""
        dialog_selectors = [
            "mwc-dialog",
            "paper-dialog",
            "[role='dialog']",
            ".modal-dialog",
            "colab-dialog",
        ]
        for sel in dialog_selectors:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                for i in range(count):
                    el = locator.nth(i)
                    if await el.is_visible():
                        text = (await el.text_content()) or ""
                        lower_text = text.lower()
                        for kw in QUOTA_KEYWORDS:
                            if kw in lower_text:
                                return True, text.strip().replace("\n", " ")
            except Exception:
                continue
        return False, ""

    async def _scan_for_tunnel_url(self, page: Page) -> Optional[str]:
        """Scan active output cells / iframe logs for Aegis ready marker."""
        try:
            # Check entire page body innerText for speed & coverage
            body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            candidate_url = None

            match = AEGIS_READY_REGEX.search(body_text)
            if match:
                candidate_url = match.group(1).rstrip("/")
            else:
                # Fallback check for Cloudflare quick tunnel URL
                cf_match = CLOUDFLARE_URL_REGEX.search(body_text)
                if cf_match:
                    url = cf_match.group(0).rstrip("/")
                    candidate_url = f"{url}/v1"

            if candidate_url:
                # Validate that the endpoint is actually accepting HTTP traffic
                is_valid = await self._probe_endpoint_health(candidate_url)
                if is_valid:
                    return candidate_url
                else:
                    logger.debug("Candidate endpoint %s detected but not yet answering with 200 OK. Waiting...", candidate_url)
        except Exception:
            pass
        return None

    async def _probe_endpoint_health(self, base_url: str) -> bool:
        """Asynchronously probe /models endpoint with short timeout."""
        try:
            if httpx:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    res = await client.get(f"{base_url.rstrip('/')}/models")
                    return res.status_code == 200
            else:
                import urllib.request
                loop = asyncio.get_event_loop()
                def _sync_probe():
                    try:
                        req = urllib.request.Request(f"{base_url.rstrip('/')}/models")
                        with urllib.request.urlopen(req, timeout=3.0) as r:
                            return r.status == 200
                    except Exception:
                        return False
                return await loop.run_in_executor(None, _sync_probe)
        except Exception:
            return False

    async def _notify_omniroute_tunnel_update(self, tunnel_url: str) -> None:
        """Call OmniRoute's plugin admin endpoint to update the tunnel URL live."""
        if not self.tunnel_callback_url:
            return
        logger.info("Pushing tunnel URL update to OmniRoute at %s...", self.tunnel_callback_url)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    self.tunnel_callback_url,
                    json={"tunnel_url": tunnel_url, "provider": "colab-aegis"},
                )
                logger.info("OmniRoute update response: %d %s", res.status_code, res.text)
        except Exception as exc:
            logger.warning("Failed to notify OmniRoute of tunnel update: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="AegisRoute Headless Colab Controller")
    parser.add_argument("--url", type=str, default=os.getenv("AEGIS_COLAB_URL", ""), help="Google Colab Notebook URL")
    parser.add_argument("--init-auth", action="store_true", help="Launch interactive browser to log in to Google")
    parser.add_argument("--headful", action="store_true", help="Run browser visibly for debugging")
    parser.add_argument("--timeout", type=int, default=420, help="Timeout in seconds")
    parser.add_argument("--user-data", type=str, default="", help="Custom user data directory for browser profile")
    parser.add_argument("--callback", type=str, default="", help="OmniRoute update webhook URL")

    args = parser.parse_args()

    notebook_url = args.url
    if not notebook_url and not args.init_auth:
        notebook_url = os.getenv("AEGIS_COLAB_URL", "https://colab.research.google.com")

    controller = ColabPlaywrightController(
        notebook_url=notebook_url,
        user_data_dir=args.user_data or None,
        headless=not args.headful,
        timeout_seconds=args.timeout,
        tunnel_callback_url=args.callback or None,
    )

    if args.init_auth:
        return asyncio.run(controller.init_interactive_auth())

    status, tunnel = asyncio.run(controller.run_notebook_and_watch())
    if tunnel:
        print(f"AEGIS_TUNNEL_URL={tunnel}")
    sys.exit(int(status))


if __name__ == "__main__":
    sys.exit(main())
