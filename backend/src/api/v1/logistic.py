import os
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from beanie import PydanticObjectId
from beanie.operators import In
from typing import Optional, List
from pydantic import BaseModel
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.vehicle import Vehicle
from src.db.models.transaction import Transaction
from src.db.models.pump import Pump
from src.db.models.payment import PaymentRequest
from src.db.schemas.logistic import VehicleCreate, VehicleResponse
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/logistic", tags=["logistic"])

# ── Upload config ─────────────────────────────────────────────────────────────
DOC_UPLOAD_DIR = "public/uploads/documents"
os.makedirs(DOC_UPLOAD_DIR, exist_ok=True)
ALLOWED_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
MAX_DOC_SIZE_MB = 10
VALID_DOC_TYPES = {
    "gstin_certificate", "pan_card", "company_registration",
    "transport_license", "payment_screenshot"
}


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[str] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None


class BankAccountUpdate(BaseModel):
    account_holder: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None
    upi_id: Optional[str] = None


class VoucherCreate(BaseModel):
    vehicle_id: str
    vehicle_plate: str
    amount: float
    fuel_type: str = "Diesel"
    expiry_date: str  # ISO date string e.g. "2026-07-05"
    notes: Optional[str] = None
    pump_id: Optional[str] = None


# ── GET /logistic/profile ─────────────────────────────────────────────────────
@router.get("/profile")
async def get_logistic_profile(
    current_user: User = Depends(require_role(["logistic"], allow_unverified=True))
):
    """Return current logged-in logistic user's profile."""
    return {
        "id": str(current_user.id),
        "full_name": current_user.full_name or "",
        "email": current_user.email,
        "phone": current_user.phone or "",
        "company_name": current_user.company_name or "",
        "gstin": current_user.gstin or "",
        "billing_address": current_user.billing_address or "",
        "roles": current_user.roles,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else "",
        "verification_status": current_user.verification_status,
        "verification_notes": current_user.verification_notes,
        "kyc_documents": current_user.kyc_documents or [],
        "bank_account": {
            "account_holder": current_user.bank_account_holder or "",
            "account_number": current_user.bank_account_number or "",
            "ifsc_code": current_user.bank_ifsc_code or "",
            "bank_name": current_user.bank_name or "",
            "upi_id": current_user.bank_upi_id or "",
            "verified": current_user.bank_verified,
        },
    }


# ── PUT /logistic/profile ─────────────────────────────────────────────────────
@router.put("/profile")
async def update_logistic_profile(
    updates: ProfileUpdate,
    current_user: User = Depends(require_role(["logistic"], allow_unverified=True))
):
    """Update logistic partner's profile fields."""
    if updates.company_name is not None:
        current_user.company_name = updates.company_name
    if updates.gstin is not None:
        current_user.gstin = updates.gstin.upper()
    if updates.billing_address is not None:
        current_user.billing_address = updates.billing_address
    if updates.full_name is not None:
        current_user.full_name = updates.full_name
    if updates.phone is not None:
        current_user.phone = updates.phone

    current_user.updated_at = datetime.utcnow()
    await current_user.save()

    return {
        "id": str(current_user.id),
        "full_name": current_user.full_name or "",
        "email": current_user.email,
        "phone": current_user.phone or "",
        "company_name": current_user.company_name or "",
        "gstin": current_user.gstin or "",
        "billing_address": current_user.billing_address or "",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# KYC DOCUMENT UPLOAD / DELETE
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/documents")
async def upload_kyc_document(
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(["logistic"], allow_unverified=True))
):
    """
    Upload a KYC document (GSTIN certificate, PAN, etc.).
    Accepts multipart/form-data with `file` and `doc_type`.
    """
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type. Must be one of: {', '.join(VALID_DOC_TYPES)}")

    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ext}'. Allowed: {', '.join(ALLOWED_DOC_EXTENSIONS)}"
        )

    # Read & validate size
    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_DOC_SIZE_MB:
        raise HTTPException(status_code=400, detail=f"File too large ({size_mb:.1f}MB). Max: {MAX_DOC_SIZE_MB}MB")

    # Remove existing document of same type (if re-uploading)
    existing_docs = current_user.kyc_documents or []
    for doc in existing_docs:
        if doc.get("doc_type") == doc_type:
            old_path = doc.get("file_url", "").lstrip("/")
            if old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
    existing_docs = [d for d in existing_docs if d.get("doc_type") != doc_type]

    # Save file with unique name
    filename = f"{doc_type}_{str(current_user.id)[:8]}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(DOC_UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(contents)

    file_url = f"/uploads/documents/{filename}"
    doc_record = {
        "doc_type": doc_type,
        "file_url": file_url,
        "original_name": file.filename or filename,
        "uploaded_at": datetime.utcnow().isoformat(),
    }
    existing_docs.append(doc_record)

    current_user.kyc_documents = existing_docs
    current_user.updated_at = datetime.utcnow()
    await current_user.save()

    log.info("KYC document uploaded", doc_type=doc_type, user=current_user.email, file=filename)
    return {"success": True, "document": doc_record}


