from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date, timedelta
import structlog

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant

log = structlog.get_logger()
router = APIRouter(prefix="/attendants", tags=["leave-mgmt"])


class LeaveReviewBody(BaseModel):
    status: str             # "approved" | "rejected"
    reviewed_note: Optional[str] = None


async def _get_pump_and_employee(attendant_id: str, current_user: User):
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    attendant = await Attendant.find_one(
        Attendant.id == PydanticObjectId(attendant_id),
        Attendant.pump_id == pump.id
    )
    if not attendant:
        raise HTTPException(status_code=404, detail="Employee not found on your pump.")

    return pump, attendant


@router.get("/{attendant_id}/leaves")
async def get_employee_leaves(
    attendant_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get all leave requests for a specific employee."""
    from src.db.models.leave import LeaveRequest

    _, attendant = await _get_pump_and_employee(attendant_id, current_user)

    leaves = await LeaveRequest.find(
        LeaveRequest.attendant_id == PydanticObjectId(str(attendant.id)),
    ).sort("-applied_on").to_list()

    return {
        "success": True,
        "data": [
            {
                "id": str(l.id),
                "leave_type": l.leave_type,
                "from_date": l.from_date.isoformat(),
                "to_date": l.to_date.isoformat(),
                "reason": l.reason,
                "status": l.status,
                "applied_on": l.applied_on.isoformat(),
                "reviewed_by": l.reviewed_by,
                "reviewed_on": l.reviewed_on.isoformat() if l.reviewed_on else None,
                "reviewed_note": l.reviewed_note,
            }
            for l in leaves
        ],
    }


@router.patch("/{attendant_id}/leaves/{leave_id}")
async def review_leave(
    attendant_id: str,
    leave_id: str,
    body: LeaveReviewBody,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Approve or reject a leave request. On approval, creates attendance records."""
    from src.db.models.leave import LeaveRequest
    from src.db.models.attendance import AttendanceRecord

    if body.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'approved' or 'rejected'.")

    pump, attendant = await _get_pump_and_employee(attendant_id, current_user)

    leave = await LeaveRequest.find_one(
        LeaveRequest.id == PydanticObjectId(leave_id),
        LeaveRequest.attendant_id == PydanticObjectId(str(attendant.id)),
    )
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found.")
    if leave.status != "pending":
        raise HTTPException(status_code=400, detail="This leave request is already reviewed.")

    leave.status = body.status
    leave.reviewed_by = current_user.email
    leave.reviewed_on = datetime.utcnow()
    leave.reviewed_note = body.reviewed_note
    await leave.save()

    # If approved, create an AttendanceRecord with status="Leave" for each day in the range
    if body.status == "approved":
        current_date = leave.from_date
        while current_date <= leave.to_date:
            # Skip if a record already exists for this day
            existing = await AttendanceRecord.find_one(
                AttendanceRecord.attendant_id == PydanticObjectId(str(attendant.id)),
                AttendanceRecord.date == current_date,
            )
            if not existing:
                record = AttendanceRecord(
                    attendant_id=PydanticObjectId(str(attendant.id)),
                    pump_id=PydanticObjectId(str(pump.id)),
                    date=current_date,
                    status="Leave",
                    is_manual=True,
                    note=f"Approved leave: {leave.leave_type}",
                )
                await record.insert()
            current_date = current_date + timedelta(days=1)

    log.info("Leave reviewed", leave_id=leave_id, status=body.status, reviewer=current_user.email)
    return {
        "success": True,
        "message": f"Leave request {body.status}.",
        "status": body.status,
    }
