"""
FILE: src/api/v1/udhaar.py
Fully migrated to async Beanie (MongoDB ODM).

ENDPOINTS (Pump Owner):
  POST   /udhaar/customers                    — Create credit customer
  GET    /udhaar/customers                    — List customers
  GET    /udhaar/customers/{id}               — Single customer view
  PUT    /udhaar/customers/{id}               — Update customer
  DELETE /udhaar/customers/{id}               — Soft delete customer
  POST   /udhaar/customers/{id}/kyc           — Upload KYC doc
  PATCH  /udhaar/customers/{id}/kyc/{doc_id}/verify — Verify KYC doc
  GET    /udhaar/customers/{id}/vehicles      — List vehicles for customer
  POST   /udhaar/vehicles                     — Register vehicle
  PUT    /udhaar/vehicles/{id}                — Update vehicle
  DELETE /udhaar/vehicles/{id}                — Soft delete vehicle
  GET    /udhaar/vehicles                     — All vehicles (pump-wide directory)
  POST   /udhaar/contracts                    — Issue contract
  GET    /udhaar/contracts                    — List contracts
  GET    /udhaar/contracts/{id}               — Get contract details
  GET    /udhaar/contracts/{id}/usage         — Get credit usage
  POST   /udhaar/contracts/{id}/amend         — Amend contract
  POST   /udhaar/transactions                 — Log credit transaction
  GET    /udhaar/transactions                 — List transactions
  GET    /udhaar/transactions/{id}            — Get transaction detail
  GET    /udhaar/invoices                     — List invoices
  GET    /udhaar/invoices/{id}                — Get invoice

ENDPOINTS (Legacy / Logistic):
  POST   /udhaar/add                          — Add udhaar (legacy)
  GET    /udhaar/history                      — Udhaar history
  DELETE /udhaar/{id}                         — Delete udhaar
  POST   /udhaar/settle/{customer_id}         — Settle outstanding
  GET    /udhaar/outstanding/{customer_id}    — Get outstanding
  GET    /udhaar/my-usage                     — Logistic partner usage
  GET    /udhaar/my-alerts                    — Logistic partner alerts
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from beanie import PydanticObjectId
from beanie.operators import In
from datetime import datetime
import structlog

from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.vehicle import Vehicle

# ── Import all udhaar models ──────────────────────────────────────────────────
from src.db.models.udhaar import (
    Udhaar,
    UdhaarCustomer,
    UdhaarKYCDocument,
    UdhaarVehicle,
    UdhaarContract,
    UdhaarSlipBooklet,
    UdhaarItemLimit,
    UdhaarCustomCondition,
    UdhaarInvoice,
    UdhaarTransaction,
    ContractStatus,
    KYCStatus,
)

# ── Import all udhaar schemas ─────────────────────────────────────────────────
from src.db.schemas.udhaar import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerListItem,
    KYCDocumentCreate,
    KYCVerifyRequest,
    KYCDocumentResponse,
    VehicleCreate,
    VehicleUpdate,
    VehicleResponse,
    ContractCreate,
    ContractResponse,
    ContractUsageResponse,
    UdhaarCreate,
    UdhaarResponse,
    UdhaarTransactionCreate,
    UdhaarTransactionResponse,
    InvoiceResponse,
    SingleCustomerView,
)

log = structlog.get_logger()
router = APIRouter(prefix="/udhaar", tags=["udhaar"])

ALERT_THRESHOLD = 0.90  # Alert when 90% credit used


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_pump(pump_id: str, user: User) -> Pump:
    """Verify pump ownership and return pump."""
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")
    pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == user.id)
    if not pump:
        raise HTTPException(status_code=403, detail="Pump not found or not authorized")
    return pump


async def _get_customer(customer_id: str, pump_id: PydanticObjectId) -> UdhaarCustomer:
    """Get active customer belonging to pump."""
    try:
        cid = PydanticObjectId(customer_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")
    customer = await UdhaarCustomer.find_one(
        UdhaarCustomer.id == cid,
        UdhaarCustomer.pump_id == pump_id,
        UdhaarCustomer.deleted_at == None,
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


async def _get_active_contract(customer_id: PydanticObjectId) -> Optional[UdhaarContract]:
    """Get the latest active contract for a customer."""
    contracts = await UdhaarContract.find(
        UdhaarContract.customer_id == customer_id,
        UdhaarContract.status == ContractStatus.active,
    ).sort(-UdhaarContract.version).to_list()
    return contracts[0] if contracts else None


def _compute_usage(contract: UdhaarContract) -> dict:
    """Build usage/progress bar data from contract."""
    remaining = max(0.0, contract.total_credit_limit - contract.current_spend)
    pct = 0.0
    if contract.total_credit_limit > 0:
        pct = round((contract.current_spend / contract.total_credit_limit) * 100, 2)
    return {
        "contract_id": str(contract.id),
        "total_credit_limit": contract.total_credit_limit,
        "current_spend": contract.current_spend,
        "remaining_credit": remaining,
        "usage_percent": pct,
        "max_spending_slips": contract.max_spending_slips,
        "current_slips_used": contract.current_slips_used,
        "status": contract.status,
        "alert": pct >= (ALERT_THRESHOLD * 100),
    }


def _build_contract_response(contract: UdhaarContract) -> dict:
    """Build full contract response with all fields."""
    data = {
        "id": str(contract.id),
        "customer_id": str(contract.customer_id),
        "pump_id": str(contract.pump_id),
        "version": contract.version,
        "station_name": contract.station_name,
        "org_name": contract.org_name,
        "address": contract.address,
        "gst_number": contract.gst_number,
        "valid_from": contract.valid_from,
        "valid_to": contract.valid_to,
        "security_deposit": contract.security_deposit,
        "total_credit_limit": contract.total_credit_limit,
        "current_spend": contract.current_spend,
        "max_spending_slips": contract.max_spending_slips,
        "current_slips_used": contract.current_slips_used,
        "money_limit_per_fill": contract.money_limit_per_fill,
        "money_limit_per_day": contract.money_limit_per_day,
        "money_limit_per_cycle": contract.money_limit_per_cycle,
        "billing_frequency": contract.billing_frequency,
        "bill_by": contract.bill_by,
        "billing_cycle": contract.billing_cycle,
        "billing_start_date": contract.billing_start_date,
        "round_off": contract.round_off,
        "require_meter_photo": contract.require_meter_photo,
        "require_vehicle_photo": contract.require_vehicle_photo,
        "require_fueling_video": contract.require_fueling_video,
        "require_driver_verification": contract.require_driver_verification,
        "sop_recipients": contract.sop_recipients,
        "late_payment_interest": contract.late_payment_interest,
        "deposit_utilization_days": contract.deposit_utilization_days,
        "suspension_period_days": contract.suspension_period_days,
        "invoice_dispute_days": contract.invoice_dispute_days,
        "custom_terms": contract.custom_terms,
        "status": contract.status,
        "created_at": contract.created_at,
        "updated_at": contract.updated_at,
    }
    if contract.total_credit_limit > 0:
        data["credit_usage_percent"] = round(
            (contract.current_spend / contract.total_credit_limit) * 100, 2
        )
    return data


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY UDHAAR ENDPOINTS (simple add/history)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/add")
async def add_udhaar_by_pump_owner(
    udhaar_data: UdhaarCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Legacy: Add a simple udhaar entry."""
    try:
        # Resolve pump_id
        try:
            pump_oid = PydanticObjectId(str(udhaar_data.pump_id))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pump_id")

        # Try to match vehicle_plate to a logistic Vehicle
        vehicle_oid = None
        if udhaar_data.vehicle_id:
            try:
                vehicle_oid = PydanticObjectId(str(udhaar_data.vehicle_id))
            except Exception:
                pass

        udhaar = Udhaar(
            pump_id=pump_oid,
            customer_id=PydanticObjectId(str(udhaar_data.customer_id)) if udhaar_data.customer_id else None,
            vehicle_id=vehicle_oid,
            amount=udhaar_data.amount,
            volume=udhaar_data.volume,
            fuel_type=udhaar_data.fuel_type,
            remarks=udhaar_data.remarks,
            status="approved",
            used_at=datetime.utcnow(),
        )
        await udhaar.insert()
        return {"message": "Udhaar added successfully", "id": str(udhaar.id)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add udhaar: {str(e)}")


@router.get("/history")
async def get_udhaar_history(
    pump_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
):
    """Udhaar history — customer_id se filter optional."""
    try:
        if customer_id:
            cid = PydanticObjectId(customer_id)
            udhaars = await Udhaar.find(Udhaar.customer_id == cid).sort(-Udhaar.used_at).to_list()
        elif pump_id:
            pid = PydanticObjectId(pump_id)
            udhaars = await Udhaar.find(Udhaar.pump_id == pid).sort(-Udhaar.used_at).to_list()
        else:
            udhaars = await Udhaar.find(Udhaar.pump_id == current_user.id).sort(-Udhaar.used_at).to_list()
    except Exception:
        udhaars = []

    return [
        {
            "id": str(u.id),
            "pump_id": str(u.pump_id),
            "customer_id": str(u.customer_id) if u.customer_id else None,
            "vehicle_id": str(u.vehicle_id) if u.vehicle_id else None,
            "amount": u.amount,
            "volume": u.volume,
            "fuel_type": u.fuel_type,
            "status": u.status,
            "remarks": u.remarks,
            "used_at": u.used_at,
        }
        for u in udhaars
    ]


@router.delete("/{udhaar_id}")
async def delete_udhaar(
    udhaar_id: str,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Pump Owner deletes a udhaar entry."""
    try:
        oid = PydanticObjectId(udhaar_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid udhaar_id")

    udhaar = await Udhaar.get(oid)
    if not udhaar:
        raise HTTPException(status_code=404, detail="Udhaar entry not found")

    await udhaar.delete()
    log.info("Udhaar deleted by Pump Owner", udhaar_id=udhaar_id)
    return {"message": "Udhaar entry deleted successfully"}


@router.post("/settle/{customer_id}")
async def settle_outstanding(
    customer_id: str,
    amount: float,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Settle customer outstanding (payment received)."""
    # For new UdhaarCustomer model — reduce current_spend on active contract
    try:
        cid = PydanticObjectId(customer_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    contract = await _get_active_contract(cid)
    if not contract:
        raise HTTPException(status_code=404, detail="No active contract found for customer")

    contract.current_spend = max(0.0, contract.current_spend - amount)
    contract.updated_at = datetime.utcnow()
    await contract.save()

    return {
        "message": "Outstanding settled",
        "customer_id": customer_id,
        "remaining_outstanding": contract.current_spend,
    }


@router.get("/outstanding/{customer_id}")
async def get_outstanding(
    customer_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Customer outstanding detail."""
    try:
        cid = PydanticObjectId(customer_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    customer = await UdhaarCustomer.get(cid)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    contract = await _get_active_contract(cid)
    credit_limit = contract.total_credit_limit if contract else 0.0
    current_spend = contract.current_spend if contract else 0.0

    return {
        "customer_id": str(customer.id),
        "name": customer.name,
        "credit_limit": credit_limit,
        "outstanding_amount": current_spend,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LOGISTIC PARTNER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/my-usage")
async def get_my_credit_usage(
    current_user: User = Depends(require_role(["logistic"]))
):
    """Logistic partner — credit usage across all their vehicles."""
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()
    vehicle_plates = [v.vehicle_plate for v in vehicles]
    vehicle_map = {v.vehicle_plate: v for v in vehicles}
    vehicle_id_map = {v.id: v for v in vehicles}

    if not vehicle_plates:
        return {"usage_summary": [], "recent_transactions": []}

    # Get recent udhaar entries for these vehicles
    vehicle_ids = [v.id for v in vehicles]
    udhaars = await Udhaar.find(
        In(Udhaar.vehicle_id, vehicle_ids),
        Udhaar.status == "approved"
    ).sort(-Udhaar.used_at).limit(50).to_list()

    # Build usage per vehicle
    vehicle_totals: dict = {}
    vehicle_pump_ids: dict = {}
    for u in udhaars:
        if u.vehicle_id:
            key = str(u.vehicle_id)
            vehicle_totals[key] = vehicle_totals.get(key, 0.0) + u.amount
            if u.pump_id and key not in vehicle_pump_ids:
                vehicle_pump_ids[key] = str(u.pump_id)

    pump_cache: dict = {}
    usage_summary = []
    for v in vehicles:
        total_spend = vehicle_totals.get(str(v.id), 0.0)
        pid_str = vehicle_pump_ids.get(str(v.id))
        pname = "Fuel Network Pump"
        if pid_str:
            if pid_str not in pump_cache:
                p_obj = await Pump.get(pid_str) if pid_str else None
                pump_cache[pid_str] = p_obj
            p_obj = pump_cache.get(pid_str)
            if p_obj:
                pname = p_obj.name

        usage_summary.append({
            "vehicle_id": str(v.id),
            "vehicle_plate": v.vehicle_plate,
            "pump_id": pid_str or "",
            "pump_name": pname,
            "credit_limit": v.credit_limit,
            "outstanding_amount": v.outstanding_amount,
            "available_credit": max(0.0, v.credit_limit - v.outstanding_amount),
        })

    # Recent transactions
    pump_cache: dict = {}
    recent_transactions = []
    for u in udhaars:
        veh = vehicle_id_map.get(u.vehicle_id)
        pump_id_str = str(u.pump_id) if u.pump_id else None
        if pump_id_str and pump_id_str not in pump_cache:
            pump = await Pump.get(u.pump_id) if u.pump_id else None
            pump_cache[pump_id_str] = pump
        pump = pump_cache.get(pump_id_str)
        recent_transactions.append({
            "id": str(u.id),
            "vehicle_id": str(u.vehicle_id) if u.vehicle_id else None,
            "vehicle_plate": veh.vehicle_plate if veh else None,
            "pump_id": str(u.pump_id),
            "pump_name": pump.name if pump else f"Pump #{pump_id_str}",
            "amount": u.amount,
            "volume": u.volume,
            "fuel_type": u.fuel_type,
            "remarks": u.remarks,
            "used_at": u.used_at.strftime("%d %b %Y, %I:%M %p") if u.used_at else None,
        })

    return {
        "usage_summary": usage_summary,
        "recent_transactions": recent_transactions,
    }


@router.get("/my-alerts")
async def get_my_overspend_alerts(
    current_user: User = Depends(require_role(["logistic"]))
):
    """Logistic partner vehicles that have exceeded credit limits."""
    vehicles = await Vehicle.find(Vehicle.partner_id == current_user.id).to_list()

    alerts = []
    for v in vehicles:
        if v.credit_limit > 0 and v.outstanding_amount > v.credit_limit:
            alerts.append({
                "vehicle_id": str(v.id),
                "vehicle_plate": v.vehicle_plate,
                "credit_limit": v.credit_limit,
                "amount_used": v.outstanding_amount,
                "overspend_amount": round(v.outstanding_amount - v.credit_limit, 2),
            })

    return alerts


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/customers")
async def list_customers(
    pump_id: str = Query(..., description="Pump ID"),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Directory: List all credit customers for a pump."""
    pump = await _get_pump(pump_id, current_user)

    customers = await UdhaarCustomer.find(
        UdhaarCustomer.pump_id == pump.id,
        UdhaarCustomer.deleted_at == None,
    ).sort(-UdhaarCustomer.created_at).to_list()

    result = []
    for c in customers:
        vehicle_count = await UdhaarVehicle.find(
            UdhaarVehicle.customer_id == c.id,
            UdhaarVehicle.deleted_at == None,
        ).count()

        active_contract = await _get_active_contract(c.id)

        result.append({
            "id": str(c.id),
            "name": c.name,
            "customer_type": c.customer_type,
            "contact_phone": c.contact_phone,
            "kyc_status": c.kyc_status,
            "vehicle_count": vehicle_count,
            "has_active_contract": active_contract is not None,
            "created_at": c.created_at,
        })
    return result


@router.post("/customers")
async def create_customer(
    pump_id: str = Query(...),
    data: CustomerCreate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Add a new credit customer to directory."""
    pump = await _get_pump(pump_id, current_user)

    customer = UdhaarCustomer(
        pump_id=pump.id,
        customer_type=data.customer_type,
        name=data.name,
        contact_name=data.contact_name,
        contact_phone=data.contact_phone,
        contact_email=data.contact_email,
    )
    await customer.insert()
    return {"message": "Customer added successfully", "customer_id": str(customer.id)}


@router.get("/customers/{customer_id}")
async def get_customer(
    pump_id: str = Query(...),
    customer_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Single Customer View — full dashboard data."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(customer_id, pump.id)

    # KYC docs
    kyc_docs = await UdhaarKYCDocument.find(
        UdhaarKYCDocument.customer_id == customer.id
    ).to_list()

    # Vehicles
    vehicles = await UdhaarVehicle.find(
        UdhaarVehicle.customer_id == customer.id,
        UdhaarVehicle.deleted_at == None,
    ).to_list()

    # Active contract + usage
    contract = await _get_active_contract(customer.id)
    contract_data = _build_contract_response(contract) if contract else None
    usage_data = _compute_usage(contract) if contract else None

    # Recent transactions (last 20)
    txns = await UdhaarTransaction.find(
        UdhaarTransaction.customer_id == customer.id,
    ).sort(-UdhaarTransaction.created_at).limit(20).to_list()

    # Recent invoices (last 10)
    invoices = await UdhaarInvoice.find(
        UdhaarInvoice.customer_id == customer.id,
    ).sort(-UdhaarInvoice.generated_at).limit(10).to_list()

    def serialize_kyc(d: UdhaarKYCDocument):
        return {
            "id": str(d.id),
            "customer_id": str(d.customer_id),
            "document_type": d.document_type,
            "image_url": d.image_url,
            "status": d.status,
            "rejection_reason": d.rejection_reason,
            "reviewed_at": d.reviewed_at,
            "created_at": d.created_at,
        }

    def serialize_vehicle(v: UdhaarVehicle):
        return {
            "id": str(v.id),
            "customer_id": str(v.customer_id),
            "pump_id": str(v.pump_id),
            "number_plate": v.number_plate,
            "registration_type": v.registration_type,
            "make": v.make,
            "model": v.model,
            "variant": v.variant,
            "fuel_types": v.fuel_types,
            "emission_standard": v.emission_standard,
            "engine_number": v.engine_number,
            "chassis_number": v.chassis_number,
            "registration_date": v.registration_date,
            "is_active": v.is_active,
            "created_at": v.created_at,
        }

    def serialize_txn(t: UdhaarTransaction):
        return {
            "id": str(t.id),
            "contract_id": str(t.contract_id),
            "customer_id": str(t.customer_id),
            "vehicle_id": str(t.vehicle_id) if t.vehicle_id else None,
            "pump_id": str(t.pump_id),
            "item_name": t.item_name,
            "quantity": t.quantity,
            "amount": t.amount,
            "slip_number": t.slip_number,
            "driver_verified": t.driver_verified,
            "created_at": t.created_at,
        }

    def serialize_invoice(i: UdhaarInvoice):
        return {
            "id": str(i.id),
            "contract_id": str(i.contract_id),
            "customer_id": str(i.customer_id),
            "pump_id": str(i.pump_id),
            "cycle_start": i.cycle_start,
            "cycle_end": i.cycle_end,
            "total_amount": i.total_amount,
            "rounded_amount": i.rounded_amount,
            "status": i.status,
            "generated_at": i.generated_at,
            "paid_at": i.paid_at,
        }

    return {
        "customer": {
            "id": str(customer.id),
            "pump_id": str(customer.pump_id),
            "name": customer.name,
            "customer_type": customer.customer_type,
            "contact_name": customer.contact_name,
            "contact_phone": customer.contact_phone,
            "contact_email": customer.contact_email,
            "kyc_status": customer.kyc_status,
            "is_active": customer.is_active,
            "created_at": customer.created_at,
        },
        "kyc_documents": [serialize_kyc(d) for d in kyc_docs],
        "vehicles": [serialize_vehicle(v) for v in vehicles],
        "active_contract": contract_data,
        "contract_usage": usage_data,
        "recent_transactions": [serialize_txn(t) for t in txns],
        "recent_invoices": [serialize_invoice(i) for i in invoices],
    }


@router.put("/customers/{customer_id}")
async def update_customer(
    pump_id: str = Query(...),
    customer_id: str = Path(...),
    data: CustomerUpdate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Edit customer identity / contact details."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(customer_id, pump.id)

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(customer, field, value)

    customer.updated_at = datetime.utcnow()
    await customer.save()
    return {"message": "Customer updated successfully"}


@router.delete("/customers/{customer_id}")
async def delete_customer(
    pump_id: str = Query(...),
    customer_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Soft delete customer — historical data preserved."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(customer_id, pump.id)

    customer.deleted_at = datetime.utcnow()
    customer.is_active = False
    await customer.save()
    return {"message": "Customer deleted"}


# ═══════════════════════════════════════════════════════════════════════════════
# KYC ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/customers/{customer_id}/kyc")
async def upload_kyc_document(
    pump_id: str = Query(...),
    customer_id: str = Path(...),
    data: KYCDocumentCreate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Upload a KYC document for a customer."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(customer_id, pump.id)

    doc = UdhaarKYCDocument(
        customer_id=customer.id,
        document_type=data.document_type,
        image_url=data.image_url,
        status=KYCStatus.pending,
    )
    await doc.insert()
    return {"message": "KYC document uploaded", "doc_id": str(doc.id), "status": "pending"}


@router.patch("/customers/{customer_id}/kyc/{doc_id}/verify")
async def verify_kyc_document(
    pump_id: str = Query(...),
    customer_id: str = Path(...),
    doc_id: str = Path(...),
    data: KYCVerifyRequest = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Accept or reject a KYC document. Also updates customer kyc_status."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(customer_id, pump.id)

    try:
        did = PydanticObjectId(doc_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid doc_id")

    doc = await UdhaarKYCDocument.find_one(
        UdhaarKYCDocument.id == did,
        UdhaarKYCDocument.customer_id == customer.id,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="KYC document not found")

    doc.status = data.status
    doc.rejection_reason = data.rejection_reason
    doc.reviewed_by = current_user.id
    doc.reviewed_at = datetime.utcnow()
    await doc.save()

    # Update overall customer KYC status
    customer.kyc_status = data.status
    await customer.save()

    return {"message": f"KYC document {data.status.value}", "doc_id": doc_id}


# ═══════════════════════════════════════════════════════════════════════════════
# VEHICLE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/customers/{customer_id}/vehicles")
async def list_customer_vehicles(
    pump_id: str = Query(...),
    customer_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """All vehicles linked to a specific customer."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(customer_id, pump.id)

    vehicles = await UdhaarVehicle.find(
        UdhaarVehicle.customer_id == customer.id,
        UdhaarVehicle.deleted_at == None,
    ).to_list()

    return [
        {
            "id": str(v.id),
            "number_plate": v.number_plate,
            "registration_type": v.registration_type,
            "make": v.make,
            "model": v.model,
            "variant": v.variant,
            "fuel_types": v.fuel_types,
            "is_active": v.is_active,
            "created_at": v.created_at,
        }
        for v in vehicles
    ]


@router.post("/vehicles")
async def create_vehicle(
    pump_id: str = Query(...),
    data: VehicleCreate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Register a new vehicle and link it to a customer."""
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(str(data.customer_id), pump.id)

    # Check duplicate plate within this pump
    plate_normalized = data.number_plate.upper().strip()
    existing = await UdhaarVehicle.find_one(
        UdhaarVehicle.number_plate == plate_normalized,
        UdhaarVehicle.pump_id == pump.id,
        UdhaarVehicle.deleted_at == None,
    )
    if existing:
        raise HTTPException(status_code=409, detail="Vehicle with this plate already exists")

    vehicle = UdhaarVehicle(
        customer_id=customer.id,
        pump_id=pump.id,
        number_plate=plate_normalized,
        registration_type=data.registration_type,
        make=data.make,
        model=data.model,
        variant=data.variant,
        fuel_types=data.fuel_types or [],
        emission_standard=data.emission_standard,
        engine_number=data.engine_number,
        chassis_number=data.chassis_number,
        registration_date=data.registration_date,
    )
    await vehicle.insert()
    return {"message": "Vehicle added successfully", "vehicle_id": str(vehicle.id)}


@router.put("/vehicles/{vehicle_id}")
async def update_vehicle(
    pump_id: str = Query(...),
    vehicle_id: str = Path(...),
    data: VehicleUpdate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Update vehicle specs."""
    pump = await _get_pump(pump_id, current_user)

    try:
        vid = PydanticObjectId(vehicle_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid vehicle_id")

    vehicle = await UdhaarVehicle.find_one(
        UdhaarVehicle.id == vid,
        UdhaarVehicle.pump_id == pump.id,
        UdhaarVehicle.deleted_at == None,
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(vehicle, field, value)

    vehicle.updated_at = datetime.utcnow()
    await vehicle.save()
    return {"message": "Vehicle updated"}


@router.delete("/vehicles/{vehicle_id}")
async def delete_vehicle(
    pump_id: str = Query(...),
    vehicle_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Soft delete vehicle."""
    pump = await _get_pump(pump_id, current_user)

    try:
        vid = PydanticObjectId(vehicle_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid vehicle_id")

    vehicle = await UdhaarVehicle.find_one(
        UdhaarVehicle.id == vid,
        UdhaarVehicle.pump_id == pump.id,
        UdhaarVehicle.deleted_at == None,
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    vehicle.deleted_at = datetime.utcnow()
    vehicle.is_active = False
    await vehicle.save()
    return {"message": "Vehicle deleted"}


@router.get("/vehicles")
async def list_all_vehicles(
    pump_id: str = Query(...),
    customer_id: Optional[str] = Query(None, description="Filter by customer"),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Vehicle directory — all pump vehicles, optionally filtered by customer."""
    pump = await _get_pump(pump_id, current_user)

    filters = [
        UdhaarVehicle.pump_id == pump.id,
        UdhaarVehicle.deleted_at == None,
    ]
    if customer_id:
        try:
            cid = PydanticObjectId(customer_id)
            filters.append(UdhaarVehicle.customer_id == cid)
        except Exception:
            pass

    vehicles = await UdhaarVehicle.find(*filters).sort(-UdhaarVehicle.created_at).to_list()

    return [
        {
            "id": str(v.id),
            "customer_id": str(v.customer_id),
            "number_plate": v.number_plate,
            "registration_type": v.registration_type,
            "make": v.make,
            "model": v.model,
            "variant": v.variant,
            "fuel_types": v.fuel_types,
            "is_active": v.is_active,
            "created_at": v.created_at,
        }
        for v in vehicles
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/contracts")
async def issue_contract(
    pump_id: str = Query(...),
    data: ContractCreate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """
    Issue a new contract for a customer.
    - If customer already has an active contract, raise error (use amend instead).
    - Slip totals are auto-calculated.
    """
    pump = await _get_pump(pump_id, current_user)
    customer = await _get_customer(str(data.customer_id), pump.id)

    # Block double-issuing
    existing = await _get_active_contract(customer.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Customer already has an active contract. Use amend instead.",
        )

    # Create contract
    contract = UdhaarContract(
        customer_id=customer.id,
        pump_id=pump.id,
        version=1,
        station_name=data.station_name,
        org_name=data.org_name,
        address=data.address,
        gst_number=data.gst_number,
        valid_from=data.valid_from or datetime.utcnow(),
        valid_to=data.valid_to,
        security_deposit=data.security_deposit,
        total_credit_limit=data.total_credit_limit,
        max_spending_slips=data.max_spending_slips,
        money_limit_per_fill=data.money_limit_per_fill,
        money_limit_per_day=data.money_limit_per_day,
        money_limit_per_cycle=data.money_limit_per_cycle,
        billing_frequency=data.billing_frequency,
        bill_by=data.bill_by,
        billing_cycle=data.billing_cycle,
        billing_start_date=data.billing_start_date,
        round_off=data.round_off,
        require_meter_photo=data.require_meter_photo,
        require_vehicle_photo=data.require_vehicle_photo,
        require_fueling_video=data.require_fueling_video,
        require_driver_verification=data.require_driver_verification,
        sop_recipients=[r.model_dump() for r in (data.sop_recipients or [])],
        late_payment_interest=data.late_payment_interest,
        deposit_utilization_days=data.deposit_utilization_days,
        suspension_period_days=data.suspension_period_days,
        invoice_dispute_days=data.invoice_dispute_days,
        custom_terms=data.custom_terms,
        status=ContractStatus.active,
        created_by=current_user.id,
    )
    await contract.insert()

    # Slip booklets — auto-calculate totals
    for b in (data.slip_booklets or []):
        total = b.end_number - b.start_number + 1
        booklet = UdhaarSlipBooklet(
            contract_id=contract.id,
            booklet_number=b.booklet_number,
            start_number=b.start_number,
            end_number=b.end_number,
            total_slips=total,
        )
        await booklet.insert()

    # Item-specific limits
    for item in (data.item_limits or []):
        il = UdhaarItemLimit(
            contract_id=contract.id,
            item_name=item.item_name,
            qty_per_fill=item.qty_per_fill,
            qty_per_day=item.qty_per_day,
            qty_per_cycle=item.qty_per_cycle,
        )
        await il.insert()

    # Custom condition cards
    for cond in (data.custom_conditions or []):
        cc = UdhaarCustomCondition(
            contract_id=contract.id,
            vehicle_type=cond.vehicle_type,
            item_name=cond.item_name,
            station_id=PydanticObjectId(cond.station_id) if cond.station_id else None,
            max_slips=cond.max_slips,
            money_per_fill=cond.money_per_fill,
            money_per_day=cond.money_per_day,
            money_per_cycle=cond.money_per_cycle,
            qty_per_fill=cond.qty_per_fill,
            qty_per_day=cond.qty_per_day,
            qty_per_cycle=cond.qty_per_cycle,
        )
        await cc.insert()

    return {
        "message": "Customer contract added successfully",
        "contract_id": str(contract.id),
        "version": contract.version,
    }


@router.get("/contracts")
async def list_contracts(
    pump_id: str = Query(...),
    status: Optional[str] = Query(None, description="active/expired/suspended/amended"),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Master contracts page — all contracts for the pump."""
    pump = await _get_pump(pump_id, current_user)

    filters = [UdhaarContract.pump_id == pump.id]
    if status:
        filters.append(UdhaarContract.status == status)

    contracts = await UdhaarContract.find(*filters).sort(-UdhaarContract.created_at).to_list()

    result = []
    for c in contracts:
        customer = await UdhaarCustomer.get(c.customer_id)
        usage = _compute_usage(c)
        result.append({
            "contract_id": str(c.id),
            "version": c.version,
            "customer_id": str(c.customer_id),
            "customer_name": customer.name if customer else "Unknown",
            "valid_from": c.valid_from,
            "valid_to": c.valid_to,
            "total_credit_limit": c.total_credit_limit,
            "current_spend": c.current_spend,
            "usage_percent": usage["usage_percent"],
            "status": c.status,
            "alert": usage["alert"],
        })
    return result


@router.get("/contracts/{contract_id}")
async def get_contract(
    pump_id: str = Query(...),
    contract_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Full contract details with all limits, conditions, slips."""
    pump = await _get_pump(pump_id, current_user)

    try:
        cid = PydanticObjectId(contract_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid contract_id")

    contract = await UdhaarContract.find_one(
        UdhaarContract.id == cid,
        UdhaarContract.pump_id == pump.id,
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    return _build_contract_response(contract)


@router.get("/contracts/{contract_id}/usage")
async def get_contract_usage(
    pump_id: str = Query(...),
    contract_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Progress bar data for a contract."""
    pump = await _get_pump(pump_id, current_user)

    try:
        cid = PydanticObjectId(contract_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid contract_id")

    contract = await UdhaarContract.find_one(
        UdhaarContract.id == cid,
        UdhaarContract.pump_id == pump.id,
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    return _compute_usage(contract)


@router.post("/contracts/{contract_id}/amend")
async def amend_contract(
    pump_id: str = Query(...),
    contract_id: str = Path(...),
    data: ContractCreate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Amend a contract — creates new version, marks old as 'amended'."""
    pump = await _get_pump(pump_id, current_user)

    try:
        cid = PydanticObjectId(contract_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid contract_id")

    old_contract = await UdhaarContract.find_one(
        UdhaarContract.id == cid,
        UdhaarContract.pump_id == pump.id,
    )
    if not old_contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    # Mark old as amended
    old_contract.status = ContractStatus.amended
    await old_contract.save()

    # Find max version for this customer
    all_versions = await UdhaarContract.find(
        UdhaarContract.customer_id == old_contract.customer_id
    ).to_list()
    max_version = max((c.version for c in all_versions), default=0)

    new_contract = UdhaarContract(
        customer_id=old_contract.customer_id,
        pump_id=pump.id,
        version=max_version + 1,
        amended_from=old_contract.id,
        station_name=data.station_name,
        org_name=data.org_name,
        address=data.address,
        gst_number=data.gst_number,
        valid_from=data.valid_from or datetime.utcnow(),
        valid_to=data.valid_to,
        security_deposit=data.security_deposit,
        total_credit_limit=data.total_credit_limit,
        max_spending_slips=data.max_spending_slips,
        money_limit_per_fill=data.money_limit_per_fill,
        money_limit_per_day=data.money_limit_per_day,
        money_limit_per_cycle=data.money_limit_per_cycle,
        billing_frequency=data.billing_frequency,
        bill_by=data.bill_by,
        billing_cycle=data.billing_cycle,
        billing_start_date=data.billing_start_date,
        round_off=data.round_off,
        require_meter_photo=data.require_meter_photo,
        require_vehicle_photo=data.require_vehicle_photo,
        require_fueling_video=data.require_fueling_video,
        require_driver_verification=data.require_driver_verification,
        sop_recipients=[r.model_dump() for r in (data.sop_recipients or [])],
        late_payment_interest=data.late_payment_interest,
        deposit_utilization_days=data.deposit_utilization_days,
        suspension_period_days=data.suspension_period_days,
        invoice_dispute_days=data.invoice_dispute_days,
        custom_terms=data.custom_terms,
        status=ContractStatus.active,
        created_by=current_user.id,
    )
    await new_contract.insert()

    # Re-add slips, limits, conditions for new version
    for b in (data.slip_booklets or []):
        total = b.end_number - b.start_number + 1
        await UdhaarSlipBooklet(
            contract_id=new_contract.id,
            booklet_number=b.booklet_number,
            start_number=b.start_number,
            end_number=b.end_number,
            total_slips=total,
        ).insert()

    for item in (data.item_limits or []):
        await UdhaarItemLimit(
            contract_id=new_contract.id,
            item_name=item.item_name,
            qty_per_fill=item.qty_per_fill,
            qty_per_day=item.qty_per_day,
            qty_per_cycle=item.qty_per_cycle,
        ).insert()

    for cond in (data.custom_conditions or []):
        await UdhaarCustomCondition(
            contract_id=new_contract.id,
            vehicle_type=cond.vehicle_type,
            item_name=cond.item_name,
            station_id=PydanticObjectId(cond.station_id) if cond.station_id else None,
            max_slips=cond.max_slips,
            money_per_fill=cond.money_per_fill,
            money_per_day=cond.money_per_day,
            money_per_cycle=cond.money_per_cycle,
            qty_per_fill=cond.qty_per_fill,
            qty_per_day=cond.qty_per_day,
            qty_per_cycle=cond.qty_per_cycle,
        ).insert()

    return {
        "message": "Contract amended successfully",
        "new_contract_id": str(new_contract.id),
        "new_version": new_contract.version,
        "old_contract_id": str(old_contract.id),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSACTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/transactions")
async def log_transaction(
    pump_id: str = Query(...),
    data: UdhaarTransactionCreate = ...,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """
    Log a credit sale transaction.
    - Validates SOPs (if required by contract).
    - Updates contract.current_spend in real-time.
    - Blocks if global credit limit exceeded.
    """
    pump = await _get_pump(pump_id, current_user)

    try:
        ctid = PydanticObjectId(str(data.contract_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid contract_id")

    contract = await UdhaarContract.find_one(
        UdhaarContract.id == ctid,
        UdhaarContract.pump_id == pump.id,
        UdhaarContract.status == ContractStatus.active,
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Active contract not found")

    # ── SOP validation ────────────────────────────────────────────
    if contract.require_meter_photo and not data.meter_photo_url:
        raise HTTPException(status_code=422, detail="Meter reading photo is required for this contract")
    if contract.require_vehicle_photo and not data.vehicle_photo_url:
        raise HTTPException(status_code=422, detail="Vehicle photo is required for this contract")
    if contract.require_fueling_video and not data.fueling_video_url:
        raise HTTPException(status_code=422, detail="Fueling video is required for this contract")
    if contract.require_driver_verification and not data.driver_verified:
        raise HTTPException(status_code=422, detail="Driver verification is required for this contract")

    # ── Global credit limit check ────────────────────────────────
    if contract.total_credit_limit > 0:
        if (contract.current_spend + data.amount) > contract.total_credit_limit:
            raise HTTPException(
                status_code=400,
                detail=f"Credit limit exceeded. Remaining: ₹{contract.total_credit_limit - contract.current_spend:.2f}",
            )

    # ── Per-fill money limit check ────────────────────────────────
    if contract.money_limit_per_fill and data.amount > contract.money_limit_per_fill:
        raise HTTPException(
            status_code=400,
            detail=f"Per-fill limit exceeded. Max: ₹{contract.money_limit_per_fill}",
        )

    vehicle_oid = None
    if data.vehicle_id:
        try:
            vehicle_oid = PydanticObjectId(str(data.vehicle_id))
        except Exception:
            pass

    # Log transaction
    txn = UdhaarTransaction(
        contract_id=contract.id,
        customer_id=contract.customer_id,
        vehicle_id=vehicle_oid,
        pump_id=pump.id,
        item_name=data.item_name,
        quantity=data.quantity,
        amount=data.amount,
        slip_number=data.slip_number,
        meter_photo_url=data.meter_photo_url,
        vehicle_photo_url=data.vehicle_photo_url,
        fueling_video_url=data.fueling_video_url,
        driver_verified=data.driver_verified,
    )
    await txn.insert()

    # Update contract spend in real-time
    contract.current_spend += data.amount
    if data.slip_number:
        contract.current_slips_used += 1
    contract.updated_at = datetime.utcnow()
    await contract.save()

    usage = _compute_usage(contract)

    return {
        "message": "Transaction logged",
        "transaction_id": str(txn.id),
        "contract_usage": {
            "current_spend": contract.current_spend,
            "usage_percent": usage["usage_percent"],
            "alert": usage["alert"],
        },
    }


@router.get("/transactions")
async def list_transactions(
    pump_id: str = Query(...),
    customer_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(50),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Master transactions page — all credit sale transactions."""
    pump = await _get_pump(pump_id, current_user)

    filters = [UdhaarTransaction.pump_id == pump.id]

    if customer_id:
        try:
            filters.append(UdhaarTransaction.customer_id == PydanticObjectId(customer_id))
        except Exception:
            pass

    if contract_id:
        try:
            filters.append(UdhaarTransaction.contract_id == PydanticObjectId(contract_id))
        except Exception:
            pass

    all_txns = await UdhaarTransaction.find(*filters).sort(-UdhaarTransaction.created_at).to_list()
    total = len(all_txns)
    txns = all_txns[skip:skip + limit]

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "transactions": [
            {
                "id": str(t.id),
                "contract_id": str(t.contract_id),
                "customer_id": str(t.customer_id),
                "vehicle_id": str(t.vehicle_id) if t.vehicle_id else None,
                "pump_id": str(t.pump_id),
                "item_name": t.item_name,
                "quantity": t.quantity,
                "amount": t.amount,
                "slip_number": t.slip_number,
                "driver_verified": t.driver_verified,
                "created_at": t.created_at,
            }
            for t in txns
        ],
    }


@router.get("/transactions/{txn_id}")
async def get_transaction_detail(
    pump_id: str = Query(...),
    txn_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Full details of a single transaction including evidence URLs."""
    pump = await _get_pump(pump_id, current_user)

    try:
        tid = PydanticObjectId(txn_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid txn_id")

    txn = await UdhaarTransaction.find_one(
        UdhaarTransaction.id == tid,
        UdhaarTransaction.pump_id == pump.id,
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {
        "id": str(txn.id),
        "contract_id": str(txn.contract_id),
        "customer_id": str(txn.customer_id),
        "vehicle_id": str(txn.vehicle_id) if txn.vehicle_id else None,
        "pump_id": str(txn.pump_id),
        "item_name": txn.item_name,
        "quantity": txn.quantity,
        "amount": txn.amount,
        "slip_number": txn.slip_number,
        "meter_photo_url": txn.meter_photo_url,
        "vehicle_photo_url": txn.vehicle_photo_url,
        "fueling_video_url": txn.fueling_video_url,
        "driver_verified": txn.driver_verified,
        "invoice_id": str(txn.invoice_id) if txn.invoice_id else None,
        "created_at": txn.created_at,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/invoices")
async def list_invoices(
    pump_id: str = Query(...),
    customer_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(50),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Master invoice page — all generated invoices."""
    pump = await _get_pump(pump_id, current_user)

    filters = [UdhaarInvoice.pump_id == pump.id]

    if customer_id:
        try:
            filters.append(UdhaarInvoice.customer_id == PydanticObjectId(customer_id))
        except Exception:
            pass

    if status:
        filters.append(UdhaarInvoice.status == status)

    all_invoices = await UdhaarInvoice.find(*filters).sort(-UdhaarInvoice.generated_at).to_list()
    total = len(all_invoices)
    invoices = all_invoices[skip:skip + limit]

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "invoices": [
            {
                "id": str(i.id),
                "contract_id": str(i.contract_id),
                "customer_id": str(i.customer_id),
                "pump_id": str(i.pump_id),
                "cycle_start": i.cycle_start,
                "cycle_end": i.cycle_end,
                "total_amount": i.total_amount,
                "rounded_amount": i.rounded_amount,
                "status": i.status,
                "generated_at": i.generated_at,
                "paid_at": i.paid_at,
            }
            for i in invoices
        ],
    }


@router.get("/invoices/{invoice_id}")
async def get_invoice(
    pump_id: str = Query(...),
    invoice_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"])),
):
    """Invoice detail view."""
    pump = await _get_pump(pump_id, current_user)

    try:
        iid = PydanticObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice_id")

    invoice = await UdhaarInvoice.find_one(
        UdhaarInvoice.id == iid,
        UdhaarInvoice.pump_id == pump.id,
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Also return linked transactions for this invoice
    txns = await UdhaarTransaction.find(
        UdhaarTransaction.invoice_id == invoice.id
    ).to_list()

    return {
        "invoice": {
            "id": str(invoice.id),
            "contract_id": str(invoice.contract_id),
            "customer_id": str(invoice.customer_id),
            "pump_id": str(invoice.pump_id),
            "cycle_start": invoice.cycle_start,
            "cycle_end": invoice.cycle_end,
            "total_amount": invoice.total_amount,
            "rounded_amount": invoice.rounded_amount,
            "status": invoice.status,
            "late_interest_applied": invoice.late_interest_applied,
            "deposit_utilized": invoice.deposit_utilized,
            "generated_at": invoice.generated_at,
            "paid_at": invoice.paid_at,
            "disputed_at": invoice.disputed_at,
            "dispute_reason": invoice.dispute_reason,
        },
        "transactions": [
            {
                "id": str(t.id),
                "item_name": t.item_name,
                "quantity": t.quantity,
                "amount": t.amount,
                "slip_number": t.slip_number,
                "created_at": t.created_at,
            }
            for t in txns
        ],
    }