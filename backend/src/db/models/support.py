from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, List, Any, Dict
from datetime import datetime, timedelta


class SupportTicket(Document):
    subject: str
    status: str = "open"                # open, in_progress, resolved, closed
    priority: str = "medium"            # low, medium, high, urgent
    user_id: PydanticObjectId           # ref to User._id
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    # Each message: {"sender": "user"|"admin", "senderName": str, "message": str, "timestamp": str}
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "support_tickets"
        indexes = [
            [("user_id", 1)],
            [("status", 1)],
        ]


async def seed_support_tickets():
    """Seed default support tickets if collection is empty."""
    from src.db.models.user import User

    count = await SupportTicket.count()
    if count > 0:
        return

    user = await User.find_one(User.email == "owner@fuelflux.com")
    if not user:
        user = await User.find_one()
    if not user:
        return

    t1 = SupportTicket(
        subject="Calibrating flow rate on nozzle 3",
        status="open",
        priority="high",
        user_id=user.id,
        messages=[
            {
                "sender": "user",
                "senderName": user.full_name or "Rajesh Kumar",
                "message": "Nozzle 3 on Pump 1 seems to flow slightly slower than Nozzle 4. Is there an automatic calibration offset?",
                "timestamp": (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
            }
        ],
        created_at=datetime.utcnow() - timedelta(days=1)
    )

    t2 = SupportTicket(
        subject="Request for invoice generation setup",
        status="in_progress",
        priority="medium",
        user_id=user.id,
        messages=[
            {
                "sender": "user",
                "senderName": user.full_name or "Rajesh Kumar",
                "message": "Can we schedule automated monthly PDF statements to be emailed directly to our credit customers?",
                "timestamp": (datetime.utcnow() - timedelta(days=2)).isoformat() + "Z"
            },
            {
                "sender": "admin",
                "senderName": "System Admin",
                "message": "Hello Rajesh, we have checked this. You can configure automatic scheduling from your Settings panel, under Invoice Preferences.",
                "timestamp": (datetime.utcnow() - timedelta(days=1, hours=12)).isoformat() + "Z"
            }
        ],
        created_at=datetime.utcnow() - timedelta(days=2)
    )

    await SupportTicket.insert_many([t1, t2])
