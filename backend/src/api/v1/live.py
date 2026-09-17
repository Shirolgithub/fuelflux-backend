from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from src.core.dependencies import get_current_active_user
from src.db.models.user import User
from src.db.models.transaction import Transaction
from src.db.models.attendant import Attendant
from src.db.models.pump import Pump
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/live", tags=["live"])


@router.get("/forecourt/{pump_id}")
async def get_live_forecourt(
    pump_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """Live Forecourt Status"""
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    try:
        pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == current_user.id)
        if not pump:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Recent transactions
        recent = await Transaction.find(
            Transaction.pump_id == oid
        ).sort(-Transaction.timestamp).limit(5).to_list()

        active_attendants = await Attendant.find(
            Attendant.pump_id == oid,
            Attendant.is_active == True
        ).count()

        return {
            "pump_id": pump_id,
            "pump_name": pump.name,
            "active_attendants": active_attendants,
            "recent_transactions": [
                {
                    "id": str(t.id),
                    "volume": t.volume,
                    "amount": t.amount,
                    "vehicle_plate": t.vehicle_plate,
                    "timestamp": t.timestamp
                } for t in recent
            ],
            "status": "online"
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Live forecourt error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))