@router.delete("/documents/{doc_type}")
async def delete_kyc_document(
    doc_type: str,
    current_user: User = Depends(require_role(["logistic"], allow_unverified=True))
):
    """Remove a previously uploaded KYC document."""
    existing_docs = current_user.kyc_documents or []
    found = None
    for doc in existing_docs:
        if doc.get("doc_type") == doc_type:
            found = doc
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"No document found for type '{doc_type}'.")

    # Delete from disk
    file_path = found.get("file_url", "").lstrip("/")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    current_user.kyc_documents = [d for d in existing_docs if d.get("doc_type") != doc_type]
    current_user.updated_at = datetime.utcnow()
    await current_user.save()

    log.info("KYC document deleted", doc_type=doc_type, user=current_user.email)
    return {"success": True, "message": f"Document '{doc_type}' removed."}


# ═══════════════════════════════════════════════════════════════════════════════
# BANK ACCOUNT LINKING
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/bank-account")
async def get_bank_account(
    current_user: User = Depends(require_role(["logistic"], allow_unverified=True))
):
    """Get linked bank account details."""
    return {
        "account_holder": current_user.bank_account_holder or "",
        "account_number": current_user.bank_account_number or "",
        "ifsc_code": current_user.bank_ifsc_code or "",
        "bank_name": current_user.bank_name or "",
        "upi_id": current_user.bank_upi_id or "",
        "verified": current_user.bank_verified,
    }


@router.put("/bank-account")
async def update_bank_account(
    data: BankAccountUpdate,
    current_user: User = Depends(require_role(["logistic"], allow_unverified=True))
):
    """Save or update linked bank account details."""
    if data.account_holder is not None:
        current_user.bank_account_holder = data.account_holder.strip()
    if data.account_number is not None:
        current_user.bank_account_number = data.account_number.strip()
    if data.ifsc_code is not None:
        current_user.bank_ifsc_code = data.ifsc_code.strip().upper()
    if data.bank_name is not None:
        current_user.bank_name = data.bank_name.strip()
    if data.upi_id is not None:
        current_user.bank_upi_id = data.upi_id.strip()

    # Reset verification when bank details change
    current_user.bank_verified = False
    current_user.updated_at = datetime.utcnow()
    await current_user.save()

    log.info("Bank account updated", user=current_user.email)
    return {
        "success": True,
        "bank_account": {
            "account_holder": current_user.bank_account_holder or "",
            "account_number": current_user.bank_account_number or "",
            "ifsc_code": current_user.bank_ifsc_code or "",
            "bank_name": current_user.bank_name or "",
            "upi_id": current_user.bank_upi_id or "",
            "verified": current_user.bank_verified,
        }
    }


