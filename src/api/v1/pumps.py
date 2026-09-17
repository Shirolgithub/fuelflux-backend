from fastapi import APIRouter, Depends, HTTPException, status, Query
from beanie import PydanticObjectId
from beanie.operators import In
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.schemas.pump import PumpCreate, PumpResponse
from src.core.auto_trial import auto_assign_default_trial
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/pumps", tags=["pumps"])


@router.get("/", summary="List all active pumps (public directory)")
async def list_all_pumps(
    city: str = Query(None, description="Filter by city"),
    search: str = Query(None, description="Search by name or address"),
):
    """
    Public endpoint — Logistic Partners use this to browse available pump stations
    before submitting a credit request. No authentication required.
    """
    query = Pump.find(Pump.is_active == True)

    results = await query.to_list()

    # Fetch owner details for each pump
    out = []
    for pump in results:
        if city and pump.city and city.lower() not in pump.city.lower():
            continue
        if search:
            name_match = pump.name and search.lower() in pump.name.lower()
            addr_match = pump.address and search.lower() in pump.address.lower()
            if not (name_match or addr_match):
                continue

        owner = await User.get(pump.owner_id)
        out.append({
            "id": str(pump.id),
            "name": pump.name,
            "address": pump.address,
            "city": pump.city,
            "state": pump.state,
            "pincode": pump.pincode,
            "contact_number": pump.contact_number,
            "opening_time": pump.opening_time,
            "closing_time": pump.closing_time,
            "fuel_types": pump.fuel_types.split(",") if pump.fuel_types else [],
            "owner_name": (owner.full_name if owner else None) or (owner.email if owner else "Unknown"),
            "status": pump.status,
        })
    return out


@router.post("/", status_code=201)
async def create_pump(
    pump_data: PumpCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        pump = Pump(
            **pump_data.model_dump(),
            owner_id=current_user.id,
            status="pending"
        )
        await pump.insert()
        await auto_assign_default_trial(pump.id, pump.owner_id)
        return {
            "id": str(pump.id),
            "name": pump.name,
            "status": pump.status,
            "message": "Pump created successfully"
        }
    except Exception as e:
        log.error("Pump creation failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Pump creation failed: {str(e)}")


@router.get("/my-pumps")
async def get_my_pumps(
    current_user: User = Depends(get_current_active_user)
):
    pumps = await Pump.find(Pump.owner_id == current_user.id).to_list()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "address": p.address,
            "city": p.city,
            "state": p.state,
            "pincode": p.pincode,
            "contact_number": p.contact_number,
            "status": p.status,
            "is_active": p.is_active,
            "fuel_types": p.fuel_types,
            "created_at": p.created_at,
        }
        for p in pumps
    ]


# ── VOUCHERS APPROVAL (PUMP OWNER) ───────────────────────────────────────────

@router.get("/vouchers/pending", summary="List pending vouchers for pump owner's stations")
async def get_pending_vouchers(
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Returns pending fuel vouchers associated with the pumps owned by this owner.
    """
    from src.db.models.payment import PaymentRequest
    from src.db.models.user import User as BeanieUser

    # 1. Fetch all pumps owned by this user
    pumps = await Pump.find(Pump.owner_id == current_user.id).to_list()
    pump_ids = [p.id for p in pumps]
    pump_map = {p.id: p for p in pumps}

    if not pump_ids:
        return []

    # 2. Find pending fuel vouchers for these pumps
    vouchers = await PaymentRequest.find(
        PaymentRequest.payment_type == "fuel_voucher",
        PaymentRequest.status == "pending",
        In(PaymentRequest.pump_id, pump_ids)
    ).sort(-PaymentRequest.requested_at).to_list()

    # 3. Formulate response with partner and vehicle details
    response = []
    for v in vouchers:
        partner = await BeanieUser.get(v.logistic_partner_id)
        form_data = v.logistic_form_data or {}
        vehicle_plate = form_data.get("vehicle_plate", "N/A")
        pump = pump_map.get(v.pump_id)

        response.append({
            "id": str(v.id),
            "voucher_ref": f"VCH-{str(v.id)[-6:]}",
            "logistic_partner_id": str(v.logistic_partner_id),
            "logistic_partner_name": partner.full_name if partner else (partner.email if partner else "Unknown"),
            "logistic_partner_email": partner.email if partner else "Unknown",
            "vehicle_plate": vehicle_plate,
            "fuel_type": form_data.get("fuel_type", "Diesel"),
            "amount": v.amount,
            "notes": form_data.get("notes", ""),
            "expiry_date": form_data.get("expiry_date", ""),
            "requested_at": v.requested_at.isoformat() if v.requested_at else None,
            "pump_id": str(v.pump_id),
            "pump_name": pump.name if pump else "Unknown",
            "status": v.status,
        })
    return response


@router.post("/vouchers/{voucher_id}/approve", summary="Approve a pending fuel voucher")
async def approve_voucher(
    voucher_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Approves a pending voucher. Sets status to 'approved'.
    """
    from src.db.models.payment import PaymentRequest
    from datetime import datetime

    try:
        oid = PydanticObjectId(voucher_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid voucher ID format")

    voucher = await PaymentRequest.get(oid)
    if not voucher or voucher.payment_type != "fuel_voucher":
        raise HTTPException(status_code=404, detail="Voucher not found")

    # Verify pump ownership
    pump = await Pump.find_one(Pump.id == voucher.pump_id, Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=403, detail="Not authorized to approve this voucher")

    if voucher.status != "pending":
        raise HTTPException(status_code=400, detail=f"Voucher is not pending. Current status: {voucher.status}")

    voucher.status = "approved"
    voucher.reviewed_at = datetime.utcnow()
    voucher.reviewed_by = current_user.id
    await voucher.save()

    log.info("Fuel voucher approved", voucher_id=voucher_id, approved_by=str(current_user.id))

    return {
        "success": True,
        "message": "Voucher approved successfully",
        "voucher_id": voucher_id,
        "status": "approved"
    }


@router.post("/vouchers/{voucher_id}/reject", summary="Reject a pending fuel voucher")
async def reject_voucher(
    voucher_id: str,
    payload: dict,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Rejects a pending voucher. Sets status to 'rejected'.
    Body: { "reason": str }
    """
    from src.db.models.payment import PaymentRequest
    from datetime import datetime

    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Rejection reason is required")

    try:
        oid = PydanticObjectId(voucher_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid voucher ID format")

    voucher = await PaymentRequest.get(oid)
    if not voucher or voucher.payment_type != "fuel_voucher":
        raise HTTPException(status_code=404, detail="Voucher not found")

    # Verify pump ownership
    pump = await Pump.find_one(Pump.id == voucher.pump_id, Pump.owner_id == current_user.id)
    if not pump:
        raise HTTPException(status_code=403, detail="Not authorized to reject this voucher")

    if voucher.status != "pending":
        raise HTTPException(status_code=400, detail=f"Voucher is not pending. Current status: {voucher.status}")

    voucher.status = "rejected"
    voucher.remarks = reason
    voucher.reviewed_at = datetime.utcnow()
    voucher.reviewed_by = current_user.id
    await voucher.save()

    log.info("Fuel voucher rejected", voucher_id=voucher_id, rejected_by=str(current_user.id), reason=reason)

    return {
        "success": True,
        "message": "Voucher rejected successfully",
        "voucher_id": voucher_id,
        "status": "rejected"
    }