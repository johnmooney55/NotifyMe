"""Command-line interface for NotifyMe."""

import json
import logging
import sys
from datetime import datetime

import click
from dotenv import load_dotenv

from .database import Database
from .models import Monitor, MonitorType
from .notifier import EmailNotifier
from .scheduler import CheckOrchestrator

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.group()
@click.option("--db", "db_path", help="Database path (default: ~/.notifyme/notifyme.db)")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx: click.Context, db_path: str | None, verbose: bool) -> None:
    """NotifyMe - Agentic Monitoring & Notification System"""
    ctx.ensure_object(dict)
    ctx.obj["db"] = Database(db_path) if db_path else Database()

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.option("--name", "-n", required=True, help="Monitor name")
@click.option(
    "--type", "-t", "monitor_type",
    required=True,
    type=click.Choice(["agentic", "news", "webpage", "price", "rss", "api"]),
    help="Monitor type",
)
@click.option("--url", "-u", required=True, help="URL to monitor")
@click.option("--condition", "-c", help="Condition for agentic monitors")
@click.option("--interval", "-i", default=60, help="Check interval in minutes (default: 60)")
@click.option("--selector", "-s", help="CSS selector for price/webpage monitors")
@click.option("--threshold", type=float, help="Price threshold for price monitors")
@click.option("--playwright", is_flag=True, help="Use Playwright for JS-rendered pages")
@click.pass_context
def add(
    ctx: click.Context,
    name: str,
    monitor_type: str,
    url: str,
    condition: str | None,
    interval: int,
    selector: str | None,
    threshold: float | None,
    playwright: bool,
) -> None:
    """Add a new monitor."""
    db: Database = ctx.obj["db"]

    # Validate type-specific requirements
    mtype = MonitorType(monitor_type)

    if mtype == MonitorType.AGENTIC and not condition:
        raise click.ClickException("Agentic monitors require --condition")

    if mtype == MonitorType.PRICE:
        if not selector:
            raise click.ClickException("Price monitors require --selector")
        if threshold is None:
            raise click.ClickException("Price monitors require --threshold")

    # Build config
    config = {}
    if selector:
        config["selector"] = selector
    if threshold is not None:
        config["threshold"] = threshold
    if playwright:
        config["use_playwright"] = True

    # Create monitor
    monitor = Monitor(
        name=name,
        type=mtype,
        url=url,
        condition=condition,
        check_interval_minutes=interval,
        config=config,
    )

    db.add_monitor(monitor)
    click.echo(f"Added monitor: {name}")
    click.echo(f"  ID: {monitor.id}")
    click.echo(f"  Type: {monitor_type}")
    click.echo(f"  URL: {url}")
    click.echo(f"  Interval: {interval} minutes")
    if condition:
        click.echo(f"  Condition: {condition}")


@cli.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include inactive monitors")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def list_monitors(ctx: click.Context, show_all: bool, as_json: bool) -> None:
    """List all monitors."""
    db: Database = ctx.obj["db"]
    monitors = db.list_monitors(active_only=not show_all)

    if as_json:
        click.echo(json.dumps([m.to_dict() for m in monitors], indent=2))
        return

    if not monitors:
        click.echo("No monitors found. Add one with: notifyme add --help")
        return

    click.echo(f"\nMonitors ({len(monitors)}):\n")
    for m in monitors:
        status = "ACTIVE" if m.is_active else "PAUSED"
        last_check = m.last_checked.strftime("%Y-%m-%d %H:%M") if m.last_checked else "never"
        condition_met = m.last_state.get("condition_met", "N/A")

        click.echo(f"  [{m.id[:8]}] {m.name}")
        click.echo(f"    Type: {m.type.value} | Status: {status} | Interval: {m.check_interval_minutes}m")
        click.echo(f"    URL: {m.url}")
        click.echo(f"    Last check: {last_check} | Condition met: {condition_met}")
        if m.condition:
            click.echo(f"    Condition: {m.condition[:60]}...")
        click.echo()


@cli.command()
@click.argument("monitor_id", required=False)
@click.option("--all", "check_all", is_flag=True, help="Check all monitors (ignore schedule)")
@click.option("--dry-run", is_flag=True, help="Don't send actual notifications")
@click.pass_context
def check(ctx: click.Context, monitor_id: str | None, check_all: bool, dry_run: bool) -> None:
    """Check monitors and send notifications if conditions are met."""
    db: Database = ctx.obj["db"]
    orchestrator = CheckOrchestrator(db=db, dry_run=dry_run)

    def on_result(monitor: Monitor, result) -> None:
        status = "MET" if result.condition_met else "not met"
        click.echo(f"  [{monitor.name}] Condition {status}: {result.explanation}")
        if result.new_items:
            click.echo(f"    New items: {len(result.new_items)}")

    if monitor_id:
        # Check specific monitor
        monitor = db.get_monitor(monitor_id)
        if not monitor:
            # Try by name
            monitor = db.get_monitor_by_name(monitor_id)
        if not monitor:
            raise click.ClickException(f"Monitor not found: {monitor_id}")

        click.echo(f"Checking monitor: {monitor.name}")
        try:
            result = orchestrator.check_monitor(monitor, on_result)
        except Exception as e:
            raise click.ClickException(f"Check failed: {e}")

    elif check_all:
        # Check all active monitors
        click.echo("Checking all active monitors...")
        results = orchestrator.check_all(on_result)
        click.echo(f"\nChecked {len(results)} monitor(s)")

    else:
        # Check only due monitors
        click.echo("Checking monitors due for check...")
        results = orchestrator.check_all_due(on_result)
        if not results:
            click.echo("No monitors due for checking")
        else:
            click.echo(f"\nChecked {len(results)} monitor(s)")


