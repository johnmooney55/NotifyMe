"""Data models for NotifyMe."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import json
import uuid


class MonitorType(str, Enum):
    WEBPAGE = "webpage"
    PRICE = "price"
    RSS = "rss"
    API = "api"
    AGENTIC = "agentic"
    NEWS = "news"
    CREDITS = "credits"


@dataclass
class Monitor:
    """Represents a monitoring target."""

    name: str
    type: MonitorType
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: dict[str, Any] = field(default_factory=dict)
    check_interval_minutes: int = 60
    condition: str | None = None  # For agentic monitors
    last_checked: datetime | None = None
    last_state: dict[str, Any] = field(default_factory=dict)
    last_state_hash: str | None = None
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "url": self.url,
            "config": json.dumps(self.config),
            "check_interval_minutes": self.check_interval_minutes,
            "condition": self.condition,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "last_state": json.dumps(self.last_state),
            "last_state_hash": self.last_state_hash,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Monitor":
        """Create from dictionary (database row)."""
        return cls(
            id=data["id"],
            name=data["name"],
            type=MonitorType(data["type"]),
            url=data["url"],
            config=json.loads(data["config"]) if data["config"] else {},
            check_interval_minutes=data["check_interval_minutes"],
            condition=data["condition"],
            last_checked=datetime.fromisoformat(data["last_checked"]) if data["last_checked"] else None,
            last_state=json.loads(data["last_state"]) if data["last_state"] else {},
            last_state_hash=data["last_state_hash"],
            is_active=bool(data["is_active"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class NotificationLog:
    """Record of a sent notification."""

    monitor_id: str
    message: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    details: dict[str, Any] = field(default_factory=dict)
    sent_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "monitor_id": self.monitor_id,
            "message": self.message,
            "details": json.dumps(self.details),
            "sent_at": self.sent_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotificationLog":
        """Create from dictionary (database row)."""
        return cls(
            id=data["id"],
            monitor_id=data["monitor_id"],
            message=data["message"],
            details=json.loads(data["details"]) if data["details"] else {},
            sent_at=datetime.fromisoformat(data["sent_at"]),
        )


@dataclass
class CheckResult:
    """Result of checking a monitor."""

    condition_met: bool
    explanation: str
    details: dict[str, Any] = field(default_factory=dict)
    state_hash: str | None = None
    new_items: list[dict[str, Any]] = field(default_factory=list)  # For news/RSS monitors
