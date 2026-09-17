"""
FILE: src/api/v1/credit.py
Fully migrated to async Beanie (MongoDB ODM).
"""

import hashlib
import random
import string
import json
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Query, Form
from pydantic import BaseModel
from typing import Optional, List, Any
from beanie import PydanticObjectId
from beanie.operators import In

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.credit_request import CreditRequest
from src.db.models.vehicle import Vehicle
from src.db.models.customer import Customer
from src.db.schemas.credit_request import CreditRequestCreate, CreditRequestResponse
from src.db.models.udhaar import (
    UdhaarCustomer, UdhaarVehicle, UdhaarContract, UdhaarKYCDocument,
    ContractStatus, BillingFrequency, BillingCycle, BillBy, KYCStatus,
    UdhaarItemLimit, UdhaarCustomCondition, UdhaarSlipBooklet,
)

UPLOAD_DIR = "public/uploads/deposit_proofs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter(prefix="/credit", tags=["credit"])


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()

def _generate_otp(length: int = 6) -> str:
    return ''.join(random.choices(string.digits, k=length))

async def _get_request_with_auth(request_id: str, user: User, role: str) -> CreditRequest:
    req = await CreditRequest.get(PydanticObjectId(request_id))
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if role == "logistic" and str(req.logistic_partner_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    if role == "pump_owner":
        from src.db.models.pump import Pump
        pump = await Pump.find_one(Pump.id == req.pump_id, Pump.owner_id == PydanticObjectId(user.id))
        if not pump:
            raise HTTPException(status_code=403, detail="Not authorized")
    return req

def _normalize_registration_type(vehicle_type: Optional[str]) -> str:
    private_types = {"car", "private", "lmv", "private_car", "suv", "sedan", "hatchback", "mpv"}
    if vehicle_type and vehicle_type.lower().strip() in private_types:
        return "private"
    return "commercial"

def _parse_iso_datetime(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, str) and val.endswith('Z'):
        val = val[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is not None:
            from datetime import timezone
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None

async def _get_or_create_udhaar_customer(pump_id: PydanticObjectId, partner: User) -> UdhaarCustomer:
    uc = await UdhaarCustomer.find_one(
        UdhaarCustomer.pump_id == pump_id,
        UdhaarCustomer.contact_email == partner.email,
        UdhaarCustomer.deleted_at == None
    )
    if not uc:
        uc = UdhaarCustomer(
            pump_id=pump_id,
            customer_type="commercial",
            name=partner.full_name or partner.email,
            contact_name=partner.full_name,
            contact_phone=partner.phone or None,
            contact_email=partner.email,
            kyc_status="pending",
        )
        await uc.insert()
    return uc

def _serialize_req(req: CreditRequest, vehicles: list, pump, partner) -> dict:
    return {
        "id": str(req.id),
        "status": req.status,
        "requested_limit": req.requested_limit,
        "approved_limit": req.approved_limit,
        "credit_limit": req.credit_limit,
        "remarks": req.remarks,
        "requested_at": req.requested_at.isoformat() if req.requested_at else None,
        "reviewed_at": req.reviewed_at.isoformat() if req.reviewed_at else None,
        "deposit_amount": req.deposit_amount,
        "deposit_proof_url": req.deposit_proof_url,
        "proof_doc_type": req.proof_doc_type,
        "deposit_confirmed": req.deposit_confirmed,
        "deposit_confirmed_at": req.deposit_confirmed_at.isoformat() if req.deposit_confirmed_at else None,
        "logistic_contract_data": json.loads(req.logistic_contract_data) if req.logistic_contract_data else None,
        "contract_terms": json.loads(req.contract_terms) if req.contract_terms else None,
        "valid_from": req.valid_from.isoformat() if req.valid_from else None,
        "valid_to": req.valid_to.isoformat() if req.valid_to else None,
        "logistic_signed": req.logistic_signed,
        "logistic_signed_at": req.logistic_signed_at.isoformat() if req.logistic_signed_at else None,
        "pump_owner_signed": req.pump_owner_signed,
        "pump_owner_signed_at": req.pump_owner_signed_at.isoformat() if req.pump_owner_signed_at else None,
        "activated_at": req.activated_at.isoformat() if req.activated_at else None,
        "vehicle_ids": [str(vid) for vid in req.vehicle_ids],
        "vehicle_plates": req.vehicle_plates,
        "vehicles": [
            {
                "id": str(v.id),
                "plate": v.vehicle_plate,
                "type": v.vehicle_type,
                "make_model": v.make_model,
                "driver": v.driver_name,
            }
            for v in vehicles if v
        ],
        "vehicle": {
            "id": str(vehicles[0].id) if vehicles else None,
            "plate": vehicles[0].vehicle_plate if vehicles else None,
            "type": vehicles[0].vehicle_type if vehicles else None,
            "make_model": vehicles[0].make_model if vehicles else None,
            "driver": vehicles[0].driver_name if vehicles else None,
        } if vehicles else None,
        "pump": {
            "id": str(pump.id) if pump else None,
            "name": pump.name if pump else None,
            "address": pump.address if pump else None,
            "city": pump.city if pump else None,
        },
        "partner": {
            "id": str(partner.id) if partner else None,
            "name": partner.full_name or partner.email if partner else None,
            "phone": partner.phone if partner else None,
            "email": partner.email if partner else None,
        }
    }


# ══════════════════════════════════════════════════════════════
# LOGISTIC — REQUEST
# ══════════════════════════════════════════════════════════════

@router.post("/request")
async def create_credit_request(
    request_data: CreditRequestCreate,
    current_user: User = Depends(require_role(["logistic"]))
):
    vehicles = []
    vehicle_plates = []
    vehicle_oids = []

    for vid_str in request_data.vehicle_ids:
        vid = PydanticObjectId(vid_str)
        vehicle = await Vehicle.find_one(
            Vehicle.id == vid,
            Vehicle.partner_id == PydanticObjectId(current_user.id)
        )
        if not vehicle:
            raise HTTPException(status_code=403, detail=f"Vehicle {vid_str} does not belong to you or does not exist")
        vehicles.append(vehicle)
        vehicle_plates.append(vehicle.vehicle_plate)
        vehicle_oids.append(vid)

    credit_request = CreditRequest(
        logistic_partner_id=PydanticObjectId(current_user.id),
        vehicle_ids=vehicle_oids,
        vehicle_plates=vehicle_plates,
        pump_id=PydanticObjectId(request_data.pump_id),
        requested_limit=request_data.requested_limit,
        remarks=request_data.remarks,
        status="pending"
    )
    await credit_request.insert()
    from src.db.models.pump import Pump
    pump = await Pump.get(credit_request.pump_id)
    partner = await User.get(credit_request.logistic_partner_id)
    
    return _serialize_req(credit_request, vehicles, pump, partner)


@router.get("/request")
async def get_logistic_credit_requests(
    pump_id: Optional[str] = Query(None, description="Filter by pump station ID"),
    current_user: User = Depends(require_role(["logistic"]))
):
    from src.db.models.pump import Pump

    find_query = {"logistic_partner_id": PydanticObjectId(current_user.id)}
    if pump_id is not None:
        find_query["pump_id"] = PydanticObjectId(pump_id)

    reqs = await CreditRequest.find(find_query).sort("-requested_at").to_list()

    # Flatten vehicle IDs to fetch
    flat_vehicle_ids = []
    for r in reqs:
        vids = getattr(r, "vehicle_ids", [])
        if vids:
            flat_vehicle_ids.extend(vids)
        elif getattr(r, "vehicle_id", None):
            flat_vehicle_ids.append(r.vehicle_id)
    flat_vehicle_ids = list(set(flat_vehicle_ids))

    pump_ids = list({r.pump_id for r in reqs})

    vehicles = await Vehicle.find(In(Vehicle.id, flat_vehicle_ids)).to_list()
    pumps = await Pump.find(In(Pump.id, pump_ids)).to_list()

    vehicle_map = {v.id: v for v in vehicles}
    pump_map = {p.id: p for p in pumps}

    return [
        {
            "id": str(req.id),
            "vehicle_plate": ", ".join(req.vehicle_plates) if getattr(req, "vehicle_plates", None) else (
                vehicle_map.get(req.vehicle_id).vehicle_plate if getattr(req, "vehicle_id", None) and vehicle_map.get(req.vehicle_id) else "Unknown"
            ),
            "vehicle_plates": getattr(req, "vehicle_plates", []) or ([vehicle_map[req.vehicle_id].vehicle_plate] if getattr(req, "vehicle_id", None) and req.vehicle_id in vehicle_map else []),
            "vehicle_ids": [str(v) for v in getattr(req, "vehicle_ids", [])] or ([str(req.vehicle_id)] if getattr(req, "vehicle_id", None) else []),
            "pump_name": pump_map.get(req.pump_id).name if pump_map.get(req.pump_id) else "Unknown",
            "requested_limit": req.requested_limit,
            "approved_limit": req.approved_limit,
            "status": req.status,
            "remarks": req.remarks,
            "requested_at": req.requested_at.isoformat() if req.requested_at else None,
            "deposit_amount": req.deposit_amount,
            "deposit_confirmed": req.deposit_confirmed,
            "deposit_proof_url": req.deposit_proof_url,
            "proof_doc_type": req.proof_doc_type,
            "logistic_signed": req.logistic_signed,
            "pump_owner_signed": req.pump_owner_signed,
            "logistic_contract_data": json.loads(req.logistic_contract_data) if req.logistic_contract_data else None,
            "contract_terms": json.loads(req.contract_terms) if req.contract_terms else None,
            "valid_from": req.valid_from.isoformat() if req.valid_from else None,
            "valid_to": req.valid_to.isoformat() if req.valid_to else None,
            "activated_at": req.activated_at.isoformat() if req.activated_at else None,
        }
        for req in reqs
    ]


@router.get("/request/{request_id}")
async def get_credit_request_detail(
    request_id: str,
    current_user: User = Depends(require_role(["logistic", "pump_owner"]))
):
    from src.db.models.pump import Pump

    req = await CreditRequest.get(PydanticObjectId(request_id))
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    vids = getattr(req, "vehicle_ids", []) or ([req.vehicle_id] if getattr(req, "vehicle_id", None) else [])
    vehicles = await Vehicle.find(In(Vehicle.id, vids)).to_list()
    pump = await Pump.get(req.pump_id)
    partner = await User.get(req.logistic_partner_id)

    uc = None
    if partner:
        uc = await UdhaarCustomer.find_one(
            UdhaarCustomer.pump_id == req.pump_id,
            UdhaarCustomer.contact_email == partner.email,
            UdhaarCustomer.deleted_at == None
        )

    result = _serialize_req(req, vehicles, pump, partner)
    result["udhaar_customer_id"] = str(uc.id) if uc else None
    return result


# ══════════════════════════════════════════════════════════════
# LOGISTIC — UPLOAD DEPOSIT PROOF + CONTRACT WIZARD DATA
# ══════════════════════════════════════════════════════════════

@router.post("/upload-deposit/{request_id}")
async def upload_deposit_proof(
    request_id: str,
    file: UploadFile = File(...),
    proof_doc_type: Optional[str] = Form("gst"),
    contract_wizard_json: Optional[str] = Form(None),
    current_user: User = Depends(require_role(["logistic"]))
):
    req = await _get_request_with_auth(request_id, current_user, "logistic")

    if req.status != "deposit_pending":
        raise HTTPException(status_code=400, detail=f"Cannot upload deposit in status: {req.status}")

    allowed = {".png", ".jpg", ".jpeg", ".pdf", ".webp"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: PNG, JPG, PDF, WEBP")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    import uuid
    filename = f"deposit_{request_id}_{uuid.uuid4().hex[:8]}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(contents)

    req.deposit_proof_url = f"/uploads/deposit_proofs/{filename}"
    req.proof_doc_type = proof_doc_type

    if contract_wizard_json:
        try:
            parsed = json.loads(contract_wizard_json)
            req.logistic_contract_data = json.dumps(parsed)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid contract_wizard_json format")

    await req.save()

    return {
        "message": "Deposit proof uploaded successfully",
        "deposit_proof_url": req.deposit_proof_url,
        "proof_doc_type": req.proof_doc_type,
        "next_step": "Waiting for pump owner to confirm deposit and build contract"
    }


# ══════════════════════════════════════════════════════════════
# PUMP OWNER — PENDING / ALL REQUESTS
# ══════════════════════════════════════════════════════════════

@router.get("/pending")
async def get_pending_credit_requests(
    pump_id: Optional[str] = Query(None),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    from src.db.models.pump import Pump

    pumps = await Pump.find(Pump.owner_id == PydanticObjectId(current_user.id)).to_list()
    pump_ids = [p.id for p in pumps]
    pump_map = {p.id: p for p in pumps}

    find_query = {
        "pump_id": {"$in": pump_ids},
        "$or": [
            {"status": "pending"},
            {"status": "deposit_pending", "deposit_proof_url": {"$ne": None}},
            {"status": "contract_generated", "pump_owner_signed": False},
            {"status": "logistic_signed"}
        ]
    }
    if pump_id is not None:
        find_query["pump_id"] = PydanticObjectId(pump_id)

    reqs = await CreditRequest.find(find_query).sort("-requested_at").to_list()

    partner_ids = list({r.logistic_partner_id for r in reqs})
    partners = await User.find(In(User.id, partner_ids)).to_list()
    partner_map = {partner.id: partner for partner in partners}

    flat_vehicle_ids = []
    for r in reqs:
        flat_vehicle_ids.extend(getattr(r, "vehicle_ids", []) or ([r.vehicle_id] if getattr(r, "vehicle_id", None) else []))
    flat_vehicle_ids = list(set(flat_vehicle_ids))

    vehicles = await Vehicle.find(In(Vehicle.id, flat_vehicle_ids)).to_list()
    vehicle_map = {v.id: v for v in vehicles}

    response_data = []
    for req in reqs:
        partner = partner_map.get(req.logistic_partner_id)
        pump = pump_map.get(req.pump_id)
        if not partner or not pump:
            continue

        req_vids = getattr(req, "vehicle_ids", []) or ([req.vehicle_id] if getattr(req, "vehicle_id", None) else [])
        req_vehicles = [vehicle_map[vid] for vid in req_vids if vid in vehicle_map]
        if not req_vehicles:
            continue

        primary_vehicle = req_vehicles[0]

        uc = await UdhaarCustomer.find_one(
            UdhaarCustomer.pump_id == req.pump_id,
            UdhaarCustomer.contact_email == partner.email,
            UdhaarCustomer.deleted_at == None
        )

        response_data.append({
            "id": str(req.id),
            "logistic_partner_id": str(req.logistic_partner_id),
            "logistic_partner_name": partner.full_name or partner.email,
            "logistic_partner_phone": partner.phone or "N/A",
            "logistic_partner_email": partner.email,
            "vehicle_plate": ", ".join([v.vehicle_plate for v in req_vehicles]),
            "vehicle_plates": [v.vehicle_plate for v in req_vehicles],
            "vehicle_ids": [str(v.id) for v in req_vehicles],
            "vehicle_type": primary_vehicle.vehicle_type,
            "make_model": primary_vehicle.make_model,
            "driver_name": primary_vehicle.driver_name or "N/A",
            "pump_id": str(req.pump_id),
            "requested_limit": req.requested_limit,
            "approved_limit": req.approved_limit,
            "deposit_amount": req.deposit_amount,
            "deposit_proof_url": req.deposit_proof_url,
            "proof_doc_type": req.proof_doc_type,
            "deposit_confirmed": req.deposit_confirmed,
            "logistic_signed": req.logistic_signed,
            "pump_owner_signed": req.pump_owner_signed,
            "logistic_contract_data": json.loads(req.logistic_contract_data) if req.logistic_contract_data else None,
            "contract_terms": json.loads(req.contract_terms) if req.contract_terms else None,
            "status": req.status,
            "remarks": req.remarks,
            "requested_at": req.requested_at.isoformat() if req.requested_at else None,
            "activated_at": req.activated_at.isoformat() if req.activated_at else None,
            "udhaar_customer_id": str(uc.id) if uc else None,
        })
    return response_data


@router.get("/all")
async def get_all_credit_requests(
    pump_id: str = Query(...),
    status: Optional[str] = Query(None),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    from src.db.models.pump import Pump
    pump = await Pump.find_one(Pump.id == PydanticObjectId(pump_id), Pump.owner_id == PydanticObjectId(current_user.id))
    if not pump:
        raise HTTPException(status_code=403, detail="Not authorized")

    find_query = {"pump_id": PydanticObjectId(pump_id)}
    if status:
        find_query["status"] = status

    reqs = await CreditRequest.find(find_query).sort("-requested_at").to_list()

    partner_ids = list({r.logistic_partner_id for r in reqs})
    partners = await User.find(In(User.id, partner_ids)).to_list()
    partner_map = {partner.id: partner for partner in partners}

    flat_vehicle_ids = []
    for r in reqs:
        flat_vehicle_ids.extend(getattr(r, "vehicle_ids", []) or ([r.vehicle_id] if getattr(r, "vehicle_id", None) else []))
    flat_vehicle_ids = list(set(flat_vehicle_ids))

    vehicles = await Vehicle.find(In(Vehicle.id, flat_vehicle_ids)).to_list()
    vehicle_map = {v.id: v for v in vehicles}

    response = []
    for req in reqs:
        partner = partner_map.get(req.logistic_partner_id)
        if not partner:
            continue

        req_vids = getattr(req, "vehicle_ids", []) or ([req.vehicle_id] if getattr(req, "vehicle_id", None) else [])
        req_vehicles = [vehicle_map[vid] for vid in req_vids if vid in vehicle_map]
        if not req_vehicles:
            continue

        primary_vehicle = req_vehicles[0]

        uc = await UdhaarCustomer.find_one(
            UdhaarCustomer.pump_id == req.pump_id,
            UdhaarCustomer.contact_email == partner.email,
            UdhaarCustomer.deleted_at == None
        )

        response.append({
            "id": str(req.id),
            "status": req.status,
            "partner_name": partner.full_name or partner.email,
            "partner_phone": partner.phone or "N/A",
            "partner_email": partner.email,
            "vehicle_plate": ", ".join([v.vehicle_plate for v in req_vehicles]),
            "vehicle_plates": [v.vehicle_plate for v in req_vehicles],
            "vehicle_ids": [str(v.id) for v in req_vehicles],
            "vehicle_type": primary_vehicle.vehicle_type,
            "requested_limit": req.requested_limit,
            "approved_limit": req.approved_limit,
            "deposit_amount": req.deposit_amount,
            "deposit_confirmed": req.deposit_confirmed,
            "proof_doc_type": req.proof_doc_type,
            "logistic_signed": req.logistic_signed,
            "pump_owner_signed": req.pump_owner_signed,
            "contract_terms": json.loads(req.contract_terms) if req.contract_terms else None,
            "activated_at": req.activated_at.isoformat() if req.activated_at else None,
            "requested_at": req.requested_at.isoformat() if req.requested_at else None,
            "udhaar_customer_id": str(uc.id) if uc else None,
        })
    return response


# ══════════════════════════════════════════════════════════════
# PUMP OWNER — APPROVE / REJECT
# ══════════════════════════════════════════════════════════════

@router.post("/approve/{request_id}")
async def approve_credit_request(
    request_id: str,
    approved_limit: float,
    deposit_required: float = 0.0,
    valid_days: int = 365,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    req = await _get_request_with_auth(request_id, current_user, "pump_owner")

    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is not pending. Current: {req.status}")

    req.approved_limit = approved_limit
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by = PydanticObjectId(current_user.id)
    req.valid_from = datetime.utcnow()
    req.valid_to = datetime.utcnow() + timedelta(days=valid_days)

    if deposit_required > 0:
        req.deposit_amount = deposit_required
        req.status = "deposit_pending"
        next_step = "logistic_upload_deposit"
    else:
        req.status = "contract_generated"
        req.deposit_confirmed = True
        next_step = "pump_owner_build_contract"

    req_vids = getattr(req, "vehicle_ids", []) or ([req.vehicle_id] if getattr(req, "vehicle_id", None) else [])
    for vid in req_vids:
        vehicle = await Vehicle.get(vid)
        if vehicle:
            vehicle.credit_limit = approved_limit
            await vehicle.save()

    partner = await User.get(req.logistic_partner_id)
    if partner:
        uc = await _get_or_create_udhaar_customer(req.pump_id, partner)
        for vid in req_vids:
            vehicle = await Vehicle.get(vid)
            if vehicle:
                existing_uv = await UdhaarVehicle.find_one(
                    UdhaarVehicle.customer_id == uc.id,
                    UdhaarVehicle.number_plate == vehicle.vehicle_plate.upper().strip(),
                    UdhaarVehicle.deleted_at == None
                )
                if not existing_uv:
                    uv = UdhaarVehicle(
                        customer_id=uc.id,
                        pump_id=req.pump_id,
                        number_plate=vehicle.vehicle_plate.upper().strip(),
                        registration_type=_normalize_registration_type(vehicle.vehicle_type),
                        make=vehicle.make_model,
                        fuel_types=["diesel", "petrol"],
                    )
                    await uv.insert()

    await req.save()

    return {
        "message": "Credit request approved",
        "approved_limit": approved_limit,
        "status": req.status,
        "next_step": next_step,
    }


@router.post("/reject/{request_id}")
async def reject_credit_request(
    request_id: str,
    reason: str = "Request rejected by pump owner",
    current_user: User = Depends(require_role(["pump_owner"]))
):
    req = await _get_request_with_auth(request_id, current_user, "pump_owner")
    req.status = "rejected"
    req.remarks = reason
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by = PydanticObjectId(current_user.id)
    await req.save()
    return {"message": "Request rejected", "reason": reason}


# ══════════════════════════════════════════════════════════════
# PUMP OWNER — CONFIRM DEPOSIT + BUILD CONTRACT (WIZARD)
# ══════════════════════════════════════════════════════════════

class FullContractPayload(BaseModel):
    station_name: Optional[str] = None
    org_name: Optional[str] = None
    address: Optional[str] = None
    gst_number: Optional[str] = None
    valid_to: str  # ISO date string
    security_deposit: Optional[float] = 0.0
    total_credit_limit: float
    max_spending_slips: Optional[int] = None
    money_limit_per_fill: Optional[float] = None
    money_limit_per_day: Optional[float] = None
    money_limit_per_cycle: Optional[float] = None
    billing_frequency: Optional[str] = "recurring"
    bill_by: Optional[str] = "customer"
    billing_cycle: Optional[str] = "monthly"
    billing_start_date: Optional[str] = None
    round_off: Optional[bool] = False
    require_meter_photo: Optional[bool] = False
    require_vehicle_photo: Optional[bool] = False
    require_fueling_video: Optional[bool] = False
    require_driver_verification: Optional[bool] = False
    sop_recipients: Optional[List[dict]] = []
    late_payment_interest: Optional[float] = 2.0
    deposit_utilization_days: Optional[int] = 30
    suspension_period_days: Optional[int] = 7
    invoice_dispute_days: Optional[int] = 15
    custom_terms: Optional[str] = None
    slip_booklets: Optional[List[dict]] = []
    item_limits: Optional[List[dict]] = []
    custom_conditions: Optional[List[dict]] = []


@router.post("/confirm-deposit/{request_id}")
async def confirm_deposit_and_build_contract(
    request_id: str,
    payload: FullContractPayload,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    req = await _get_request_with_auth(request_id, current_user, "pump_owner")

    if req.status != "deposit_pending":
        raise HTTPException(status_code=400, detail=f"Not in deposit_pending status: {req.status}")

    if not req.deposit_proof_url:
        raise HTTPException(status_code=400, detail="Logistic partner has not uploaded deposit proof yet")

    req.deposit_confirmed = True
    req.deposit_confirmed_at = datetime.utcnow()
    req.deposit_confirmed_by = PydanticObjectId(current_user.id)
    req.status = "contract_generated"
    req.contract_terms = json.dumps(payload.dict())

    parsed_valid_to = _parse_iso_datetime(payload.valid_to)
    if parsed_valid_to:
        req.valid_to = parsed_valid_to
    else:
        req.valid_to = datetime.utcnow() + timedelta(days=365)

    await req.save()

    return {
        "message": "Deposit confirmed. Contract built.",
        "status": req.status,
        "next_step": "pump_owner_sign_contract"
    }


@router.post("/build-contract/{request_id}")
async def build_contract(
    request_id: str,
    payload: FullContractPayload,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    req = await _get_request_with_auth(request_id, current_user, "pump_owner")

    if req.status != "contract_generated":
        raise HTTPException(status_code=400, detail=f"Not in contract_generated status: {req.status}")

    req.contract_terms = json.dumps(payload.dict())

    parsed_valid_to = _parse_iso_datetime(payload.valid_to)
    if parsed_valid_to:
        req.valid_to = parsed_valid_to
    else:
        req.valid_to = datetime.utcnow() + timedelta(days=365)

    await req.save()

    return {
        "message": "Contract saved.",
        "status": req.status,
        "next_step": "pump_owner_sign_contract"
    }


# ══════════════════════════════════════════════════════════════
# OTP + SIGNING
# ══════════════════════════════════════════════════════════════

@router.post("/send-otp/{request_id}")
async def send_signing_otp(
    request_id: str,
    signing_as: str = Query(..., description="'logistic' or 'pump_owner'"),
    current_user: User = Depends(require_role(["logistic", "pump_owner"]))
):
    req = await CreditRequest.get(PydanticObjectId(request_id))
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    if signing_as not in ["logistic", "pump_owner"]:
        raise HTTPException(status_code=400, detail="signing_as must be 'logistic' or 'pump_owner'")

    signable = req.status in ["contract_generated", "pump_signed", "logistic_signed"]
    if not signable:
        raise HTTPException(status_code=400, detail=f"Contract not ready for signing: {req.status}")

    otp = _generate_otp(6)
    otp_hash = _hash_otp(otp)

    if signing_as == "logistic":
        if str(current_user.id) != str(req.logistic_partner_id):
            raise HTTPException(status_code=403, detail="You are not the logistic partner")
        req.logistic_otp_hash = otp_hash
    else:
        from src.db.models.pump import Pump
        pump = await Pump.find_one(Pump.id == req.pump_id, Pump.owner_id == PydanticObjectId(current_user.id))
        if not pump:
            raise HTTPException(status_code=403, detail="You are not the pump owner")
        req.pump_owner_otp_hash = otp_hash

    await req.save()
    return {
        "message": "OTP generated",
        "otp": otp,  # REMOVE IN PRODUCTION
        "note": "In production this will be sent via SMS/Email"
    }


class SignContractRequest(BaseModel):
    otp: str
    ip_address: Optional[str] = None


@router.post("/sign/{request_id}")
async def sign_contract(
    request_id: str,
    data: SignContractRequest,
    request: Request,
    signing_as: str = Query(..., description="'logistic' or 'pump_owner'"),
    current_user: User = Depends(require_role(["logistic", "pump_owner"]))
):
    req = await CreditRequest.get(PydanticObjectId(request_id))
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    if not req.contract_terms:
        raise HTTPException(status_code=400, detail="Contract not built yet.")

    signable_statuses = ["contract_generated", "pump_signed", "logistic_signed"]
    if req.status not in signable_statuses:
        raise HTTPException(status_code=400, detail=f"Contract not in signable state: {req.status}")

    # ✅ signing_as param se decide karo — ID se nahi
    if signing_as not in ["logistic", "pump_owner"]:
        raise HTTPException(status_code=400, detail="signing_as must be 'logistic' or 'pump_owner'")

    client_ip = request.client.host if request.client else data.ip_address or "unknown"
    otp_hash = _hash_otp(data.otp)
    now = datetime.utcnow()

    if signing_as == "logistic":
        # ── LOGISTIC PARTNER SIGNING ─────────────────────────────
        # Verify user is actually the logistic partner
        if str(current_user.id) != str(req.logistic_partner_id):
            raise HTTPException(status_code=403, detail="You are not the logistic partner for this request")

        if req.logistic_signed:
            raise HTTPException(status_code=400, detail="You have already signed this contract")

        if not req.logistic_otp_hash:
            raise HTTPException(status_code=400, detail="Request OTP first")

        if req.logistic_otp_hash != otp_hash:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        req.logistic_signed = True
        req.logistic_signed_at = now
        req.logistic_sign_ip = client_ip
        req.logistic_otp_hash = None

        if req.pump_owner_signed:
            await _activate_contract(req, now)
        else:
            req.status = "logistic_signed"

        await req.save()
        return {
            "message": "Contract signed by logistic partner" + (". Contract is now ACTIVE." if req.pump_owner_signed else ""),
            "status": req.status,
            "logistic_signed": req.logistic_signed,
            "pump_owner_signed": req.pump_owner_signed,
            "next_step": None if req.pump_owner_signed else "Pump owner must now review and sign"
        }

    else:
        # ── PUMP OWNER SIGNING ───────────────────────────────────
        from src.db.models.pump import Pump
        pump = await Pump.find_one(Pump.id == req.pump_id, Pump.owner_id == PydanticObjectId(current_user.id))
        if not pump:
            raise HTTPException(status_code=403, detail="You are not the pump owner for this request")

        if req.pump_owner_signed:
            raise HTTPException(status_code=400, detail="You have already signed this contract")

        if not req.pump_owner_otp_hash:
            raise HTTPException(status_code=400, detail="Request OTP first")

        if req.pump_owner_otp_hash != otp_hash:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        req.pump_owner_signed = True
        req.pump_owner_signed_at = now
        req.pump_owner_sign_ip = client_ip
        req.pump_owner_otp_hash = None

        if req.logistic_signed:
            await _activate_contract(req, now)
        else:
            req.status = "pump_signed"

        await req.save()
        return {
            "message": "Contract signed by pump owner" + (". Contract is now ACTIVE." if req.logistic_signed else ""),
            "status": req.status,
            "logistic_signed": req.logistic_signed,
            "pump_owner_signed": req.pump_owner_signed,
            "next_step": None if req.logistic_signed else "Logistic partner must now review and sign"
        }


# ══════════════════════════════════════════════════════════════
# INTERNAL — ACTIVATE CONTRACT
# ══════════════════════════════════════════════════════════════

async def _activate_contract(req: CreditRequest, now: datetime):
    """
    Dono sign ho gaye:
    1. status → active
    2. UdhaarContract create karo full wizard data se
    3. ItemLimits, Conditions, SlipBooklets save karo
    4. KYC document (proof) UdhaarKYCDocument mein save karo
    """
    req.status = "active"
    req.activated_at = now
    req.credit_limit = req.approved_limit

    partner = await User.get(req.logistic_partner_id)
    if not partner:
        return

    from src.db.models.pump import Pump
    pump = await Pump.get(req.pump_id)
    req_vids = getattr(req, "vehicle_ids", []) or ([req.vehicle_id] if getattr(req, "vehicle_id", None) else [])

    # UdhaarCustomer fetch/create
    uc = await _get_or_create_udhaar_customer(req.pump_id, partner)

    # Vehicle link
    for vid in req_vids:
        vehicle = await Vehicle.get(vid)
        if vehicle:
            existing_uv = await UdhaarVehicle.find_one(
                UdhaarVehicle.customer_id == uc.id,
                UdhaarVehicle.number_plate == vehicle.vehicle_plate.upper().strip(),
                UdhaarVehicle.deleted_at == None
            )
            if not existing_uv:
                uv = UdhaarVehicle(
                    customer_id=uc.id,
                    pump_id=req.pump_id,
                    number_plate=vehicle.vehicle_plate.upper().strip(),
                    registration_type=_normalize_registration_type(vehicle.vehicle_type),
                    make=vehicle.make_model,
                    fuel_types=["diesel", "petrol"],
                )
                await uv.insert()

    # KYC document save
    if req.deposit_proof_url and req.proof_doc_type:
        existing_kyc = await UdhaarKYCDocument.find_one(
            UdhaarKYCDocument.customer_id == uc.id,
            UdhaarKYCDocument.image_url == req.deposit_proof_url
        )
        if not existing_kyc:
            kyc = UdhaarKYCDocument(
                customer_id=uc.id,
                document_type=req.proof_doc_type,
                image_url=req.deposit_proof_url,
                status=KYCStatus.accepted,
            )
            await kyc.insert()
        uc.kyc_status = KYCStatus.accepted
        await uc.save()

    # Parse contract terms
    terms = {}
    if req.contract_terms:
        try:
            terms = json.loads(req.contract_terms)
        except Exception:
            pass

    valid_from = req.valid_from or now
    valid_to = req.valid_to or (now + timedelta(days=365))

    # Billing enums
    bf_map = {"one_time": BillingFrequency.one_time, "recurring": BillingFrequency.recurring}
    billing_frequency = bf_map.get(terms.get("billing_frequency", "recurring"), BillingFrequency.recurring)

    bc_map = {"weekly": BillingCycle.weekly, "fortnightly": BillingCycle.fortnightly, "monthly": BillingCycle.monthly}
    billing_cycle = bc_map.get(terms.get("billing_cycle", "monthly"), BillingCycle.monthly)

    bb_map = {"vehicle": BillBy.vehicle, "customer": BillBy.customer}
    bill_by = bb_map.get(terms.get("bill_by", "customer"), BillBy.customer)

    # Create UdhaarContract
    contract = UdhaarContract(
        customer_id=uc.id,
        pump_id=req.pump_id,
        version=1,
        station_name=terms.get("station_name") or (pump.name if pump else None),
        org_name=terms.get("org_name") or (pump.name if pump else None),
        address=terms.get("address") or (pump.address if pump else None),
        gst_number=terms.get("gst_number") or (pump.gst if pump else None),
        valid_from=valid_from,
        valid_to=valid_to,
        security_deposit=float(terms.get("security_deposit") or req.deposit_amount or 0),
        total_credit_limit=float(terms.get("total_credit_limit") or req.approved_limit or 0),
        max_spending_slips=terms.get("max_spending_slips"),
        money_limit_per_fill=terms.get("money_limit_per_fill"),
        money_limit_per_day=terms.get("money_limit_per_day"),
        money_limit_per_cycle=terms.get("money_limit_per_cycle"),
        billing_frequency=billing_frequency,
        billing_cycle=billing_cycle,
        bill_by=bill_by,
        billing_start_date=_parse_iso_datetime(terms.get("billing_start_date")),
        round_off=bool(terms.get("round_off", False)),
        require_meter_photo=bool(terms.get("require_meter_photo", False)),
        require_vehicle_photo=bool(terms.get("require_vehicle_photo", False)),
        require_fueling_video=bool(terms.get("require_fueling_video", False)),
        require_driver_verification=bool(terms.get("require_driver_verification", False)),
        sop_recipients=terms.get("sop_recipients", []),
        late_payment_interest=float(terms.get("late_payment_interest") or 2.0),
        deposit_utilization_days=int(terms.get("deposit_utilization_days") or 30),
        suspension_period_days=int(terms.get("suspension_period_days") or 7),
        invoice_dispute_days=int(terms.get("invoice_dispute_days") or 15),
        custom_terms=terms.get("custom_terms"),
        status=ContractStatus.active,
        created_by=req.reviewed_by,
    )
    await contract.insert()

    # Item Limits
    for il in terms.get("item_limits", []):
        if il.get("item_name"):
            limit_item = UdhaarItemLimit(
                contract_id=contract.id,
                item_name=il["item_name"],
                qty_per_fill=float(il["qty_per_fill"]) if il.get("qty_per_fill") else None,
                qty_per_day=float(il["qty_per_day"]) if il.get("qty_per_day") else None,
                qty_per_cycle=float(il["qty_per_cycle"]) if il.get("qty_per_cycle") else None,
            )
            await limit_item.insert()

    # Custom Conditions
    for cc in terms.get("custom_conditions", []):
        condition = UdhaarCustomCondition(
            contract_id=contract.id,
            vehicle_type=cc.get("vehicle_type"),
            item_name=cc.get("item_name"),
            station_id=PydanticObjectId(cc["station_id"]) if cc.get("station_id") else None,
            max_slips=int(cc["max_slips"]) if cc.get("max_slips") else None,
            money_per_fill=float(cc["money_per_fill"]) if cc.get("money_per_fill") else None,
            money_per_day=float(cc["money_per_day"]) if cc.get("money_per_day") else None,
            money_per_cycle=float(cc["money_per_cycle"]) if cc.get("money_per_cycle") else None,
            qty_per_fill=float(cc["qty_per_fill"]) if cc.get("qty_per_fill") else None,
            qty_per_day=float(cc["qty_per_day"]) if cc.get("qty_per_day") else None,
            qty_per_cycle=float(cc["qty_per_cycle"]) if cc.get("qty_per_cycle") else None,
        )
        await condition.insert()

    # Slip Booklets
    for sb in terms.get("slip_booklets", []):
        if sb.get("booklet_number") and sb.get("start_number") and sb.get("end_number"):
            booklet = UdhaarSlipBooklet(
                contract_id=contract.id,
                booklet_number=sb["booklet_number"],
                start_number=int(sb["start_number"]),
                end_number=int(sb["end_number"]),
                total_slips=int(sb["end_number"]) - int(sb["start_number"]) + 1,
            )
            await booklet.insert()

    # Customer credit limit update
    uc.credit_limit = float(terms.get("total_credit_limit") or req.approved_limit or 0)
    await uc.save()

    # Old Customer model update (backward compat)
    for vid in req_vids:
        vehicle = await Vehicle.get(vid)
        if vehicle:
            old_customer = await Customer.find_one(
                Customer.vehicle_plate == vehicle.vehicle_plate,
                Customer.pump_id == req.pump_id
            )
            if old_customer:
                old_customer.credit_limit = req.approved_limit
                await old_customer.save()