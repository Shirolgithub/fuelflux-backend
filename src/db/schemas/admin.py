from pydantic import BaseModel
from typing import List, Optional

class AdminStats(BaseModel):
    total_pumps: int
    total_owners: int
    active_subscriptions: int
    pending_registrations: int
    pending_payments: int

class PendingPump(BaseModel):
    id: int
    name: str
    owner_name: str
    address: str
    status: str
    created_at: str