# ── POST /logistic/vehicles — Register a new vehicle ─────────────────────────
@router.post("/vehicles")
async def add_vehicle(
    vehicle_data: VehicleCreate,
    current_user: User = Depends(require_role(["logistic"]))
):
    try:
        vehicle = Vehicle(
            **vehicle_data.model_dump(),
            partner_id=current_user.id
        )
        await vehicle.insert()
        return {
            "id": str(vehicle.id),
            "vehicle_plate": vehicle.vehicle_plate,
            "vehicle_type": vehicle.vehicle_type,
            "make_model": vehicle.make_model,
            "fuel_type": vehicle.fuel_type,
            "driver_name": vehicle.driver_name,
            "driver_phone": vehicle.driver_phone,
            "credit_limit": vehicle.credit_limit,
            "outstanding_amount": vehicle.outstanding_amount,
            "is_active": vehicle.is_active,
            "partner_id": str(vehicle.partner_id),
        }
    except Exception as e:
        log.error("Failed to add vehicle", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to add vehicle: {str(e)}")


# ── GET /logistic/vehicles — List all fleet vehicles ─────────────────────────
@router.get("/vehicles")
async def get_my_vehicles(
    current_user: User = Depends(require_role(["logistic"]))
):
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()
    return [
        {
            "id": str(v.id),
            "vehicle_plate": v.vehicle_plate,
            "vehicle_type": v.vehicle_type,
            "make_model": v.make_model,
            "fuel_type": v.fuel_type,
            "driver_name": v.driver_name,
            "driver_phone": v.driver_phone,
            "credit_limit": v.credit_limit,
            "outstanding_amount": v.outstanding_amount,
            "is_active": v.is_active,
        }
        for v in vehicles
    ]


# ── GET /logistic/dashboard — KPI summary cards ───────────────────────────────
@router.get("/dashboard")
async def logistic_dashboard(
    current_user: User = Depends(require_role(["logistic"]))
):
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()

    total_outstanding = sum(v.outstanding_amount for v in vehicles)

    approved_topups = await PaymentRequest.find(
        PaymentRequest.logistic_partner_id == current_user.id,
        PaymentRequest.payment_type == "wallet_topup",
        PaymentRequest.status == "approved"
    ).to_list()
    total_topup = sum(p.amount for p in approved_topups)

    plates = [v.vehicle_plate for v in vehicles]
    total_wallet_spend = 0.0
    if plates:
        wallet_txns = await Transaction.find(
            In(Transaction.vehicle_plate, plates),
            Transaction.payment_mode == "wallet"
        ).to_list()
        total_wallet_spend = sum(t.amount for t in wallet_txns)

    wallet_balance = total_topup - total_wallet_spend

    return {
        "total_vehicles": len(vehicles),
        "total_outstanding": round(total_outstanding, 2),
        "active_vehicles": len([v for v in vehicles if v.is_active]),
        "this_month_fuel": round(total_outstanding + total_wallet_spend, 2),
        "wallet_balance": round(max(0.0, wallet_balance), 2),
        "recent_activity": "No recent activity" if not vehicles else "Fleet is active",
    }


# ── GET /logistic/transactions — Fuel ledger ──────────────────────────────────
@router.get("/transactions")
async def get_transactions(
    current_user: User = Depends(require_role(["logistic"]))
):
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()

    plates = [v.vehicle_plate for v in vehicles]
    vehicle_map = {v.vehicle_plate: v for v in vehicles}

    if not plates:
        return []

    txns = await Transaction.find(In(Transaction.vehicle_plate, plates)).sort(-Transaction.timestamp).to_list()

    response_data = []
    for txn in txns:
        vehicle = vehicle_map.get(txn.vehicle_plate)
        pump = await Pump.get(txn.pump_id) if txn.pump_id else None
        response_data.append({
            "id": f"TXN-{str(txn.id)[-6:]}",
            "vehicleId": str(vehicle.id) if vehicle else "",
            "vehicleNumber": txn.vehicle_plate,
            "pumpName": pump.name if pump else "Unknown",
            "fuelType": vehicle.fuel_type.lower() if vehicle and vehicle.fuel_type else "diesel",
            "quantity": txn.volume,
            "amount": txn.amount,
            "driverName": vehicle.driver_name if vehicle else "N/A",
            "paymentType": "credit" if txn.payment_mode == "credit" else "wallet",
            "date": txn.timestamp.strftime("%Y-%m-%d %H:%M") if txn.timestamp else "",
            "balanceRemaining": (vehicle.credit_limit - vehicle.outstanding_amount) if vehicle else 0,
        })
    return response_data


# ── GET /logistic/wallet — Prepaid wallet state ───────────────────────────────
@router.get("/wallet")
async def get_wallet(
    current_user: User = Depends(require_role(["logistic"]))
):
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()

    approved_topups = await PaymentRequest.find(
        PaymentRequest.logistic_partner_id == current_user.id,
        PaymentRequest.payment_type == "wallet_topup",
        PaymentRequest.status == "approved"
    ).to_list()
    total_topup = sum(p.amount for p in approved_topups)

    plates = [v.vehicle_plate for v in vehicles]
    total_wallet_spend = 0.0
    if plates:
        wallet_txns = await Transaction.find(
            In(Transaction.vehicle_plate, plates),
            Transaction.payment_mode == "wallet"
        ).to_list()
        total_wallet_spend = sum(t.amount for t in wallet_txns)

    history_records = await PaymentRequest.find(
        PaymentRequest.logistic_partner_id == current_user.id
    ).sort(-PaymentRequest.requested_at).to_list()

    txn_history = []
    for item in history_records:
        remarks = item.remarks or ""
        is_stripe = "Stripe" in remarks or "stripe" in remarks.lower()
        txn_history.append({
            "id": f"REF-{str(item.id)[-6:]}",
            "referenceId": item.transaction_reference or f"TXN-{str(item.id)[-6:]}",
            "paymentMethod": "Visa Ending in 4242" if is_stripe else "Bank Transfer / Manual",
            "processor": "stripe" if is_stripe else "manual",
            "amount": item.amount,
            "status": "success" if item.status == "approved" else "failed" if item.status == "rejected" else "processing",
            "date": item.requested_at.strftime("%Y-%m-%d %H:%M") if item.requested_at else "",
        })

    balance = total_topup - total_wallet_spend

    return {
        "fleetId": f"fleet_{str(current_user.id)[-6:]}",
        "balance": round(max(0.0, balance), 2),
        "autoRecharge": {
            "enabled": False,
            "threshold": 30000,
            "rechargeAmount": 100000,
            "paymentMethodId": "",
        },
        "transactions": txn_history,
    }


# ── GET /logistic/fuel-history — Per-vehicle fuel logs ───────────────────────
@router.get("/fuel-history")
async def get_fuel_history(
    vehicle_id: Optional[str] = None,
    current_user: User = Depends(require_role(["logistic"]))
):
    """Logistic partner ke vehicles ka fuel history — Udhaar table se"""
    from src.db.models.udhaar import Udhaar

    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()
    vehicle_ids = [v.id for v in vehicles]
    vehicle_map = {v.id: v for v in vehicles}

    if not vehicle_ids:
        return []

    query_filter = [In(Udhaar.vehicle_id, vehicle_ids), Udhaar.status == "approved"]
    if vehicle_id:
        try:
            vid_oid = PydanticObjectId(vehicle_id)
            query_filter.append(Udhaar.vehicle_id == vid_oid)
        except Exception:
            pass

    udhaars = await Udhaar.find(*query_filter).sort(-Udhaar.used_at).limit(200).to_list()

    response = []
    for udhaar in udhaars:
        veh = vehicle_map.get(udhaar.vehicle_id)
        pump = await Pump.get(udhaar.pump_id) if udhaar.pump_id else None
        response.append({
            "id": str(udhaar.id),
            "vehicle_plate": veh.vehicle_plate if veh else "N/A",
            "vehicle_type": veh.vehicle_type if veh else "truck",
            "make_model": veh.make_model if veh else "N/A",
            "driver_name": veh.driver_name if veh else "N/A",
            "fuel_type": udhaar.fuel_type or "Diesel",
            "pump_name": pump.name if pump else "Unknown",
            "pump_address": pump.address if pump else "",
            "pump_city": pump.city if pump else "",
            "volume_litres": round(udhaar.volume or 0, 2),
            "amount": round(udhaar.amount, 2),
            "payment_mode": "credit",
            "timestamp": udhaar.used_at.strftime("%Y-%m-%d %H:%M") if udhaar.used_at else "",
        })

    return response


# ── GET /logistic/vouchers — List all vouchers for this partner ───────────────
@router.get("/vouchers")
async def get_vouchers(
    current_user: User = Depends(require_role(["logistic"]))
):
    """Logistic partner ke sabhi digital fuel vouchers."""
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()
    vehicle_map = {str(v.id): v for v in vehicles}

    # PaymentRequest table se vouchers fetch karo (payment_type = 'fuel_voucher')
    voucher_requests = await PaymentRequest.find(
        PaymentRequest.logistic_partner_id == current_user.id,
        PaymentRequest.payment_type == "fuel_voucher"
    ).sort(-PaymentRequest.requested_at).to_list()

    result = []
    for vr in voucher_requests:
        form_data = vr.logistic_form_data or {}
        vehicle_id_str = form_data.get("vehicle_id", "")
        vehicle_plate = form_data.get("vehicle_plate", "N/A")
        veh = vehicle_map.get(vehicle_id_str)
        pump = await Pump.get(vr.pump_id) if vr.pump_id else None

        # Status mapping
        if vr.status == "approved":
            voucher_status = "approved"
        elif vr.status == "rejected":
            voucher_status = "rejected"
        elif vr.status == "pending":
            voucher_status = "pending"
        else:
            voucher_status = "pending"

        result.append({
            "id": f"VCH-{str(vr.id)[-6:]}",
            "vehicleId": vehicle_id_str,
            "vehicleNumber": veh.vehicle_plate if veh else vehicle_plate,
            "amount": vr.amount,
            "fuelType": form_data.get("fuel_type", "Diesel").lower(),
            "status": voucher_status,
            "expiryDate": form_data.get("expiry_date", ""),
            "createdDate": vr.requested_at.strftime("%Y-%m-%d") if vr.requested_at else "",
            "qrCode": vr.transaction_reference or f"FF_QR_{str(vr.id)[-8:]}",
            "notes": form_data.get("notes", ""),
            "pumpId": str(vr.pump_id) if vr.pump_id else None,
            "pumpName": pump.name if pump else "N/A",
        })

    return result


# ── POST /logistic/vouchers — Create a new fuel voucher ──────────────────────
@router.post("/vouchers")
async def create_voucher(
    voucher_data: VoucherCreate,
    current_user: User = Depends(require_role(["logistic"]))
):
    """New digital fuel voucher create karo."""
    # Vehicle validate karo
    try:
        vid = PydanticObjectId(voucher_data.vehicle_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid vehicle_id format")

    vehicle = await Vehicle.find_one(
        Vehicle.id == vid,
        Vehicle.partner_id == current_user.id
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found or not owned by you")

    import uuid
    qr_token = f"FF_QR_{str(uuid.uuid4())[:8].upper()}_{int(voucher_data.amount)}"

    pump_oid = None
    if voucher_data.pump_id:
        try:
            pump_oid = PydanticObjectId(voucher_data.pump_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pump_id format")

    payment_req = PaymentRequest(
        logistic_partner_id=current_user.id,
        pump_id=pump_oid,
        amount=voucher_data.amount,
        payment_type="fuel_voucher",
        transaction_reference=qr_token,
        status="pending",
        remarks=f"Fuel voucher for {vehicle.vehicle_plate} — {voucher_data.fuel_type}",
        logistic_form_data={
            "vehicle_id": voucher_data.vehicle_id,
            "vehicle_plate": vehicle.vehicle_plate,
            "fuel_type": voucher_data.fuel_type,
            "expiry_date": voucher_data.expiry_date,
            "notes": voucher_data.notes or "",
        },
        requested_at=datetime.utcnow(),
    )
    await payment_req.insert()

    pump = await Pump.get(pump_oid) if pump_oid else None

    return {
        "id": f"VCH-{str(payment_req.id)[-6:]}",
        "vehicleId": voucher_data.vehicle_id,
        "vehicleNumber": vehicle.vehicle_plate,
        "amount": voucher_data.amount,
        "fuelType": voucher_data.fuel_type.lower(),
        "status": "pending",
        "expiryDate": voucher_data.expiry_date,
        "createdDate": datetime.utcnow().strftime("%Y-%m-%d"),
        "qrCode": qr_token,
        "notes": voucher_data.notes or "",
        "pumpId": str(pump_oid) if pump_oid else None,
        "pumpName": pump.name if pump else "N/A",
    }


# ── GET /logistic/payments — All payment requests by this partner ─────────────
@router.get("/payments")
async def get_logistic_payments(
    current_user: User = Depends(require_role(["logistic"]))
):
    """Logistic partner ke sabhi payment requests."""
    payment_requests = await PaymentRequest.find(
        PaymentRequest.logistic_partner_id == current_user.id
    ).sort(-PaymentRequest.requested_at).to_list()

    result = []
    for pr in payment_requests:
        pump = await Pump.get(pr.pump_id) if pr.pump_id else None
        result.append({
            "id": str(pr.id),
            "pump_id": str(pr.pump_id) if pr.pump_id else None,
            "pump_name": pump.name if pump else "N/A",
            "amount": pr.amount,
            "payment_type": pr.payment_type,
            "transaction_reference": pr.transaction_reference,
            "status": pr.status,
            "remarks": pr.remarks,
            "logistic_signed": pr.logistic_signed if hasattr(pr, 'logistic_signed') else False,
            "pump_owner_signed": pr.pump_owner_signed if hasattr(pr, 'pump_owner_signed') else False,
            "contract_terms": pr.contract_terms if hasattr(pr, 'contract_terms') else None,
            "requested_at": pr.requested_at.strftime("%Y-%m-%d %H:%M") if pr.requested_at else "",
            "reviewed_at": pr.reviewed_at.strftime("%Y-%m-%d %H:%M") if hasattr(pr, 'reviewed_at') and pr.reviewed_at else None,
        })

    return result