"""
Settings API — Pump Profile, Logo Upload, IoT Config
======================================================
Prefix: /api/v1/settings
Fully async — uses Beanie (MongoDB ODM)
"""

import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from beanie import PydanticObjectId
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/settings", tags=["settings"])

# ── Upload directory ──────────────────────────────────────────────────────────
UPLOAD_DIR = "public/uploads/logos"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
MAX_FILE_SIZE_MB = 5


# ── Schemas ───────────────────────────────────────────────────────────────────

class PumpProfileUpdate(BaseModel):
    org_name: Optional[str] = None
    address: Optional[str] = None
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    pincode: Optional[str] = None
    contact_number: Optional[str] = None


class IoTConfigUpdate(BaseModel):
    atg_api_key: Optional[str] = None
    anpr_rtsp_url: Optional[str] = None
    mqtt_broker_url: Optional[str] = None
    mqtt_topic_prefix: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_owned_pump(pump_id: str, user: User) -> Pump:
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id format")
    pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == user.id)
    if not pump:
        raise HTTPException(status_code=403, detail="Pump not found or not authorized")
    return pump


# ═══════════════════════════════════════════════════════════════════════════════
# PROFILE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/profile")
async def get_pump_profile(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get full pump profile including logo_url and org_name"""
    pump = await get_owned_pump(pump_id, current_user)
    return {
        "id": str(pump.id),
        "name": pump.name,
        "org_name": pump.org_name,
        "logo_url": pump.logo_url,
        "address": pump.address,
        "city": pump.city,
        "state": pump.state,
        "pincode": pump.pincode,
        "gst": pump.gst,
        "license": pump.license,
        "contact_number": pump.contact_number,
        "opening_time": pump.opening_time,
        "closing_time": pump.closing_time,
        "fuel_types": pump.fuel_types,
        "tanks_count": pump.tanks_count,
        "nozzles_count": pump.nozzles_count,
        "daily_capacity": pump.daily_capacity,
        "latitude": pump.latitude,
        "longitude": pump.longitude,
        "status": pump.status,
        "is_active": pump.is_active,
        "created_at": pump.created_at,
    }


@router.put("/profile")
async def update_pump_profile(
    pump_id: str = Query(...),
    data: PumpProfileUpdate = ...,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Update editable pump profile fields"""
    pump = await get_owned_pump(pump_id, current_user)

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(pump, field, value)

    pump.updated_at = datetime.utcnow()
    await pump.save()

    return {
        "message": "Profile updated successfully",
        "org_name": pump.org_name,
        "logo_url": pump.logo_url,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LOGO UPLOAD
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/logo")
async def upload_pump_logo(
    pump_id: str = Query(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Upload pump/brand logo.
    - Max 5MB
    - Allowed: PNG, JPG, SVG, WEBP
    - Saves to public/uploads/logos/
    - Returns URL path for frontend use
    """
    pump = await get_owned_pump(pump_id, current_user)

    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # Validate file size
    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size: {MAX_FILE_SIZE_MB}MB"
        )

    # Delete old logo if exists
    if pump.logo_url:
        old_path = pump.logo_url.lstrip("/")
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    # Save new logo with unique name
    filename = f"pump_{pump_id}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(contents)

    # Store URL path in DB (frontend will prepend base URL)
    logo_url = f"/uploads/logos/{filename}"
    pump.logo_url = logo_url
    pump.updated_at = datetime.utcnow()
    await pump.save()

    return {
        "message": "Logo uploaded successfully",
        "logo_url": logo_url,
        "filename": filename,
    }


@router.delete("/logo")
async def delete_pump_logo(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Remove pump logo"""
    pump = await get_owned_pump(pump_id, current_user)

    if not pump.logo_url:
        raise HTTPException(status_code=404, detail="No logo found")

    # Delete file
    file_path = pump.logo_url.lstrip("/")
    if os.path.exists(file_path):
        os.remove(file_path)

    pump.logo_url = None
    pump.updated_at = datetime.utcnow()
    await pump.save()

    return {"message": "Logo removed successfully"}


# ═══════════════════════════════════════════════════════════════════════════════
# ALL PUMPS (for franchise owners)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/pumps")
async def get_all_my_pumps(
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get all pumps owned by current user — for franchise multi-pump view"""
    pumps = await Pump.find(
        Pump.owner_id == current_user.id,
        Pump.is_active == True
    ).to_list()

    return [
        {
            "id": str(p.id),
            "name": p.name,
            "org_name": p.org_name,
            "logo_url": p.logo_url,
            "city": p.city,
            "state": p.state,
            "status": p.status,
            "address": p.address,
            "gst": p.gst,
            "tanks_count": p.tanks_count,
            "nozzles_count": p.nozzles_count,
        }
        for p in pumps
    ]