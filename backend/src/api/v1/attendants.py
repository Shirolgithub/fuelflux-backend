from fastapi import APIRouter, Depends, HTTPException, status
from beanie import PydanticObjectId
import structlog

from src.core.dependencies import get_current_active_user, require_role
from src.core.security import hash_password, verify_password
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant
from src.db.schemas.attendant import (
    AttendantCreate, AttendantUpdate,
    AttendantResetPassword, AttendantResponse
)

log = structlog.get_logger()
router = APIRouter(prefix="/attendants", tags=["attendants"])


def _attendant_dict(a: Attendant) -> dict:
    return {
        "id": str(a.id),
        "pump_id": str(a.pump_id),
        "name": a.name,
        "phone": a.phone,
        "email": a.email,
        "employee_id": a.employee_id,
        "designation": a.designation,
        "shift": a.shift,
        "role": a.role,
        "face_photo_url": a.face_photo_url,
        "address": a.address,
        "date_of_joining": a.date_of_joining.isoformat() if a.date_of_joining else None,
        "date_of_birth": a.date_of_birth.isoformat() if a.date_of_birth else None,
        "emergency_contact": a.emergency_contact,
        "is_active": a.is_active,
        "created_at": a.created_at.isoformat(),
    }


# ── EXISTING ROUTES (logic same, password hashing added) ──────────────────────

@router.post("/", status_code=201)
async def add_attendant(
    attendant_data: AttendantCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Create a new employee. Pump owner sets initial password."""
    try:
        pump = await Pump.find_one(Pump.owner_id == current_user.id)
        if not pump:
            raise HTTPException(
                status_code=400,
                detail="You don't have any registered pump. Create a pump first."
            )

        # Check duplicate employee_id
        existing = await Attendant.find_one(Attendant.employee_id == attendant_data.employee_id)
        if existing:
            raise HTTPException(status_code=400, detail="Employee ID already exists.")

        # Check duplicate phone
        existing_phone = await Attendant.find_one(Attendant.phone == attendant_data.phone)
        if existing_phone:
            raise HTTPException(status_code=400, detail="Phone number already registered.")

        data = attendant_data.model_dump()
        plain_password = data.pop("password")           # remove plaintext

        attendant = Attendant(
            **data,
            pump_id=pump.id,
            hashed_password=hash_password(plain_password),  # hash and store
        )
        await attendant.insert()

        log.info("Attendant created", employee_id=attendant.employee_id, pump_id=str(pump.id))
        return {"success": True, "data": _attendant_dict(attendant)}

    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to add attendant", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to add attendant: {str(e)}")


@router.get("/my-attendants")
async def get_my_attendants(
    current_user: User = Depends(get_current_active_user)
):
    """List all employees of current pump owner's pump."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        return []
    attendants = await Attendant.find(Attendant.pump_id == pump.id).to_list()
    return [_attendant_dict(a) for a in attendants]


# ── NEW ROUTES ─────────────────────────────────────────────────────────────────

@router.get("/{attendant_id}")
async def get_attendant_detail(
    attendant_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get single employee detail. Only pump owner of that pump can access."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    attendant = await Attendant.find_one(
        Attendant.id == PydanticObjectId(attendant_id),
        Attendant.pump_id == pump.id          # isolation: only this pump's employee
    )
    if not attendant:
        raise HTTPException(status_code=404, detail="Employee not found.")

    return {"success": True, "data": _attendant_dict(attendant)}


@router.put("/{attendant_id}")
async def update_attendant(
    attendant_id: str,
    update_data: AttendantUpdate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Update employee details."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    attendant = await Attendant.find_one(
        Attendant.id == PydanticObjectId(attendant_id),
        Attendant.pump_id == pump.id
    )
    if not attendant:
        raise HTTPException(status_code=404, detail="Employee not found.")

    update_dict = update_data.model_dump(exclude_none=True)
    for field, value in update_dict.items():
        setattr(attendant, field, value)

    from datetime import datetime
    attendant.updated_at = datetime.utcnow()
    await attendant.save()

    log.info("Attendant updated", employee_id=attendant.employee_id)
    return {"success": True, "data": _attendant_dict(attendant)}


@router.patch("/{attendant_id}/toggle-status")
async def toggle_attendant_status(
    attendant_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Activate or deactivate an employee account."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    attendant = await Attendant.find_one(
        Attendant.id == PydanticObjectId(attendant_id),
        Attendant.pump_id == pump.id
    )
    if not attendant:
        raise HTTPException(status_code=404, detail="Employee not found.")

    attendant.is_active = not attendant.is_active
    from datetime import datetime
    attendant.updated_at = datetime.utcnow()
    await attendant.save()

    action = "activated" if attendant.is_active else "deactivated"
    log.info(f"Attendant {action}", employee_id=attendant.employee_id)
    return {
        "success": True,
        "message": f"Employee {action} successfully.",
        "is_active": attendant.is_active
    }


@router.post("/{attendant_id}/reset-password")
async def reset_attendant_password(
    attendant_id: str,
    data: AttendantResetPassword,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Pump owner resets employee password (e.g. employee forgot it)."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    attendant = await Attendant.find_one(
        Attendant.id == PydanticObjectId(attendant_id),
        Attendant.pump_id == pump.id
    )
    if not attendant:
        raise HTTPException(status_code=404, detail="Employee not found.")

    attendant.hashed_password = hash_password(data.new_password)
    from datetime import datetime
    attendant.updated_at = datetime.utcnow()
    await attendant.save()

    log.info("Attendant password reset by owner", employee_id=attendant.employee_id)
    return {"success": True, "message": "Password reset successfully."}