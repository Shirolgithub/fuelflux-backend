from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from datetime import datetime
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.transaction import Transaction
from src.db.models.pump import Pump
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily")
async def daily_report(
    pump_id: str,
    date: str = None,
    current_user: User = Depends(get_current_active_user)
):
    """Daily Sales Report"""
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    try:
        pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == current_user.id)
        if not pump:
            raise HTTPException(status_code=403, detail="Not authorized")

        transactions = await Transaction.find(Transaction.pump_id == oid).to_list()

        return {
            "pump_name": pump.name,
            "date": date or datetime.utcnow().date().isoformat(),
            "total_sales": len(transactions),
            "total_volume": round(sum(t.volume for t in transactions), 2),
            "total_amount": round(sum(t.amount for t in transactions), 2),
            "top_attendants": "Coming soon",
            "peak_hours": "Coming soon"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monthly")
async def monthly_report(
    pump_id: str,
    month: int = None,
    year: int = None,
    current_user: User = Depends(get_current_active_user)
):
    """Monthly Summary Report"""
    return {
        "pump_id": pump_id,
        "month": month or datetime.utcnow().month,
        "year": year or datetime.utcnow().year,
        "total_amount": 245000.50,
        "total_volume": 12450.75,
        "message": "Monthly report (Demo data)"
    }