@cli.command()
@click.option("--monitor", "-m", "monitor_id", help="Filter by monitor ID or name")
@click.option("--limit", "-n", default=20, help="Number of entries to show (default: 20)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def history(ctx: click.Context, monitor_id: str | None, limit: int, as_json: bool) -> None:
    """View notification history."""
    db: Database = ctx.obj["db"]

    # Resolve monitor ID if provided
    resolved_id = None
    if monitor_id:
        monitor = db.get_monitor(monitor_id) or db.get_monitor_by_name(monitor_id)
        if monitor:
            resolved_id = monitor.id
        else:
            raise click.ClickException(f"Monitor not found: {monitor_id}")

    notifications = db.get_notifications(monitor_id=resolved_id, limit=limit)

    if as_json:
        click.echo(json.dumps([n.to_dict() for n in notifications], indent=2))
        return

    if not notifications:
        click.echo("No notifications found")
        return

    click.echo(f"\nNotification History (last {limit}):\n")
    for n in notifications:
        monitor = db.get_monitor(n.monitor_id)
        monitor_name = monitor.name if monitor else n.monitor_id[:8]
        click.echo(f"  [{n.sent_at.strftime('%Y-%m-%d %H:%M')}] {monitor_name}")
        click.echo(f"    {n.message}")
        click.echo()


@cli.command()
@click.argument("monitor_id")
@click.pass_context
def pause(ctx: click.Context, monitor_id: str) -> None:
    """Pause a monitor (stop checking)."""
    db: Database = ctx.obj["db"]

    monitor = db.get_monitor(monitor_id) or db.get_monitor_by_name(monitor_id)
    if not monitor:
        raise click.ClickException(f"Monitor not found: {monitor_id}")

    db.set_monitor_active(monitor.id, False)
    click.echo(f"Paused monitor: {monitor.name}")


@cli.command()
@click.argument("monitor_id")
@click.pass_context
def resume(ctx: click.Context, monitor_id: str) -> None:
    """Resume a paused monitor."""
    db: Database = ctx.obj["db"]

    monitor = db.get_monitor(monitor_id) or db.get_monitor_by_name(monitor_id)
    if not monitor:
        raise click.ClickException(f"Monitor not found: {monitor_id}")

    db.set_monitor_active(monitor.id, True)
    click.echo(f"Resumed monitor: {monitor.name}")


@cli.command()
@click.argument("monitor_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.pass_context
def remove(ctx: click.Context, monitor_id: str, force: bool) -> None:
    """Remove a monitor and its history."""
    db: Database = ctx.obj["db"]

    monitor = db.get_monitor(monitor_id) or db.get_monitor_by_name(monitor_id)
    if not monitor:
        raise click.ClickException(f"Monitor not found: {monitor_id}")

    if not force:
        click.confirm(f"Remove monitor '{monitor.name}' and all its history?", abort=True)

    db.delete_monitor(monitor.id)
    click.echo(f"Removed monitor: {monitor.name}")


@cli.command("test-email")
@click.pass_context
def test_email(ctx: click.Context) -> None:
    """Test email configuration."""
    notifier = EmailNotifier()

    click.echo("Testing email configuration...")
    click.echo(f"  SMTP Host: {notifier.smtp_host}")
    click.echo(f"  SMTP Port: {notifier.smtp_port}")
    click.echo(f"  SMTP User: {notifier.smtp_user or '(not set)'}")
    click.echo(f"  Notify Email: {notifier.notify_email or '(not set)'}")

    if notifier.test_connection():
        click.echo("\nSMTP connection successful!")
    else:
        click.echo("\nSMTP connection failed. Check your settings.")
        sys.exit(1)


@cli.command("install-scheduler")
@click.option("--interval", "-i", default=5, help="Check interval in minutes (default: 5)")
@click.pass_context
def install_scheduler(ctx: click.Context, interval: int) -> None:
    """Generate and install launchd plist for background checking."""
    import os
    from pathlib import Path

    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = plist_dir / "com.notifyme.checker.plist"

    # Find the notifyme executable
    notifyme_path = sys.executable.replace("python", "notifyme")
    if not os.path.exists(notifyme_path):
        # Fall back to module invocation
        notifyme_path = f"{sys.executable} -m notifyme.cli"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.notifyme.checker</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>notifyme.cli</string>
        <string>check</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval * 60}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.notifyme/scheduler.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.notifyme/scheduler-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""

    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)

    click.echo(f"Created launchd plist: {plist_path}")
    click.echo(f"\nTo enable the scheduler, run:")
    click.echo(f"  launchctl load {plist_path}")
    click.echo(f"\nTo disable:")
    click.echo(f"  launchctl unload {plist_path}")
    click.echo(f"\nLogs will be written to:")
    click.echo(f"  ~/.notifyme/scheduler.log")


if __name__ == "__main__":
    cli()
