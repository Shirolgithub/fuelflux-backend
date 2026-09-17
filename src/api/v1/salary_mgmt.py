from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import structlog

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant

log = structlog.get_logger()
router = APIRouter(prefix="/attendants", tags=["salary-mgmt"])


class SalaryCreate(BaseModel):
    month: int
    year: int
    basic_salary: float
    deductions: float = 0.0
    bonuses: float = 0.0


class SalaryMarkPaid(BaseModel):
    payment_status: str  # "paid"


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


def _slip_dict(s) -> dict:
    return {
        "id": str(s.id),
        "month": s.month,
        "year": s.year,
        "basic_salary": s.basic_salary,
        "deductions": s.deductions,
        "bonuses": s.bonuses,
        "net_salary": s.net_salary,
        "payment_status": s.payment_status,
        "paid_on": s.paid_on.isoformat() if s.paid_on else None,
        "note": s.note,
        "created_at": s.created_at.isoformat(),
    }


@router.get("/{attendant_id}/salary")
async def get_employee_salary(
    attendant_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get all salary slips for a specific employee."""
    from src.db.models.salary import SalarySlip

    _, attendant = await _get_pump_and_employee(attendant_id, current_user)

    slips = await SalarySlip.find(
        SalarySlip.attendant_id == PydanticObjectId(str(attendant.id)),
    ).sort([("year", -1), ("month", -1)]).to_list()

    return {"success": True, "data": [_slip_dict(s) for s in slips]}


@router.post("/{attendant_id}/salary", status_code=201)
async def generate_salary_slip(
    attendant_id: str,
    body: SalaryCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Generate a salary slip for an employee for a given month/year."""
    from src.db.models.salary import SalarySlip

    pump, attendant = await _get_pump_and_employee(attendant_id, current_user)

    # Duplicate check
    existing = await SalarySlip.find_one(
        SalarySlip.attendant_id == PydanticObjectId(str(attendant.id)),
        SalarySlip.month == body.month,
        SalarySlip.year == body.year,
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Salary slip for {body.month}/{body.year} already exists for this employee."
        )

    net_salary = round(body.basic_salary + body.bonuses - body.deductions, 2)

    slip = SalarySlip(
        attendant_id=PydanticObjectId(str(attendant.id)),
        pump_id=PydanticObjectId(str(pump.id)),
        month=body.month,
        year=body.year,
        basic_salary=body.basic_salary,
        deductions=body.deductions,
        bonuses=body.bonuses,
        net_salary=net_salary,
        payment_status="pending",
    )
    await slip.insert()

    log.info("Salary slip generated",
             employee_id=attendant.employee_id,
             month=body.month,
             year=body.year,
             net_salary=net_salary)
    return {"success": True, "data": _slip_dict(slip)}


@router.patch("/{attendant_id}/salary/{slip_id}")
async def mark_salary_paid(
    attendant_id: str,
    slip_id: str,
    body: SalaryMarkPaid,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Mark a salary slip as paid. RazorpayX integration placeholder."""
    from src.db.models.salary import SalarySlip

    _, attendant = await _get_pump_and_employee(attendant_id, current_user)

    slip = await SalarySlip.find_one(
        SalarySlip.id == PydanticObjectId(slip_id),
        SalarySlip.attendant_id == PydanticObjectId(str(attendant.id)),
    )
    if not slip:
        raise HTTPException(status_code=404, detail="Salary slip not found.")

    if slip.payment_status == "paid":
        raise HTTPException(status_code=400, detail="This salary slip is already marked as paid.")

    # RazorpayX placeholder — log intent
    log.info(
        "PAYMENT_PENDING_RAZORPAY_INTEGRATION",
        employee_id=attendant.employee_id,
        slip_id=slip_id,
        amount=slip.net_salary,
        month=slip.month,
        year=slip.year,
    )

    slip.payment_status = "paid"
    slip.paid_on = datetime.utcnow()
    await slip.save()

    return {
        "success": True,
        "message": "Salary marked as paid. RazorpayX payout will be processed in next integration cycle.",
        "payment_status": "paid",
        "paid_on": slip.paid_on.isoformat(),
    }
