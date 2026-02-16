#!/usr/bin/env python3
"""
Prototype: Anthropic Console Credit Balance Monitor

Logs into console.anthropic.com using magic link authentication,
retrieves credit balance, and can alert when below threshold.

Uses Playwright for browser control + IMAP for magic link retrieval.
"""

import email
import imaplib
import os
import re
import sys
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv

# Load environment variables
load_dotenv("/Users/mooney/NotifyMe/.env")

# Configuration from environment
ANTHROPIC_EMAIL = os.getenv("ANTHROPIC_CONSOLE_EMAIL")
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")


def get_magic_link(max_wait_seconds: int = 90) -> str | None:
    """
    Poll IMAP inbox for Anthropic magic link.

    Args:
        max_wait_seconds: Maximum time to wait for email

    Returns:
        Magic link URL or None if not found
    """
    print(f"Checking IMAP for magic link from Anthropic...")
    start_time = time.time()
    request_time = datetime.now()

    while time.time() - start_time < max_wait_seconds:
        try:
            # Connect to IMAP
            mail = imaplib.IMAP4_SSL(IMAP_HOST)
            mail.login(IMAP_USER, IMAP_PASSWORD)
            mail.select("INBOX")

            # Search for Anthropic emails from today
            date_since = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
            _, messages = mail.search(None, f'(FROM "anthropic" SINCE "{date_since}")')

            email_ids = messages[0].split()

            if email_ids:
                # Check emails from newest to oldest
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
                                from email.utils import parsedate_to_datetime
                                email_date = parsedate_to_datetime(date_str)
                                email_age = (datetime.now(email_date.tzinfo) - email_date).total_seconds()

                                # Skip emails older than 2 minutes
                                if email_age > 120:
                                    continue

                                print(f"Found recent login email ({int(email_age)}s old)")
                            except Exception as e:
                                print(f"Could not parse date: {e}")
                                continue

                            # Get the email body
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() in ["text/html", "text/plain"]:
                                        payload = part.get_payload(decode=True)
                                        if payload:
                                            body += payload.decode('utf-8', errors='replace')
                            else:
                                payload = msg.get_payload(decode=True)
                                if payload:
                                    body = payload.decode('utf-8', errors='replace')

                            # Find magic link
                            magic_link_match = re.search(
                                r'href="(https://platform\.claude\.com/magic-link[^"]+)"',
                                body
                            )
                            if magic_link_match:
                                magic_link = magic_link_match.group(1)
                                print(f"Found magic link!")
                                mail.logout()
                                return magic_link

            mail.logout()

        except Exception as e:
            print(f"IMAP error: {e}")

        print(f"No magic link yet, waiting... ({int(time.time() - start_time)}s)")
        time.sleep(3)

    print("Timeout waiting for magic link email")
    return None


