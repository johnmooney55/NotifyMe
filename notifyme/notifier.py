"""Email notification system."""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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
        html_body = self._format_html_body(monitor, result)
        text_body = self._format_text_body(monitor, result)

        if dry_run:
            logger.info(f"[DRY RUN] Would send email:\n  To: {self.notify_email}\n  Subject: {subject}")
            logger.info(f"  Body:\n{text_body}")
        else:
            self._send_email(subject, html_body, text_body)

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

    def _format_html_body(self, monitor: Monitor, result: CheckResult) -> str:
        """Format email body as HTML."""
        status_color = "#28a745" if result.condition_met else "#dc3545"
        status_text = "CONDITION MET" if result.condition_met else "Condition not met"

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.5; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
        .header h2 {{ margin: 0 0 10px 0; color: #333; }}
        .status {{ display: inline-block; padding: 4px 12px; border-radius: 4px; color: white; background: {status_color}; font-weight: 500; font-size: 14px; }}
        .meta {{ color: #666; font-size: 14px; margin-top: 10px; }}
        .meta a {{ color: #0066cc; }}
        .explanation {{ background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid {status_color}; }}
        .articles {{ margin-top: 20px; }}
        .article {{ background: #fff; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px; margin-bottom: 12px; }}
        .article-title {{ font-weight: 600; color: #333; text-decoration: none; font-size: 15px; }}
        .article-title:hover {{ color: #0066cc; }}
        .article-meta {{ color: #666; font-size: 13px; margin-top: 6px; }}
        .article-source {{ color: #28a745; font-weight: 500; }}
        .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #e9ecef; color: #999; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>{monitor.name}</h2>
        <span class="status">{status_text}</span>
        <div class="meta">
            Type: {monitor.type.value} &bull;
            <a href="{monitor.url}">View Source</a>
        </div>
    </div>

    <div class="explanation">
        {result.explanation}
    </div>
"""

        # Add details if present (for agentic monitors)
        if result.details and isinstance(result.details, dict):
            details_to_show = {k: v for k, v in result.details.items()
                            if k not in ('event_id', 'feed_title') and v}
            if details_to_show:
                html += '<div class="details"><strong>Details:</strong><ul>'
                for key, value in details_to_show.items():
                    html += f"<li><strong>{key}:</strong> {value}</li>"
                html += "</ul></div>"

        # Add articles for news monitors
        if result.new_items:
            html += f'<div class="articles"><h3>New Articles ({len(result.new_items)})</h3>'

            for item in result.new_items[:15]:  # Show up to 15 articles
                title = item.get('title', 'No title')
                link = item.get('link', '#')
                source = item.get('source', 'Unknown')
                published = item.get('published', '')

                # Clean up the title (remove source suffix if present)
                if ' - ' in title and title.endswith(source):
                    title = title.rsplit(' - ', 1)[0]

                html += f"""
                <div class="article">
                    <a href="{link}" class="article-title">{title}</a>
                    <div class="article-meta">
                        <span class="article-source">{source}</span>
                        {f' &bull; {published}' if published else ''}
                    </div>
                </div>
"""

            if len(result.new_items) > 15:
                html += f'<p style="color: #666;">...and {len(result.new_items) - 15} more articles</p>'

            html += "</div>"

        html += """
    <div class="footer">
        Sent by NotifyMe
    </div>
</body>
</html>
"""
        return html

    def _format_text_body(self, monitor: Monitor, result: CheckResult) -> str:
        """Format email body as plain text (fallback)."""
        lines = [
            f"Monitor: {monitor.name}",
            f"Type: {monitor.type.value}",
            f"URL: {monitor.url}",
            "",
            f"Status: {'CONDITION MET' if result.condition_met else 'Condition not met'}",
            f"Explanation: {result.explanation}",
        ]

        if result.details and isinstance(result.details, dict):
            details_to_show = {k: v for k, v in result.details.items()
                            if k not in ('event_id', 'feed_title') and v}
            if details_to_show:
                lines.append("")
                lines.append("Details:")
                for key, value in details_to_show.items():
                    lines.append(f"  {key}: {value}")

        if result.new_items:
            lines.append("")
            lines.append(f"New Articles ({len(result.new_items)}):")
            lines.append("-" * 40)
            for item in result.new_items[:10]:
                title = item.get('title', 'No title')
                source = item.get('source', '')
                link = item.get('link', '')

                if ' - ' in title and source and title.endswith(source):
                    title = title.rsplit(' - ', 1)[0]

                lines.append(f"\n* {title}")
                if source:
                    lines.append(f"  Source: {source}")
                if link:
                    lines.append(f"  {link}")

            if len(result.new_items) > 10:
                lines.append(f"\n... and {len(result.new_items) - 10} more articles")

        lines.append("")
        lines.append("-" * 40)
        lines.append("Sent by NotifyMe")

        return "\n".join(lines)

    def _send_email(self, subject: str, html_body: str, text_body: str) -> None:
        """Send email via SMTP with HTML and plain text versions."""
        if not self.smtp_user or not self.smtp_password or not self.notify_email:
            raise ValueError(
                "Email not configured. Set SMTP_USER, SMTP_PASSWORD, and NOTIFY_EMAIL environment variables."
            )

        msg = MIMEMultipart("alternative")
        msg["From"] = self.smtp_user
        msg["To"] = self.notify_email
        msg["Subject"] = subject

        # Attach plain text first, then HTML (email clients prefer the last one)
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

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
