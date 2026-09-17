# src/db/models/audit_log.py
# Persistent audit trail — every admin action gets stored here.

from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class AuditLog(Document):
    admin_id: PydanticObjectId          # ref to User._id
    admin_email: str
    action: str                         # e.g. "PUMP_STATUS_CHANGED:pending→active"
    target_type: Optional[str] = None   # pump, user, payment
    target_id: Optional[str] = None     # e.g. ObjectId as string
    metadata_json: Optional[str] = None # JSON string of extra context
    ip_address: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "audit_logs"
        indexes = [
            [("timestamp", -1)],
            [("admin_id", 1)],
        ]