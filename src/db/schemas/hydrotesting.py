from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class HydroTestBase(BaseModel):
    vehicle_plate: str
    test_type: str = "hydrotesting"   # hydrotesting, puc, insurance etc.
    status: str = "pending"           # pending, passed, failed

class HydroTestCreate(HydroTestBase):
    pump_id: int
    remarks: Optional[str] = None

class HydroTestResponse(HydroTestBase):
    id: int
    pump_id: int
    tested_at: datetime
    anpr_confidence: Optional[float] = None

    class Config:
        from_attributes = True