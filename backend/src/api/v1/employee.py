from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, date
import structlog

from src.core.dependencies import get_current_attendant
from src.core.security import hash_password, verify_password
from src.db.models.attendant import Attendant
from src.db.schemas.auth import EmployeeChangePassword

log = structlog.get_logger()
router = APIRouter(prefix="/employee", tags=["employee"])


# ── Profile ────────────────────────────────────────────────────────────────────

@router.get("/my-profile")
async def get_my_profile(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apni profile dekhta hai."""
    return {
        "success": True,
        "data": {
            "id": str(attendant.id),
            "name": attendant.name,
            "phone": attendant.phone,
            "email": attendant.email,
            "employee_id": attendant.employee_id,
            "designation": attendant.designation,
            "shift": attendant.shift,
            "pump_id": str(attendant.pump_id),
            "face_photo_url": attendant.face_photo_url,
            "address": attendant.address,
            "date_of_joining": attendant.date_of_joining.isoformat() if attendant.date_of_joining else None,
            "date_of_birth": attendant.date_of_birth.isoformat() if attendant.date_of_birth else None,
            "emergency_contact": attendant.emergency_contact,
            "is_active": attendant.is_active,
        }
    }


# ── Attendance ─────────────────────────────────────────────────────────────────

@router.post("/check-in")
async def check_in(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee check-in karta hai."""
    from src.db.models.attendance import AttendanceRecord

    today = date.today()

    # Duplicate check-in guard
    existing = await AttendanceRecord.find_one(
        AttendanceRecord.attendant_id == attendant.id,
        AttendanceRecord.date == today,
    )
    if existing:
        if existing.check_in:
            raise HTTPException(
                status_code=400,
                detail="Already checked in today."
            )

    now = datetime.utcnow()
    record = AttendanceRecord(
        attendant_id=attendant.id,
        pump_id=attendant.pump_id,                  # isolation field
        date=today,
        check_in=now,
        status="Present",
    )
    await record.insert()

    log.info("Check-in recorded", employee_id=attendant.employee_id, time=now.isoformat())
    return {
        "success": True,
        "message": "Checked in successfully.",
        "check_in_time": now.strftime("%I:%M %p"),
    }


@router.post("/check-out")
async def check_out(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee check-out karta hai."""
    from src.db.models.attendance import AttendanceRecord

    today = date.today()
    record = await AttendanceRecord.find_one(
        AttendanceRecord.attendant_id == attendant.id,
        AttendanceRecord.date == today,
    )

    if not record or not record.check_in:
        raise HTTPException(status_code=400, detail="No check-in found for today.")

    if record.check_out:
        raise HTTPException(status_code=400, detail="Already checked out today.")

    now = datetime.utcnow()
    record.check_out = now

    # Working hours calculate karo
    delta = now - record.check_in
    record.working_hours = round(delta.total_seconds() / 3600, 2)
    await record.save()

    log.info("Check-out recorded", employee_id=attendant.employee_id, hours=record.working_hours)
    return {
        "success": True,
        "message": "Checked out successfully.",
        "check_out_time": now.strftime("%I:%M %p"),
        "working_hours": record.working_hours,
    }


@router.get("/attendance")
async def get_my_attendance(
    month: int = None,
    year: int = None,
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apni attendance history dekhta hai."""
    from src.db.models.attendance import AttendanceRecord

    now = datetime.utcnow()
    month = month or now.month
    year = year or now.year

    records = await AttendanceRecord.find(
        AttendanceRecord.attendant_id == attendant.id,
        AttendanceRecord.pump_id == attendant.pump_id,  # double isolation
    ).to_list()

    # Month filter
    records = [
        r for r in records
        if r.date.month == month and r.date.year == year
    ]

    total = len(records)
    present = sum(1 for r in records if r.status in ("Present", "Late"))
    late = sum(1 for r in records if r.status == "Late")
    leaves = sum(1 for r in records if r.status == "Leave")
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
            "attendance_percentage": attendance_pct,
        },
        "records": [
            {
                "id": str(r.id),
                "date": r.date.isoformat(),
                "status": r.status,
                "check_in": r.check_in.strftime("%I:%M %p") if r.check_in else None,
                "check_out": r.check_out.strftime("%I:%M %p") if r.check_out else None,
                "working_hours": r.working_hours,
            }
            for r in sorted(records, key=lambda x: x.date, reverse=True)
        ]
    }


