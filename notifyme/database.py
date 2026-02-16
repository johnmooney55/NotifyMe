"""SQLite database operations for NotifyMe."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Monitor, NotificationLog


DEFAULT_DB_PATH = Path.home() / ".notifyme" / "notifyme.db"


class Database:
    """SQLite database wrapper for NotifyMe."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS monitors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    url TEXT NOT NULL,
                    config TEXT,
                    check_interval_minutes INTEGER DEFAULT 60,
                    condition TEXT,
                    last_checked TEXT,
                    last_state TEXT,
                    last_state_hash TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notifications_log (
                    id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    sent_at TEXT NOT NULL,
                    FOREIGN KEY (monitor_id) REFERENCES monitors(id)
                );

                CREATE INDEX IF NOT EXISTS idx_monitors_active ON monitors(is_active);
                CREATE INDEX IF NOT EXISTS idx_monitors_last_checked ON monitors(last_checked);
                CREATE INDEX IF NOT EXISTS idx_notifications_monitor ON notifications_log(monitor_id);
                CREATE INDEX IF NOT EXISTS idx_notifications_sent ON notifications_log(sent_at);
            """)

    # Monitor operations

    def add_monitor(self, monitor: Monitor) -> Monitor:
        """Add a new monitor."""
        with self._get_connection() as conn:
            data = monitor.to_dict()
            conn.execute(
                """
                INSERT INTO monitors (
                    id, name, type, url, config, check_interval_minutes,
                    condition, last_checked, last_state, last_state_hash,
                    is_active, created_at, updated_at
                ) VALUES (
                    :id, :name, :type, :url, :config, :check_interval_minutes,
                    :condition, :last_checked, :last_state, :last_state_hash,
                    :is_active, :created_at, :updated_at
                )
                """,
                data,
            )
        return monitor

    def get_monitor(self, monitor_id: str) -> Monitor | None:
        """Get a monitor by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM monitors WHERE id = ?", (monitor_id,)
            ).fetchone()
            return Monitor.from_dict(dict(row)) if row else None

    def get_monitor_by_name(self, name: str) -> Monitor | None:
        """Get a monitor by name (case-insensitive)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM monitors WHERE LOWER(name) = LOWER(?)", (name,)
            ).fetchone()
            return Monitor.from_dict(dict(row)) if row else None

    def list_monitors(self, active_only: bool = False) -> list[Monitor]:
        """List all monitors."""
        with self._get_connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM monitors WHERE is_active = 1 ORDER BY created_at"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM monitors ORDER BY created_at"
                ).fetchall()
            return [Monitor.from_dict(dict(row)) for row in rows]

    def get_monitors_due_for_check(self) -> list[Monitor]:
        """Get monitors that are due for checking based on their interval."""
        # Use Python datetime for consistent timezone handling
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM monitors
                WHERE is_active = 1
                AND (
                    last_checked IS NULL
                    OR datetime(last_checked, '+' || check_interval_minutes || ' minutes') <= datetime(?)
                )
                ORDER BY last_checked NULLS FIRST
                """,
                (now,),
            ).fetchall()
            return [Monitor.from_dict(dict(row)) for row in rows]

    def update_monitor(self, monitor: Monitor) -> None:
        """Update an existing monitor."""
        monitor.updated_at = datetime.now()
        with self._get_connection() as conn:
            data = monitor.to_dict()
            conn.execute(
                """
                UPDATE monitors SET
                    name = :name,
                    type = :type,
                    url = :url,
                    config = :config,
                    check_interval_minutes = :check_interval_minutes,
                    condition = :condition,
                    last_checked = :last_checked,
                    last_state = :last_state,
                    last_state_hash = :last_state_hash,
                    is_active = :is_active,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                data,
            )

    def delete_monitor(self, monitor_id: str) -> bool:
        """Delete a monitor and its notification history."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM notifications_log WHERE monitor_id = ?", (monitor_id,))
            result = conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
            return result.rowcount > 0

    def set_monitor_active(self, monitor_id: str, active: bool) -> bool:
        """Set monitor active status (pause/resume)."""
        with self._get_connection() as conn:
            result = conn.execute(
                "UPDATE monitors SET is_active = ?, updated_at = ? WHERE id = ?",
                (active, datetime.now().isoformat(), monitor_id),
            )
            return result.rowcount > 0

    # Notification operations

    def add_notification(self, notification: NotificationLog) -> NotificationLog:
        """Log a sent notification."""
        with self._get_connection() as conn:
            data = notification.to_dict()
            conn.execute(
                """
                INSERT INTO notifications_log (id, monitor_id, message, details, sent_at)
                VALUES (:id, :monitor_id, :message, :details, :sent_at)
                """,
                data,
            )
        return notification

    def get_notifications(
        self, monitor_id: str | None = None, limit: int = 50
    ) -> list[NotificationLog]:
        """Get notification history."""
        with self._get_connection() as conn:
            if monitor_id:
                rows = conn.execute(
                    """
                    SELECT * FROM notifications_log
                    WHERE monitor_id = ?
                    ORDER BY sent_at DESC
                    LIMIT ?
                    """,
                    (monitor_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM notifications_log
                    ORDER BY sent_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [NotificationLog.from_dict(dict(row)) for row in rows]

    def get_last_notification(self, monitor_id: str) -> NotificationLog | None:
        """Get the most recent notification for a monitor."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM notifications_log
                WHERE monitor_id = ?
                ORDER BY sent_at DESC
                LIMIT 1
                """,
                (monitor_id,),
            ).fetchone()
            return NotificationLog.from_dict(dict(row)) if row else None
