"""Anthropic Console credit balance checker with magic link authentication."""

import email
import hashlib
import imaplib
import logging
import os
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class CreditsChecker(BaseChecker):
    """Checker for Anthropic Console credit balance.

    Uses magic link authentication via IMAP email retrieval.

    Config options:
        - threshold: Credit balance threshold for alerting (default: 5.00)
        - imap_host: IMAP server hostname (default: from IMAP_HOST env)
        - imap_user: IMAP username (default: from IMAP_USER env)
        - imap_password: IMAP password (default: from IMAP_PASSWORD env)
        - console_email: Anthropic console email (default: from ANTHROPIC_CONSOLE_EMAIL env)
        - archive_emails: Archive magic link emails after use (default: True)
        - headed: Run browser in headed mode (default: False for background checks)
    """

    def __init__(self):
        self.imap_host = os.getenv("IMAP_HOST", "imap.gmail.com")
        self.imap_user = os.getenv("IMAP_USER")
        self.imap_password = os.getenv("IMAP_PASSWORD")
        self.console_email = os.getenv("ANTHROPIC_CONSOLE_EMAIL")

    def check(self, monitor: Monitor) -> CheckResult:
        """
        Log into Anthropic Console and check credit balance.

        Args:
            monitor: Monitor with threshold config

        Returns:
            CheckResult with balance info and threshold check
        """
        # Get config
        threshold = monitor.config.get("threshold", 5.00)
        archive_emails = monitor.config.get("archive_emails", True)
        headed = monitor.config.get("headed", False)

        # Override from config if provided
        imap_host = monitor.config.get("imap_host", self.imap_host)
        imap_user = monitor.config.get("imap_user", self.imap_user)
        imap_password = monitor.config.get("imap_password", self.imap_password)
        console_email = monitor.config.get("console_email", self.console_email)

        if not all([imap_user, imap_password, console_email]):
            raise ValueError(
                "Missing required credentials. Set IMAP_USER, IMAP_PASSWORD, "
                "and ANTHROPIC_CONSOLE_EMAIL environment variables."
            )

        try:
            balance = self._login_and_get_balance(
                console_email=console_email,
                imap_host=imap_host,
                imap_user=imap_user,
                imap_password=imap_password,
                archive_emails=archive_emails,
                headed=headed,
            )

            if balance is None:
                return CheckResult(
                    condition_met=False,
                    explanation="Failed to retrieve credit balance",
                    details={"error": "Could not log in or extract balance"},
                    state_hash=None,
                )

            # Check if below threshold
            below_threshold = balance < threshold

            return CheckResult(
                condition_met=below_threshold,
                explanation=f"Credit balance: ${balance:.2f} ({'BELOW' if below_threshold else 'above'} ${threshold:.2f} threshold)",
                details={
                    "balance": balance,
                    "threshold": threshold,
                    "below_threshold": below_threshold,
                },
                state_hash=hashlib.sha256(f"{balance:.2f}".encode()).hexdigest()[:16],
            )

        except Exception as e:
            logger.error(f"Error checking Anthropic credits: {e}")
            raise

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """
        Notify when balance drops below threshold.

        Also notify if balance was previously below and is now above (recovery).
        """
        if not result.details:
            return False

        current_below = result.details.get("below_threshold", False)
        previous_below = monitor.last_state.get("details", {}).get("below_threshold", False)

        # Notify on transition: above -> below threshold
        if current_below and not previous_below:
            return True

        return False

    def get_state_for_storage(self, result: CheckResult, monitor: Monitor) -> dict[str, Any]:
        """Store balance and threshold state."""
        return {
            "condition_met": result.condition_met,
            "explanation": result.explanation,
            "details": result.details,
        }

    def _login_and_get_balance(
        self,
        console_email: str,
        imap_host: str,
        imap_user: str,
        imap_password: str,
        archive_emails: bool = True,
        headed: bool = False,
    ) -> float | None:
        """
        Log into Anthropic console using magic link and retrieve credit balance.

        Args:
            console_email: Email for Anthropic console account
            imap_host: IMAP server hostname
            imap_user: IMAP username
            imap_password: IMAP password
            archive_emails: Whether to archive magic link emails after use
            headed: Run browser in headed mode

        Returns:
            Credit balance as float, or None on failure
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "Playwright not installed. Install with: "
                "pip install playwright && playwright install chromium"
            )

        logger.info(f"Starting Anthropic credits check (headed={headed})")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Step 1: Navigate to login page
                logger.info("Navigating to Anthropic console login...")
                page.goto("https://console.anthropic.com/login", wait_until="networkidle")
                time.sleep(2)

                # Step 2: Enter email
                logger.info(f"Entering email: {console_email}")
                email_input = page.locator(
                    'input[type="email"], input[name="email"], input[placeholder*="email" i]'
                )
                email_input.fill(console_email)
                time.sleep(1)

                # Step 3: Click Continue with email
                logger.info("Clicking 'Continue with email'...")
                continue_btn = page.locator('button:has-text("Continue with email")')
                continue_btn.click()
                time.sleep(2)

                # Step 4: Click "Email me a link" if prompted
                try:
                    email_link_btn = page.locator(
                        'button:has-text("Email me a link"), button:has-text("Send link")'
                    )
                    if email_link_btn.is_visible(timeout=3000):
                        logger.info("Clicking 'Email me a link'...")
                        email_link_btn.click()
                        time.sleep(2)
                except Exception:
                    pass

                # Step 5: Get magic link from email
                logger.info("Waiting for magic link email...")
                magic_link, email_id = self._get_magic_link(
                    imap_host=imap_host,
                    imap_user=imap_user,
                    imap_password=imap_password,
                    max_wait_seconds=90,
                )

                if not magic_link:
                    logger.error("Failed to retrieve magic link from email")
                    return None

                # Step 6: Navigate to magic link
                logger.info("Navigating to magic link...")
                page.goto(magic_link, wait_until="networkidle")
                time.sleep(5)

                # Step 7: Archive the magic link email
                if archive_emails and email_id:
                    self._archive_email(
                        imap_host=imap_host,
                        imap_user=imap_user,
                        imap_password=imap_password,
                        email_id=email_id,
                    )

                # Step 8: Navigate to billing page
                logger.info("Navigating to billing page...")
                page.goto(
                    "https://console.anthropic.com/settings/billing",
                    wait_until="networkidle",
                )
                time.sleep(3)

                # Check if we're still logged in (not redirected to login page)
                current_url = page.url
                if "login" in current_url:
                    logger.error("Magic link expired or already used - redirected to login")
                    return None

                # Step 9: Extract credit balance
                logger.info("Extracting credit balance...")
                page_text = page.inner_text("body")

                # Look for credit/balance patterns
                balance_patterns = [
                    r"Credit Balance[:\s]*\$?([\d,]+\.?\d*)",
                    r"\$([\d,]+\.?\d*)\s*(?:remaining|credit|balance)",
                    r"remaining[:\s]*\$?([\d,]+\.?\d*)",
                    r"balance[:\s]*\$?([\d,]+\.?\d*)",
                    r"credits?[:\s]*\$?([\d,]+\.?\d*)",
                ]

                for pattern in balance_patterns:
                    match = re.search(pattern, page_text, re.IGNORECASE)
                    if match:
                        balance_str = match.group(1).replace(",", "")
                        balance = float(balance_str)
                        logger.info(f"Found credit balance: ${balance:.2f}")
                        return balance

                logger.warning("Could not find credit balance pattern in page")
                logger.debug(f"Page text: {page_text[:1000]}")
                return None

            except Exception as e:
                logger.error(f"Error during login: {e}")
                raise
            finally:
                browser.close()

    def _get_magic_link(
        self,
        imap_host: str,
        imap_user: str,
        imap_password: str,
        max_wait_seconds: int = 90,
    ) -> tuple[str | None, bytes | None]:
        """
        Poll IMAP inbox for Anthropic magic link.

        Args:
            imap_host: IMAP server hostname
            imap_user: IMAP username
            imap_password: IMAP password
            max_wait_seconds: Maximum time to wait for email

        Returns:
            Tuple of (magic_link_url, email_id) or (None, None) if not found
        """
        logger.info("Checking IMAP for magic link from Anthropic...")
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            try:
                mail = imaplib.IMAP4_SSL(imap_host)
                mail.login(imap_user, imap_password)
                mail.select("INBOX")

                # Search for Anthropic emails
                date_since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
                _, messages = mail.search(None, f'(FROM "anthropic" SINCE "{date_since}")')

                email_ids = messages[0].split()

                if email_ids:
                    for email_id in reversed(email_ids):
                        _, msg_data = mail.fetch(email_id, "(RFC822)")

                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                subject = msg.get("Subject", "")
                                date_str = msg.get("Date", "")

                                # Check if this is a login email
                                if "secure link" not in subject.lower() and "log in" not in subject.lower():
                                    continue

                                # Parse email date to check freshness
                                try:
                                    email_date = parsedate_to_datetime(date_str)
                                    email_age = (
                                        datetime.now(email_date.tzinfo) - email_date
                                    ).total_seconds()

                                    # Skip emails older than 2 minutes
                                    if email_age > 120:
                                        continue

                                    logger.info(f"Found recent login email ({int(email_age)}s old)")
                                except Exception as e:
                                    logger.debug(f"Could not parse date: {e}")
                                    continue

                                # Get the email body
                                body = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() in ["text/html", "text/plain"]:
                                            payload = part.get_payload(decode=True)
                                            if payload:
                                                body += payload.decode("utf-8", errors="replace")
                                else:
                                    payload = msg.get_payload(decode=True)
                                    if payload:
                                        body = payload.decode("utf-8", errors="replace")

                                # Find magic link
                                magic_link_match = re.search(
                                    r'href="(https://platform\.claude\.com/magic-link[^"]+)"',
                                    body,
                                )
                                if magic_link_match:
                                    magic_link = magic_link_match.group(1)
                                    logger.info("Found magic link!")
                                    mail.logout()
                                    return magic_link, email_id

                mail.logout()

            except Exception as e:
                logger.warning(f"IMAP error: {e}")

            elapsed = int(time.time() - start_time)
            logger.info(f"No magic link yet, waiting... ({elapsed}s)")
            time.sleep(3)

        logger.warning("Timeout waiting for magic link email")
        return None, None

    def _archive_email(
        self,
        imap_host: str,
        imap_user: str,
        imap_password: str,
        email_id: bytes,
    ) -> bool:
        """
        Archive (move to trash or delete) the magic link email.

        Args:
            imap_host: IMAP server hostname
            imap_user: IMAP username
            imap_password: IMAP password
            email_id: Email ID to archive

        Returns:
            True if successfully archived
        """
        try:
            mail = imaplib.IMAP4_SSL(imap_host)
            mail.login(imap_user, imap_password)
            mail.select("INBOX")

            # Try to move to trash (Gmail uses "[Gmail]/Trash")
            # For other providers, we just mark as deleted
            try:
                # Gmail-specific: copy to Trash folder
                mail.copy(email_id, "[Gmail]/Trash")
                logger.info("Moved magic link email to Trash")
            except Exception:
                # Fallback: mark as deleted
                mail.store(email_id, "+FLAGS", "\\Deleted")
                logger.info("Marked magic link email as deleted")

            mail.expunge()
            mail.logout()
            return True

        except Exception as e:
            logger.warning(f"Failed to archive email: {e}")
            return False
