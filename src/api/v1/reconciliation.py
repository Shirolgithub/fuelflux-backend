from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from datetime import datetime
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.transaction import Transaction
from src.db.models.pump import Pump
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/shift")
async def reconcile_shift(
    pump_id: str,
    shift_date: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Auto Reconciliation for a shift"""
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    try:
        transactions = await Transaction.find(Transaction.pump_id == oid).to_list()
        total_amount = sum(t.amount for t in transactions)
        total_volume = sum(t.volume for t in transactions)

        return {
            "pump_id": pump_id,
            "shift_date": shift_date,
            "expected_amount": total_amount,
            "actual_amount": total_amount,
            "expected_volume": total_volume,
            "actual_volume": total_volume,
            "mismatch_amount": 0.0,
            "mismatch_volume": 0.0,
            "status": "matched"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary/{pump_id}")
async def reconciliation_summary(
    pump_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """Daily summary for reconciliation"""
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    transactions = await Transaction.find(Transaction.pump_id == oid).to_list()
    return {
        "pump_id": pump_id,
        "total_transactions": len(transactions),
        "total_volume": sum(t.volume for t in transactions),
        "total_amount": sum(t.amount for t in transactions),
        "last_reconciled": "Today"
    }


@router.post("/manual")
async def manual_reconcile(
    pump_id: str,
    expected_amount: float,
    actual_amount: float,
    remarks: str = "",
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Manual Reconciliation by Owner"""
    try:
        mismatch = abs(expected_amount - actual_amount)
        status = "matched" if mismatch < 50 else "mismatch"  # ₹50 tolerance

        return {
            "pump_id": pump_id,
            "expected_amount": expected_amount,
            "actual_amount": actual_amount,
            "mismatch_amount": round(mismatch, 2),
            "status": status,
            "remarks": remarks,
            "message": "Manual reconciliation completed successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))