"""
FILE: src/api/v1/payment.py
Fully migrated to async Beanie (MongoDB ODM).
"""

import hashlib
import json
import random
import string
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from beanie import PydanticObjectId
from beanie.operators import In

from src.core.dependencies import require_role
from src.db.models.payment import PaymentRequest
from src.db.models.user import User
from src.db.schemas.payment import PaymentRequestCreate, PaymentRequestResponse

router = APIRouter(prefix="/payment", tags=["payment"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()

def _generate_otp(length: int = 6) -> str:
    return ''.join(random.choices(string.digits, k=length))


# ─── Schemas ──────────────────────────────────────────────────────────────────

class PaymentRequestCreateExtended(BaseModel):
    pump_id: Optional[str] = None  # Optional for vouchers
    amount: float
    payment_type: str
    transaction_reference: Optional[str] = None
    remarks: Optional[str] = None
    screenshot_url: Optional[str] = None
    logistic_form_data: Optional[dict] = None   # vehicle_plate, purpose, etc.

class ContractTermsIn(BaseModel):
    credit_limit: float
    billing_cycle: Optional[str] = "monthly"
    late_payment_interest: Optional[float] = 2.0
    dispute_window_days: Optional[int] = 15
    valid_days: Optional[int] = 365
    remarks: Optional[str] = None

class SignRequest(BaseModel):
    otp: str
    ip_address: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# LOGISTIC SIDE
# ═══════════════════════════════════════════════════════════════════

@router.post("/request")
async def create_payment_request(
    payment_data: PaymentRequestCreateExtended,
    current_user: User = Depends(require_role(["logistic"]))
):
    """
    Logistic Partner payment proof + optional form data submit karta hai.
    """
    try:
        pump_oid = PydanticObjectId(payment_data.pump_id) if payment_data.pump_id else None
        status = "pending"
        if payment_data.payment_type == "wallet_topup" and payment_data.transaction_reference and (
            "stripe" in payment_data.transaction_reference.lower() or 
            "razorpay" in payment_data.transaction_reference.lower()
        ):
            status = "approved"

        payment = PaymentRequest(
            logistic_partner_id=PydanticObjectId(current_user.id),
            pump_id=pump_oid,
            amount=payment_data.amount,
            payment_type=payment_data.payment_type,
            transaction_reference=payment_data.transaction_reference,
            remarks=payment_data.remarks,
            screenshot_url=payment_data.screenshot_url,
            logistic_form_data=payment_data.logistic_form_data or None,
            status=status
        )
        await payment.insert()
        return {
            "id": str(payment.id),
            "logistic_partner_id": str(payment.logistic_partner_id),
            "pump_id": str(payment.pump_id) if payment.pump_id else None,
            "amount": payment.amount,
            "payment_type": payment.payment_type,
            "transaction_reference": payment.transaction_reference,
            "status": payment.status,
            "remarks": payment.remarks,
            "screenshot_url": payment.screenshot_url,
            "requested_at": payment.requested_at,
            "reviewed_at": payment.reviewed_at,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create payment request: {str(e)}")


@router.get("/my-requests")
async def get_my_payment_requests(
    current_user: User = Depends(require_role(["logistic"]))
):
    """Logistic Partner apne sabhi payment requests dekh sakta hai"""
    from src.db.models.pump import Pump

    pay_requests = await PaymentRequest.find(
        PaymentRequest.logistic_partner_id == PydanticObjectId(current_user.id)
    ).sort("-requested_at").to_list()

    pump_ids = list({p.pump_id for p in pay_requests if p.pump_id})
    pumps = await Pump.find(In(Pump.id, pump_ids)).to_list()
    pump_map = {pump.id: pump.name for pump in pumps}

    response_data = []
    for pay in pay_requests:
        contract = pay.contract_terms if isinstance(pay.contract_terms, dict) else (json.loads(pay.contract_terms) if pay.contract_terms else None)
        response_data.append({
            "id": str(pay.id),
            "pump_id": str(pay.pump_id) if pay.pump_id else None,
            "pump_name": pump_map.get(pay.pump_id) or "N/A",
            "amount": pay.amount,
            "payment_type": pay.payment_type,
            "transaction_reference": pay.transaction_reference,
            "remarks": pay.remarks,
            "screenshot_url": pay.screenshot_url,
            "status": pay.status,
            "logistic_form_data": pay.logistic_form_data if isinstance(pay.logistic_form_data, dict) else (json.loads(pay.logistic_form_data) if pay.logistic_form_data else None),
            "contract_terms": contract,
            "contract_generated_at": pay.contract_generated_at.isoformat() if pay.contract_generated_at else None,
            "logistic_signed": pay.logistic_signed,
            "logistic_signed_at": pay.logistic_signed_at.isoformat() if pay.logistic_signed_at else None,
            "pump_owner_signed": pay.pump_owner_signed,
            "pump_owner_signed_at": pay.pump_owner_signed_at.isoformat() if pay.pump_owner_signed_at else None,
            "requested_at": pay.requested_at.strftime("%Y-%m-%d %H:%M") if pay.requested_at else None,
            "reviewed_at": pay.reviewed_at.strftime("%Y-%m-%d %H:%M") if pay.reviewed_at else None,
        })
    return response_data


@router.get("/detail/{request_id}")
async def get_payment_detail(
    request_id: str,
    current_user: User = Depends(require_role(["logistic", "pump_owner"]))
):
    """Get single payment request with full contract detail"""
    from src.db.models.pump import Pump

    pay = await PaymentRequest.get(PydanticObjectId(request_id))
    if not pay:
        raise HTTPException(status_code=404, detail="Payment request not found")

    pump = await Pump.get(pay.pump_id)
    partner = await User.get(pay.logistic_partner_id)

    return {
        "id": str(pay.id),
        "pump_id": str(pay.pump_id) if pay.pump_id else None,
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
        },
        "amount": pay.amount,
        "payment_type": pay.payment_type,
        "transaction_reference": pay.transaction_reference,
        "remarks": pay.remarks,
        "screenshot_url": pay.screenshot_url,
        "status": pay.status,
        "logistic_form_data": pay.logistic_form_data if isinstance(pay.logistic_form_data, dict) else (json.loads(pay.logistic_form_data) if pay.logistic_form_data else None),
        "contract_terms": pay.contract_terms if isinstance(pay.contract_terms, dict) else (json.loads(pay.contract_terms) if pay.contract_terms else None),
        "contract_generated_at": pay.contract_generated_at.isoformat() if pay.contract_generated_at else None,
        "logistic_signed": pay.logistic_signed,
        "logistic_signed_at": pay.logistic_signed_at.isoformat() if pay.logistic_signed_at else None,
        "pump_owner_signed": pay.pump_owner_signed,
        "pump_owner_signed_at": pay.pump_owner_signed_at.isoformat() if pay.pump_owner_signed_at else None,
        "requested_at": pay.requested_at.isoformat() if pay.requested_at else None,
        "reviewed_at": pay.reviewed_at.isoformat() if pay.reviewed_at else None,
    }


# ═══════════════════════════════════════════════════════════════════
# PUMP OWNER SIDE
# ═══════════════════════════════════════════════════════════════════

@router.get("/pending")
async def get_pending_payments(
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Pump Owner ko pending payment requests dikhe — partner info ke saath"""
    from src.db.models.pump import Pump
    from src.db.models.customer import Customer
    from src.db.models.vehicle import Vehicle

    pumps = await Pump.find(Pump.owner_id == PydanticObjectId(current_user.id)).to_list()
    pump_ids = [pump.id for pump in pumps]
    pump_map = {pump.id: pump for pump in pumps}

    pay_requests = await PaymentRequest.find(
        In(PaymentRequest.pump_id, pump_ids),
        In(PaymentRequest.status, ["pending", "contract_generated"])
    ).to_list()

    partner_ids = list({pay.logistic_partner_id for pay in pay_requests})
    partners = await User.find(In(User.id, partner_ids)).to_list()
    partner_map = {partner.id: partner for partner in partners}

    vehicles = await Vehicle.find(In(Vehicle.partner_id, partner_ids)).to_list()
    vehicles_by_partner = {}
    for v in vehicles:
        vehicles_by_partner.setdefault(v.partner_id, []).append(v)

    response_data = []
    for pay in pay_requests:
        partner = partner_map.get(pay.logistic_partner_id)
        pump = pump_map.get(pay.pump_id)
        if not partner or not pump:
            continue

        partner_vehicles = vehicles_by_partner.get(partner.id, [])
        vehicle_plates = [v.vehicle_plate for v in partner_vehicles]

        existing_outstanding = 0.0
        for plate in vehicle_plates:
            customer = await Customer.find_one(
                Customer.vehicle_plate == plate,
                Customer.pump_id == pay.pump_id
            )
            if customer:
                existing_outstanding = customer.outstanding_amount - customer.credit_limit

        logistic_form = json.loads(pay.logistic_form_data) if pay.logistic_form_data else {}
        contract = json.loads(pay.contract_terms) if pay.contract_terms else None

        response_data.append({
            "id": str(pay.id),
            "logistic_partner_id": str(pay.logistic_partner_id),
            "logistic_partner_name": partner.full_name or partner.email,
            "logistic_partner_phone": partner.phone or "N/A",
            "logistic_partner_email": partner.email,
            "pump_id": str(pay.pump_id),
            "pump_name": pump.name,
            "amount": pay.amount,
            "payment_type": pay.payment_type,
            "transaction_reference": pay.transaction_reference,
            "remarks": pay.remarks,
            "screenshot_url": pay.screenshot_url,
            "status": pay.status,
            "logistic_form_data": logistic_form,
            "contract_terms": contract,
            "contract_generated_at": pay.contract_generated_at.isoformat() if pay.contract_generated_at else None,
            "logistic_signed": pay.logistic_signed,
            "logistic_signed_at": pay.logistic_signed_at.isoformat() if pay.logistic_signed_at else None,
            "pump_owner_signed": pay.pump_owner_signed,
            "pump_owner_signed_at": pay.pump_owner_signed_at.isoformat() if pay.pump_owner_signed_at else None,
            "existing_outstanding": existing_outstanding,
            "net_new_credit": max(0.0, pay.amount - existing_outstanding),
            "requested_at": pay.requested_at.strftime("%Y-%m-%d %H:%M") if pay.requested_at else None,
        })
    return response_data


@router.post("/generate-contract/{request_id}")
async def generate_contract(
    request_id: str,
    terms: ContractTermsIn,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Pump Owner contract create karta hai (payment approve se pehle).
    Status: pending → contract_generated
    """
    from src.db.models.pump import Pump

    payment = await PaymentRequest.get(PydanticObjectId(request_id))
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")

    pump = await Pump.find_one(
        Pump.id == payment.pump_id,
        Pump.owner_id == PydanticObjectId(current_user.id)
    )
    if not pump:
        raise HTTPException(status_code=403, detail="This pump does not belong to you")

    if payment.status not in ["pending", "contract_generated"]:
        raise HTTPException(status_code=400, detail=f"Cannot generate contract in status: {payment.status}")

    partner = await User.get(payment.logistic_partner_id)
    logistic_form = json.loads(payment.logistic_form_data) if payment.logistic_form_data else {}

    now = datetime.utcnow()
    valid_from = now
    valid_to = now + timedelta(days=terms.valid_days or 365)

    contract_data = {
        "credit_limit": terms.credit_limit,
        "payment_amount": payment.amount,
        "billing_cycle": terms.billing_cycle or "monthly",
        "late_payment_interest": terms.late_payment_interest or 2.0,
        "dispute_window_days": terms.dispute_window_days or 15,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat(),
        "pump_name": pump.name,
        "pump_address": getattr(pump, "address", None),
        "pump_gst": getattr(pump, "gst", None),
        "partner_name": partner.full_name or partner.email if partner else None,
        "partner_email": partner.email if partner else None,
        "partner_phone": partner.phone if partner else None,
        "vehicle_plate": logistic_form.get("vehicle_plate"),
        "vehicle_type": logistic_form.get("vehicle_type"),
        "driver_name": logistic_form.get("driver_name"),
        "purpose": logistic_form.get("purpose"),
        "transaction_reference": payment.transaction_reference,
        "contract_remarks": terms.remarks,
        "generated_by": str(current_user.id),
        "generated_at": now.isoformat(),
    }

    payment.contract_terms = json.dumps(contract_data)
    payment.contract_generated_at = now
    payment.status = "contract_generated"

    await payment.save()

    return {
        "message": "Contract generated successfully",
        "status": payment.status,
        "contract_terms": contract_data,
        "next_step": "Logistic partner needs to sign the contract"
    }


@router.post("/send-otp/{request_id}")
async def send_signing_otp(
    request_id: str,
    current_user: User = Depends(require_role(["logistic", "pump_owner"]))
):
    """Generate OTP for contract signing (logistic or pump owner)"""
    payment = await PaymentRequest.get(PydanticObjectId(request_id))
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")

    if payment.status not in ["contract_generated", "logistic_signed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Contract not ready for signing. Current status: {payment.status}"
        )

    otp = _generate_otp(6)
    otp_hash = _hash_otp(otp)

    if str(current_user.id) == str(payment.logistic_partner_id):
        payment.logistic_otp_hash = otp_hash
    else:
        from src.db.models.pump import Pump
        pump = await Pump.find_one(
            Pump.id == payment.pump_id,
            Pump.owner_id == PydanticObjectId(current_user.id)
        )
        if not pump:
            raise HTTPException(status_code=403, detail="Not authorized to sign this contract")
        payment.pump_owner_otp_hash = otp_hash

    await payment.save()

    return {
        "message": "OTP generated for contract signing",
        "otp": otp,
        "note": "In production this OTP will be sent to your registered mobile/email"
    }


@router.post("/sign/{request_id}")
async def sign_contract(
    request_id: str,
    data: SignRequest,
    request: Request,
    current_user: User = Depends(require_role(["logistic", "pump_owner"]))
):
    """
    OTP verify karke payment contract sign karo.
    Flow:
    - Logistic signs → status: logistic_signed
    - Pump owner signs → status: pump_signed → approve + credit update
    """
    payment = await PaymentRequest.get(PydanticObjectId(request_id))
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")

    if payment.status not in ["contract_generated", "logistic_signed"]:
        raise HTTPException(status_code=400, detail=f"Contract not in signable state: {payment.status}")

    client_ip = request.client.host if request.client else data.ip_address or "unknown"
    otp_hash = _hash_otp(data.otp)
    now = datetime.utcnow()

    # ── LOGISTIC SIGNING ─────────────────────────────────────────
    if str(current_user.id) == str(payment.logistic_partner_id):
        if payment.logistic_signed:
            raise HTTPException(status_code=400, detail="You have already signed this contract")
        if not payment.logistic_otp_hash:
            raise HTTPException(status_code=400, detail="Please request OTP first")
        if payment.logistic_otp_hash != otp_hash:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        payment.logistic_signed = True
        payment.logistic_signed_at = now
        payment.logistic_sign_ip = client_ip
        payment.logistic_otp_hash = None

        if payment.pump_owner_signed:
            await _finalize_payment(payment, now, current_user)
        else:
            payment.status = "logistic_signed"

        await payment.save()
        return {
            "message": "Contract signed by logistic partner",
            "status": payment.status,
            "next_step": "Waiting for pump owner to sign" if not payment.pump_owner_signed else "Payment approved"
        }

    # ── PUMP OWNER SIGNING ───────────────────────────────────────
    else:
        from src.db.models.pump import Pump
        pump = await Pump.find_one(
            Pump.id == payment.pump_id,
            Pump.owner_id == PydanticObjectId(current_user.id)
        )
        if not pump:
            raise HTTPException(status_code=403, detail="Not authorized to sign this contract")

        if payment.pump_owner_signed:
            raise HTTPException(status_code=400, detail="You have already signed this contract")
        if not payment.pump_owner_otp_hash:
            raise HTTPException(status_code=400, detail="Please request OTP first")
        if payment.pump_owner_otp_hash != otp_hash:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        payment.pump_owner_signed = True
        payment.pump_owner_signed_at = now
        payment.pump_owner_sign_ip = client_ip
        payment.pump_owner_otp_hash = None

        if payment.logistic_signed:
            await _finalize_payment(payment, now, current_user)
        else:
            payment.status = "pump_signed"

        await payment.save()
        return {
            "message": "Contract signed by pump owner",
            "status": payment.status,
            "next_step": "Waiting for logistic partner to sign" if not payment.logistic_signed else "Payment approved"
        }


@router.post("/reject/{request_id}")
async def reject_payment(
    request_id: str,
    reason: str = "Payment proof not verified",
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Pump Owner payment request reject karta hai"""
    from src.db.models.pump import Pump

    payment = await PaymentRequest.get(PydanticObjectId(request_id))
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")

    pump = await Pump.find_one(
        Pump.id == payment.pump_id,
        Pump.owner_id == PydanticObjectId(current_user.id)
    )
    if not pump:
        raise HTTPException(status_code=403, detail="This pump does not belong to you")

    payment.status = "rejected"
    payment.remarks = f"Rejected: {reason}"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = PydanticObjectId(current_user.id)

    await payment.save()
    return {"message": "Payment request rejected", "reason": reason}


# Legacy direct-approve (kept for backward compat, only works on 'pending' without contract)
@router.post("/approve/{request_id}")
async def approve_payment(
    request_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    from src.db.models.pump import Pump

    payment = await PaymentRequest.get(PydanticObjectId(request_id))
    if not payment:
        raise HTTPException(status_code=404, detail="Payment request not found")

    pump = await Pump.find_one(
        Pump.id == payment.pump_id,
        Pump.owner_id == PydanticObjectId(current_user.id)
    )
    if not pump:
        raise HTTPException(status_code=403, detail="This pump does not belong to you")

    if payment.status == "pending" and not payment.contract_terms:
        raise HTTPException(
            status_code=400,
            detail="Please create a contract first using POST /payment/generate-contract/{id}"
        )

    now = datetime.utcnow()
    await _finalize_payment(payment, now, current_user)
    await payment.save()

    return {
        "message": "Payment approved and credit updated",
        "amount_received": payment.amount,
        "status": "approved"
    }


# ─── Internal Helpers ─────────────────────────────────────────────────────────

async def _finalize_payment(payment: PaymentRequest, now: datetime, reviewed_by: User):
    """Both signed → approve payment and update credit"""
    from src.db.models.customer import Customer
    from src.db.models.vehicle import Vehicle
    from src.db.models.udhaar_alert import UdhaarAlert

    payment.status = "approved"
    payment.reviewed_at = now
    payment.reviewed_by = PydanticObjectId(reviewed_by.id)

    vehicles = await Vehicle.find(
        Vehicle.partner_id == payment.logistic_partner_id
    ).to_list()

    remaining_payment = payment.amount
    total_settled = 0.0

    for vehicle in vehicles:
        if remaining_payment <= 0:
            break
        customer = await Customer.find_one(
            Customer.vehicle_plate == vehicle.vehicle_plate,
            Customer.pump_id == payment.pump_id
        )
        if not customer:
            continue
        if customer.outstanding_amount > 0:
            settle_amount = min(customer.outstanding_amount, remaining_payment)
            customer.outstanding_amount -= settle_amount
            remaining_payment -= settle_amount
            total_settled += settle_amount
            customer.updated_at = now
            await customer.save()
            
            # Resolve outstanding alerts
            await UdhaarAlert.find(
                UdhaarAlert.customer_id == customer.id,
                UdhaarAlert.is_resolved == False
            ).update({"$set": {"is_resolved": True, "resolved_at": now}})

    contract = json.loads(payment.contract_terms) if payment.contract_terms else {}
    new_credit_limit = contract.get("credit_limit", max(0.0, remaining_payment))

    for vehicle in vehicles:
        customer = await Customer.find_one(
            Customer.vehicle_plate == vehicle.vehicle_plate,
            Customer.pump_id == payment.pump_id
        )
        if customer:
            customer.credit_limit = new_credit_limit
            customer.updated_at = now
            await customer.save()
        else:
            partner = await User.get(payment.logistic_partner_id)
            new_customer = Customer(
                name=partner.full_name or partner.email,
                phone=partner.phone or None,
                vehicle_plate=vehicle.vehicle_plate,
                vehicle_type=vehicle.vehicle_type or "Fleet",
                credit_limit=new_credit_limit,
                outstanding_amount=0.0,
                is_fleet=True,
                pump_id=payment.pump_id,
            )
            await new_customer.insert()