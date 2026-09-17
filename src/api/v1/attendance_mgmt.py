from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date
import structlog

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant

log = structlog.get_logger()
router = APIRouter(prefix="/attendants", tags=["attendance-mgmt"])


class ManualAttendanceBody(BaseModel):
    date: str                               # ISO date "2025-06-01"
    status: str                             # Present | Late | Absent | Leave | Holiday
    check_in: Optional[str] = None          # "09:00" 24h format
    check_out: Optional[str] = None
    note: Optional[str] = None


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


@router.get("/{attendant_id}/attendance")
async def get_employee_attendance(
    attendant_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get attendance records for specific employee (owner only)."""
    from src.db.models.attendance import AttendanceRecord

    pump, attendant = await _get_pump_and_employee(attendant_id, current_user)

    now = datetime.utcnow()
    month = month or now.month
    year = year or now.year

    records = await AttendanceRecord.find(
        AttendanceRecord.attendant_id == PydanticObjectId(str(attendant.id)),
        AttendanceRecord.pump_id == PydanticObjectId(str(pump.id)),
    ).to_list()

    # Filter by month/year
    records = [r for r in records if r.date.month == month and r.date.year == year]

    total = len(records)
    present = sum(1 for r in records if r.status in ("Present", "Late"))
    late = sum(1 for r in records if r.status == "Late")
    leaves = sum(1 for r in records if r.status == "Leave")
    absent = sum(1 for r in records if r.status == "Absent")
    attendance_pct = round((present / total * 100), 1) if total > 0 else 100.0

    return {
        "success": True,
        "summary": {
            "month": month,
            "year": year,
            "total_days": total,
            "present_days": present,
            "late_days": late,
            "leave_days": leaves,
            "absent_days": absent,
            "attendance_percentage": attendance_pct,
        },
        "records": [
            {
                "id": str(r.id),
                "date": r.date.isoformat(),
                "status": r.status,
                "check_in": r.check_in.strftime("%I:%M %p") if r.check_in else None,
                "check_out": r.check_out.strftime("%I:%M %p") if r.check_out else None,
                "working_hours": r.working_hours or 0,
                "is_manual": r.is_manual,
                "note": r.note,
            }
            for r in sorted(records, key=lambda x: x.date, reverse=True)
        ],
    }


@router.post("/{attendant_id}/attendance", status_code=201)
async def mark_manual_attendance(
    attendant_id: str,
    body: ManualAttendanceBody,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Pump owner manually marks attendance for an employee."""
    from src.db.models.attendance import AttendanceRecord

    pump, attendant = await _get_pump_and_employee(attendant_id, current_user)

    record_date = datetime.fromisoformat(body.date).date()

    def _parse_time(t_str: Optional[str], d: date) -> Optional[datetime]:
        if not t_str:
            return None
        try:
            h, m = map(int, t_str.split(":"))
            return datetime(d.year, d.month, d.day, h, m)
        except Exception:
            return None

    check_in_dt = _parse_time(body.check_in, record_date)
    check_out_dt = _parse_time(body.check_out, record_date)

    working_hours = None
    if check_in_dt and check_out_dt and check_out_dt > check_in_dt:
        delta = check_out_dt - check_in_dt
        working_hours = round(delta.total_seconds() / 3600, 2)

    # Upsert: update existing record if same date, otherwise create
    existing = await AttendanceRecord.find_one(
        AttendanceRecord.attendant_id == PydanticObjectId(str(attendant.id)),
        AttendanceRecord.date == record_date,
    )

    if existing:
        existing.status = body.status
        existing.check_in = check_in_dt
        existing.check_out = check_out_dt
        existing.working_hours = working_hours
        existing.note = body.note
        existing.is_manual = True
        await existing.save()
        record = existing
    else:
        record = AttendanceRecord(
            attendant_id=PydanticObjectId(str(attendant.id)),
            pump_id=PydanticObjectId(str(pump.id)),
            date=record_date,
            status=body.status,
            check_in=check_in_dt,
            check_out=check_out_dt,
            working_hours=working_hours,
            note=body.note,
            is_manual=True,
        )
        await record.insert()

    log.info("Manual attendance marked", employee_id=attendant.employee_id, date=body.date, status=body.status)
    return {
        "success": True,
        "message": "Attendance marked successfully.",
        "id": str(record.id),
    }
