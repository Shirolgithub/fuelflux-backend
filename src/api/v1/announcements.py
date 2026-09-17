from fastapi import APIRouter, Depends, HTTPException, status
from beanie import PydanticObjectId
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import structlog

from src.core.dependencies import get_current_active_user, require_role, get_current_attendant
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant
from src.db.models.announcement import Announcement

log = structlog.get_logger()
router = APIRouter(prefix="/announcements", tags=["announcements"])


class AnnouncementCreate(BaseModel):
    title: str
    content: str
    announcement_type: str = "General"  # General | Urgent | Safety | Holiday
    target_employee_id: Optional[str] = None


def _announcement_dict(a: Announcement, attendant_name: Optional[str] = None) -> dict:
    return {
        "id": str(a.id),
        "pump_id": str(a.pump_id),
        "title": a.title,
        "content": a.content,
        "announcement_type": a.announcement_type,
        "is_active": a.is_active,
        "created_by": a.created_by,
        "created_at": a.created_at.isoformat(),
        "target_employee_id": str(a.target_employee_id) if a.target_employee_id else None,
        "target_employee_name": attendant_name,
    }


@router.post("/", status_code=201)
async def create_announcement(
    data: AnnouncementCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Pump owner creates an announcement."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(
            status_code=400,
            detail="You don't have any registered pump. Create a pump first."
        )

    target_id = None
    if data.target_employee_id:
        target_id = PydanticObjectId(data.target_employee_id)
        # Validate employee belongs to pump
        employee = await Attendant.find_one(Attendant.id == target_id, Attendant.pump_id == pump.id)
        if not employee:
            raise HTTPException(status_code=400, detail="Employee not found on your pump.")

    announcement = Announcement(
        pump_id=pump.id,
        title=data.title,
        content=data.content,
        announcement_type=data.announcement_type,
        is_active=True,
        created_by=current_user.email,
        target_employee_id=target_id,
        created_at=datetime.utcnow()
    )
    await announcement.insert()

    log.info("Announcement created", announcement_id=str(announcement.id), pump_id=str(pump.id))
    return {"success": True, "data": _announcement_dict(announcement)}


@router.get("/my-pump")
async def get_my_pump_announcements(
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """List all announcements for current pump owner's pump."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        return []

    announcements = await Announcement.find(
        Announcement.pump_id == pump.id
    ).sort("-created_at").to_list()

    # Collect attendant names for display
    res_list = []
    for a in announcements:
        name = "All Employees"
        if a.target_employee_id:
            emp = await Attendant.find_one(Attendant.id == a.target_employee_id)
            if emp:
                name = emp.name
        res_list.append(_announcement_dict(a, name))

    return res_list


@router.delete("/{id}")
async def delete_announcement(
    id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Delete an announcement."""
    pump = await Pump.find_one(Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    announcement = await Announcement.find_one(
        Announcement.id == PydanticObjectId(id),
        Announcement.pump_id == pump.id
    )
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found or not owned by you.")

    await announcement.delete()
    log.info("Announcement deleted", announcement_id=id)
    return {"success": True, "message": "Announcement deleted successfully."}


@router.get("/employee")
async def get_employee_announcements(
    attendant: Attendant = Depends(get_current_attendant)
):
    """List announcements for current attendant's pump."""
    announcements = await Announcement.find(
        Announcement.pump_id == PydanticObjectId(str(attendant.pump_id)),
        Announcement.is_active == True
    ).sort("-created_at").to_list()

    # Filter manually for target_employee_id match or None
    filtered = []
    for a in announcements:
        if a.target_employee_id is None or a.target_employee_id == attendant.id:
            filtered.append(_announcement_dict(a))

    return filtered
