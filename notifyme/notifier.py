"""Email notification system."""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from dotenv import load_dotenv

from .models import CheckResult, Monitor, NotificationLog

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class EmailNotifier:
    """Send email notifications via SMTP."""

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        notify_email: str | None = None,
    ):
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD")
        self.notify_email = notify_email or os.getenv("NOTIFY_EMAIL")

        if not self.smtp_user:
            logger.warning("SMTP_USER not configured - emails will not be sent")
        if not self.notify_email:
            logger.warning("NOTIFY_EMAIL not configured - emails will not be sent")

    def send(
        self,
        monitor: Monitor,
        result: CheckResult,
        dry_run: bool = False,
    ) -> NotificationLog:
        """
        Send notification email for a triggered monitor.

        Args:
            monitor: The monitor that triggered
            result: The check result
            dry_run: If True, log but don't actually send

        Returns:
            NotificationLog record
        """
        subject = f"[NotifyMe] {monitor.name}"
        body = self._format_body(monitor, result)

        if dry_run:
            logger.info(f"[DRY RUN] Would send email:\n  To: {self.notify_email}\n  Subject: {subject}")
            logger.info(f"  Body:\n{body}")
        else:
            self._send_email(subject, body)

        # Ensure details is a dict
        result_details = result.details if isinstance(result.details, dict) else {}

        return NotificationLog(
            monitor_id=monitor.id,
            message=result.explanation,
            details={
                "subject": subject,
                "dry_run": dry_run,
                **result_details,
            },
        )

    def _format_body(self, monitor: Monitor, result: CheckResult) -> str:
        """Format email body based on monitor type and result."""
        lines = [
            f"Monitor: {monitor.name}",
            f"Type: {monitor.type.value}",
            f"URL: {monitor.url}",
            "",
            f"Status: {'CONDITION MET' if result.condition_met else 'Condition not met'}",
            f"Explanation: {result.explanation}",
        ]

        # Add details if present
        if result.details:
            lines.append("")
            lines.append("Details:")
            if isinstance(result.details, dict):
                for key, value in result.details.items():
                    lines.append(f"  {key}: {value}")
            else:
                lines.append(f"  {result.details}")

        # Special handling for news monitors
        if result.new_items:
            lines.append("")
            lines.append(f"New Articles ({len(result.new_items)}):")
            lines.append("-" * 40)
            for item in result.new_items[:10]:  # Limit to 10 articles
                lines.append(f"\n{item.get('title', 'No title')}")
                if item.get("source"):
                    lines.append(f"  Source: {item['source']}")
                if item.get("link"):
                    lines.append(f"  Link: {item['link']}")
                if item.get("published"):
                    lines.append(f"  Published: {item['published']}")

            if len(result.new_items) > 10:
                lines.append(f"\n... and {len(result.new_items) - 10} more articles")

        lines.append("")
        lines.append("-" * 40)
        lines.append("Sent by NotifyMe")

        return "\n".join(lines)

    def _send_email(self, subject: str, body: str) -> None:
        """Send email via SMTP."""
        if not self.smtp_user or not self.smtp_password or not self.notify_email:
            raise ValueError(
                "Email not configured. Set SMTP_USER, SMTP_PASSWORD, and NOTIFY_EMAIL environment variables."
            )

        msg = MIMEMultipart()
        msg["From"] = self.smtp_user
        msg["To"] = self.notify_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
                logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            raise

    def test_connection(self) -> bool:
        """Test SMTP connection."""
        if not self.smtp_user or not self.smtp_password:
            return False

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                return True
        except Exception as e:
            logger.error(f"SMTP connection test failed: {e}")
            return False
