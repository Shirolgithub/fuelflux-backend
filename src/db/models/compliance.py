from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class NotifyContact(BaseModel):
    name: str
    phone: str
    email: str
    via: List[str] = Field(default_factory=list) # "whatsapp", "email"


class ComplianceDocumentType(Document):
    pump_id: PydanticObjectId
    name: str
    category: str
    is_mandatory: bool = True
    reminder_days: List[int] = Field(default_factory=lambda: [90, 60, 30, 15, 7, 1])
    is_active: bool = True

    class Settings:
        name = "compliance_document_types"
        indexes = [
            [("pump_id", 1)],
            [("pump_id", 1), ("is_active", 1)],
        ]


class ComplianceDocument(Document):
    pump_id: PydanticObjectId
    doc_type_id: PydanticObjectId
    doc_type_name: str
    certificate_number: str
    issuing_authority: Optional[str] = None
    issue_date: datetime
    expiry_date: datetime
    renewal_date: Optional[datetime] = None
    file_url: str
    file_name: str
    file_size: int
    status: str = "active" # active, expiring_soon, expired, renewal_pending, pending_verification, rejected
    notify_contacts: List[NotifyContact] = Field(default_factory=list)
    reminders_sent: List[int] = Field(default_factory=list)
    is_verified: bool = False
    verified_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "compliance_documents"
        indexes = [
            [("pump_id", 1)],
            [("pump_id", 1), ("status", 1)],
            [("expiry_date", 1)],
        ]