def login_and_get_credits(headed: bool = True) -> float | None:
    """
    Log into Anthropic console and retrieve credit balance.

    Args:
        headed: Run browser in headed mode (visible) for debugging

    Returns:
        Credit balance as float, or None on failure
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    print(f"Starting browser (headed={headed})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Step 1: Navigate to login page
            print("Navigating to Anthropic console login...")
            page.goto("https://console.anthropic.com/login", wait_until="networkidle")
            time.sleep(2)

            # Step 2: Enter email
            print(f"Entering email: {ANTHROPIC_EMAIL}")
            email_input = page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i]')
            email_input.fill(ANTHROPIC_EMAIL)
            time.sleep(1)

            # Step 3: Click Continue with email button
            print("Clicking 'Continue with email'...")
            continue_btn = page.locator('button:has-text("Continue with email")')
            continue_btn.click()
            time.sleep(2)

            # Step 4: Click "Email me a link" or similar (if prompted)
            try:
                email_link_btn = page.locator('button:has-text("Email me a link"), button:has-text("Send link")')
                if email_link_btn.is_visible(timeout=3000):
                    print("Clicking 'Email me a link'...")
                    email_link_btn.click()
                    time.sleep(2)
            except Exception:
                pass

            # Step 5: Get magic link from email
            print("Waiting for magic link email...")
            magic_link = get_magic_link(max_wait_seconds=90)

            if not magic_link:
                print("Failed to retrieve magic link")
                return None

            # Step 6: Navigate to magic link
            print(f"Navigating to magic link...")
            page.goto(magic_link, wait_until="networkidle")
            time.sleep(5)

            # Step 7: Check if we're logged in - try to navigate to billing
            print("Navigating to billing page...")
            page.goto("https://console.anthropic.com/settings/billing", wait_until="networkidle")
            time.sleep(3)

            # Step 8: Extract credit balance
            print("Extracting credit balance...")
            page_text = page.inner_text("body")

            # Debug: print page URL and text
            print(f"Current URL: {page.url}")

            # Look for credit/balance patterns
            balance_patterns = [
                r'Credit Balance[:\s]*\$?([\d,]+\.?\d*)',  # Credit Balance: $12.34
                r'\$([\d,]+\.?\d*)\s*(?:remaining|credit|balance)',  # $12.34 remaining
                r'remaining[:\s]*\$?([\d,]+\.?\d*)',  # remaining: $12.34
                r'balance[:\s]*\$?([\d,]+\.?\d*)',  # balance: $12.34
                r'credits?[:\s]*\$?([\d,]+\.?\d*)',  # credits: $12.34
            ]

            for pattern in balance_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    balance_str = match.group(1).replace(',', '')
                    balance = float(balance_str)
                    print(f"Found credit balance: ${balance:.2f}")

                    # Take screenshot for verification
                    screenshot_path = "/tmp/anthropic_credits.png"
                    page.screenshot(path=screenshot_path)
                    print(f"Screenshot saved: {screenshot_path}")

                    return balance

            # Try to find any dollar amounts
            dollar_amounts = re.findall(r'\$([\d,]+\.?\d*)', page_text)
            if dollar_amounts:
                print(f"Found dollar amounts: {dollar_amounts}")

            # If no balance found, save debug info
            print("Could not find credit balance pattern in page")
            print(f"Page text (first 3000 chars):\n{page_text[:3000]}")
            screenshot_path = "/tmp/anthropic_credits_debug.png"
            page.screenshot(path=screenshot_path)
            print(f"Debug screenshot saved: {screenshot_path}")

            return None

        except Exception as e:
            print(f"Error during login: {e}")
            import traceback
            traceback.print_exc()
            # Save error screenshot
            try:
                page.screenshot(path="/tmp/anthropic_error.png")
                print("Error screenshot saved: /tmp/anthropic_error.png")
            except Exception:
                pass
            raise
        finally:
            browser.close()


def main():
    """Main entry point."""
    print("=" * 60)
    print("Anthropic Console Credit Balance Monitor - Prototype")
    print("=" * 60)
    print()

    if not all([ANTHROPIC_EMAIL, IMAP_USER, IMAP_PASSWORD]):
        print("Missing environment variables. Ensure these are set in .env:")
        print("  ANTHROPIC_CONSOLE_EMAIL")
        print("  IMAP_USER")
        print("  IMAP_PASSWORD")
        sys.exit(1)

    print(f"Anthropic Email: {ANTHROPIC_EMAIL}")
    print(f"IMAP User: {IMAP_USER}")
    print()

    # Run with headed browser for debugging
    balance = login_and_get_credits(headed=True)

    if balance is not None:
        print()
        print("=" * 60)
        print(f"Credit Balance: ${balance:.2f}")
        print("=" * 60)

        # Check threshold
        threshold = 1.00
        if balance < threshold:
            print(f"WARNING: Balance ${balance:.2f} is below threshold ${threshold:.2f}!")
        else:
            print(f"Balance is above threshold ${threshold:.2f}")
    else:
        print()
        print("Failed to retrieve credit balance")
        sys.exit(1)


if __name__ == "__main__":
    main()
