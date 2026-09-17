from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class UdhaarAlert(Document):
    # Links
    customer_id: PydanticObjectId       # ref to Customer._id
    vehicle_id: Optional[PydanticObjectId] = None   # ref to Vehicle._id
    pump_id: PydanticObjectId           # ref to Pump._id
    logistic_partner_id: PydanticObjectId  # ref to User._id

    # Alert details
    credit_limit: float
    amount_used: float
    overspend_amount: float             # amount_used - credit_limit

    # Status
    is_resolved: bool = False
    resolved_at: Optional[datetime] = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_alerts"
        indexes = [
            [("pump_id", 1)],
            [("customer_id", 1)],
        ]