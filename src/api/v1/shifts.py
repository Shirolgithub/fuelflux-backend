from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date, timedelta
import structlog

from src.core.dependencies import require_role, get_current_attendant
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant

log = structlog.get_logger()
router = APIRouter(prefix="/shifts", tags=["shifts"])

SHIFT_TYPES = ["Morning", "Evening", "Night"]


class AssignShiftBody(BaseModel):
    attendant_id: str
    date: str           # ISO "2025-06-01"
    shift_type: str     # Morning | Evening | Night


class CoverShiftBody(BaseModel):
    covered_by_attendant_id: str


async def _get_owner_pump(current_user: User) -> Pump:
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")
    return pump


def _assignment_dict(a, attendant_name: Optional[str] = None) -> dict:
    return {
        "id": str(a.id),
        "attendant_id": str(a.attendant_id),
        "attendant_name": attendant_name,
        "date": a.date.isoformat(),
        "shift_type": a.shift_type,
        "status": a.status,
        "covered_by": str(a.covered_by) if a.covered_by else None,
        "note": a.note,
    }


@router.get("/weekly")
async def get_weekly_shifts(
    start_date: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get weekly schedule (7 days from start_date) with attendant names."""
    from src.db.models.shift_assignment import ShiftAssignment

    pump = await _get_owner_pump(current_user)
    start = datetime.fromisoformat(start_date).date()
    dates = [start + timedelta(days=i) for i in range(7)]

    # Fetch all assignments for the 7-day window
    assignments = await ShiftAssignment.find(
        ShiftAssignment.pump_id == pump.id,
        ShiftAssignment.date >= dates[0],
        ShiftAssignment.date <= dates[-1],
    ).to_list()

    # Build attendant id→name lookup
    attendant_ids = list({a.attendant_id for a in assignments})
    attendants = {}
    for aid in attendant_ids:
        emp = await Attendant.find_one(Attendant.id == aid)
        if emp:
            attendants[str(aid)] = emp.name

    # Build grid: { date_iso: { shift_type: assignment_dict | None } }
    grid = {}
    for d in dates:
        grid[d.isoformat()] = {st: None for st in SHIFT_TYPES}

    for a in assignments:
        d_iso = a.date.isoformat()
        name = attendants.get(str(a.attendant_id))
        if d_iso in grid and a.shift_type in SHIFT_TYPES:
            grid[d_iso][a.shift_type] = _assignment_dict(a, name)

    return {"success": True, "start_date": start_date, "grid": grid}


@router.post("/assign", status_code=201)
async def assign_shift(
    body: AssignShiftBody,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Create or update a shift assignment for an employee."""
    from src.db.models.shift_assignment import ShiftAssignment

    pump = await _get_owner_pump(current_user)

    if body.shift_type not in SHIFT_TYPES:
        raise HTTPException(status_code=400, detail=f"shift_type must be one of {SHIFT_TYPES}.")

    # Validate employee belongs to pump
    attendant = await Attendant.find_one(
        Attendant.id == PydanticObjectId(body.attendant_id),
        Attendant.pump_id == pump.id,
    )
    if not attendant:
        raise HTTPException(status_code=404, detail="Employee not found on your pump.")

    assign_date = datetime.fromisoformat(body.date).date()

    # Upsert: update if same employee + date + shift_type
    existing = await ShiftAssignment.find_one(
        ShiftAssignment.attendant_id == PydanticObjectId(body.attendant_id),
        ShiftAssignment.date == assign_date,
        ShiftAssignment.shift_type == body.shift_type,
        ShiftAssignment.pump_id == pump.id,
    )

    if existing:
        existing.status = "scheduled"
        existing.covered_by = None
        await existing.save()
        assignment = existing
    else:
        assignment = ShiftAssignment(
            pump_id=pump.id,
            attendant_id=PydanticObjectId(body.attendant_id),
            date=assign_date,
            shift_type=body.shift_type,
            status="scheduled",
        )
        await assignment.insert()

    log.info("Shift assigned",
             employee_id=attendant.employee_id,
             date=body.date,
             shift_type=body.shift_type)
    return {"success": True, "data": _assignment_dict(assignment, attendant.name)}


@router.patch("/{assignment_id}/cover")
async def cover_shift(
    assignment_id: str,
    body: CoverShiftBody,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Mark a shift as covered by another employee."""
    from src.db.models.shift_assignment import ShiftAssignment

    pump = await _get_owner_pump(current_user)

    assignment = await ShiftAssignment.find_one(
        ShiftAssignment.id == PydanticObjectId(assignment_id),
        ShiftAssignment.pump_id == pump.id,
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Shift assignment not found.")

    cover_id = PydanticObjectId(body.covered_by_attendant_id)
    cover_emp = await Attendant.find_one(Attendant.id == cover_id, Attendant.pump_id == pump.id)
    if not cover_emp:
        raise HTTPException(status_code=404, detail="Cover employee not found on your pump.")

    assignment.covered_by = cover_id
    assignment.status = "covered"
    await assignment.save()

    log.info("Shift covered", assignment_id=assignment_id, cover_employee=cover_emp.employee_id)
    return {"success": True, "message": f"Shift covered by {cover_emp.name}.", "status": "covered"}


@router.get("/my-schedule")
async def get_my_schedule(
    attendant: Attendant = Depends(get_current_attendant)
):
    """Returns next 7 days of shift assignments for the authenticated employee."""
    from src.db.models.shift_assignment import ShiftAssignment

    today = date.today()
    end = today + timedelta(days=6)

    assignments = await ShiftAssignment.find(
        ShiftAssignment.attendant_id == PydanticObjectId(str(attendant.id)),
        ShiftAssignment.date >= today,
        ShiftAssignment.date <= end,
    ).to_list()

    # Build dict keyed by date for easy lookup
    assign_map = {a.date.isoformat(): a for a in assignments}

    schedule = []
    for i in range(7):
        d = today + timedelta(days=i)
        d_iso = d.isoformat()
        a = assign_map.get(d_iso)
        schedule.append({
            "date": d_iso,
            "day_name": d.strftime("%A"),
            "shift_type": a.shift_type if a else None,
            "status": a.status if a else None,
            "assignment_id": str(a.id) if a else None,
        })

    return {"success": True, "schedule": schedule}