@router.get("/today-attendance")
async def today_attendance(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Aaj ki attendance status."""
    from src.db.models.attendance import AttendanceRecord

    today = date.today()
    record = await AttendanceRecord.find_one(
        AttendanceRecord.attendant_id == attendant.id,
        AttendanceRecord.date == today,
    )

    if not record:
        return {
            "success": True,
            "today_status": "Not Checked In",
            "check_in": None,
            "check_out": None,
            "working_hours": 0,
        }

    return {
        "success": True,
        "today_status": record.status,
        "check_in": record.check_in.strftime("%I:%M %p") if record.check_in else None,
        "check_out": record.check_out.strftime("%I:%M %p") if record.check_out else None,
        "working_hours": record.working_hours or 0,
    }


# ── Leave ──────────────────────────────────────────────────────────────────────

@router.get("/leaves")
async def get_my_leaves(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apni leave requests dekhta hai."""
    from src.db.models.leave import LeaveRequest

    leaves = await LeaveRequest.find(
        LeaveRequest.attendant_id == attendant.id,
        LeaveRequest.pump_id == attendant.pump_id,
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
                "status": l.status,          # pending | approved | rejected
                "applied_on": l.applied_on.isoformat(),
                "reviewed_note": l.reviewed_note,
            }
            for l in leaves
        ]
    }


@router.post("/leaves")
async def apply_leave(
    leave_data: dict,
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee leave apply karta hai."""
    from src.db.models.leave import LeaveRequest

    leave = LeaveRequest(
        attendant_id=attendant.id,
        pump_id=attendant.pump_id,
        leave_type=leave_data.get("leave_type", "Casual"),
        from_date=datetime.fromisoformat(leave_data["from_date"]).date(),
        to_date=datetime.fromisoformat(leave_data["to_date"]).date(),
        reason=leave_data.get("reason", ""),
        status="pending",
        applied_on=datetime.utcnow(),
    )
    await leave.insert()

    return {"success": True, "message": "Leave application submitted.", "id": str(leave.id)}


# ── Salary ─────────────────────────────────────────────────────────────────────

@router.get("/salary")
async def get_my_salary(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apni salary slips dekhta hai."""
    from src.db.models.salary import SalarySlip

    slips = await SalarySlip.find(
        SalarySlip.attendant_id == attendant.id,
        SalarySlip.pump_id == attendant.pump_id,
    ).sort("-month").to_list()

    return {
        "success": True,
        "data": [
            {
                "id": str(s.id),
                "month": s.month,
                "year": s.year,
                "basic_salary": s.basic_salary,
                "deductions": s.deductions,
                "bonuses": s.bonuses,
                "net_salary": s.net_salary,
                "payment_status": s.payment_status,   # paid | pending
                "paid_on": s.paid_on.isoformat() if s.paid_on else None,
            }
            for s in slips
        ]
    }


# ── Announcements ──────────────────────────────────────────────────────────────

@router.get("/announcements")
async def get_announcements(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apne pump ki announcements dekhta hai."""
    from src.db.models.announcement import Announcement

    announcements = await Announcement.find(
        Announcement.pump_id == attendant.pump_id,
        Announcement.is_active == True,
    ).sort("-created_at").to_list()

    return {
        "success": True,
        "data": [
            {
                "id": str(a.id),
                "title": a.title,
                "content": a.content,
                "type": a.announcement_type,    # General | Urgent | Safety
                "created_at": a.created_at.isoformat(),
            }
            for a in announcements
        ]
    }


# ── Shift ──────────────────────────────────────────────────────────────────────

@router.get("/my-shift")
async def get_my_shift(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apni shift info dekhta hai."""
    return {
        "success": True,
        "data": {
            "current_shift": attendant.shift or "Not Assigned",
            "designation": attendant.designation,
            "employee_id": attendant.employee_id,
        }
    }


# ── Password ───────────────────────────────────────────────────────────────────

@router.post("/change-password")
async def change_password(
    data: EmployeeChangePassword,
    attendant: Attendant = Depends(get_current_attendant)
):
    """Employee apna password khud change karta hai."""
    if not verify_password(data.current_password, attendant.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    attendant.hashed_password = hash_password(data.new_password)
    attendant.updated_at = datetime.utcnow()
    await attendant.save()

    log.info("Employee changed password", employee_id=attendant.employee_id)
    return {"success": True, "message": "Password changed successfully."}