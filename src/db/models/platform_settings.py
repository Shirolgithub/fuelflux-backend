# src/db/models/platform_settings.py
# Single-document — platform-wide configuration set by admin.

from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class PlatformSettings(Document):
    """Singleton document — only one record should ever exist."""
    singleton_key: str = "global"       # always "global" — for upsert queries

    default_fleet_credit_limit: float = 500000.0
    transaction_alert_threshold: float = 200000.0
    require_2fa: bool = True
    admin_session_expiry_minutes: int = 30
    api_key: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[PydanticObjectId] = None   # ref to User._id

    class Settings:
        name = "platform_settings"
        indexes = [
            [("singleton_key", 1)],
        ]