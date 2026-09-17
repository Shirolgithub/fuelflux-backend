from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class CreditUsage(Document):
    # Who used the credit
    logistic_partner_id: PydanticObjectId  # ref to User._id
    vehicle_id: PydanticObjectId           # ref to Vehicle._id
    pump_id: PydanticObjectId              # ref to Pump._id
    attendant_id: Optional[PydanticObjectId] = None  # ref to Attendant._id

    # Linked credit request / voucher
    credit_request_id: Optional[PydanticObjectId] = None  # ref to CreditRequest._id
    voucher_id: Optional[PydanticObjectId] = None         # ref to PaymentRequest._id (voucher)
    sale_log_id: Optional[PydanticObjectId] = None        # ref to SaleLog._id

    # Fuel / Amount details
    fuel_type: Optional[str] = None
    volume: Optional[float] = None          # litres dispensed
    amount: float                           # monetary value

    # Status
    status: str = "pending"                 # pending, settled, disputed
    remarks: Optional[str] = None

    # Timestamps
    used_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "credit_usage"
        indexes = [
            [("pump_id", 1)],
            [("logistic_partner_id", 1)],
            [("vehicle_id", 1)],
        ]
