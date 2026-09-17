"""
Invoice API Endpoints
======================
Fully migrated to async Beanie (MongoDB ODM).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId
from beanie.operators import In

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.udhaar import (
    UdhaarContract,
    UdhaarInvoice,
    UdhaarTransaction,
    UdhaarCustomer,
    ContractStatus,
    BillingFrequency,
)
from src.services.invoice_service import generate_invoice_for_contract

router = APIRouter(prefix="/udhaar", tags=["udhaar-invoices"])


async def _get_pump(pump_id: str, user: User) -> Pump:
    pump = await Pump.find_one(
        Pump.id == PydanticObjectId(pump_id),
        Pump.owner_id == PydanticObjectId(user.id)
    )
    if not pump:
        raise HTTPException(status_code=403, detail="Pump not found or not authorized")
    return pump


# ═══════════════════════════════════════════════════════════════════════════════
# MANUAL INVOICE GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/invoices/generate")
async def generate_invoice_manual(
    pump_id: str = Query(...),
    contract_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Manual invoice generation for a specific contract.
    """
    await _get_pump(pump_id, current_user)

    contract = await UdhaarContract.find_one(
        UdhaarContract.id == PydanticObjectId(contract_id),
        UdhaarContract.pump_id == PydanticObjectId(pump_id),
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    if contract.status != ContractStatus.active:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot generate invoice for contract with status: {contract.status.value}"
        )

    result = await generate_invoice_for_contract(
        contract=contract,
        reference_date=datetime.utcnow(),
    )

    if result.get("status") == "skipped" or result.get("created_count", 0) == 0:
        return {
            "message": "No new invoice generated",
            "detail": result.get("reason") or "Invoice already exists or no transactions found",
            "result": result,
        }

    return {
        "message": "Invoice generated successfully",
        "result": result,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE LIST
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/invoices")
async def list_invoices(
    pump_id: str = Query(...),
    customer_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="unpaid/paid/disputed/overdue"),
    skip: int = Query(0),
    limit: int = Query(50),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Master invoice page — all invoices with customer name included.
    """
    await _get_pump(pump_id, current_user)

    find_query = {"pump_id": PydanticObjectId(pump_id)}

    if customer_id:
        find_query["customer_id"] = PydanticObjectId(customer_id)
    if contract_id:
        find_query["contract_id"] = PydanticObjectId(contract_id)
    if status:
        find_query["status"] = status

    query = UdhaarInvoice.find(find_query)
    total = await query.count()
    invoices = await query.sort("-generated_at").skip(skip).limit(limit).to_list()

    customer_ids = list({inv.customer_id for inv in invoices})
    customers = await UdhaarCustomer.find(In(UdhaarCustomer.id, customer_ids)).to_list()
    customer_map = {c.id: c.name for c in customers}

    # Fetch transaction counts
    invoice_ids = [inv.id for inv in invoices]
    pipeline = [
        {"$match": {"invoice_id": {"$in": invoice_ids}}},
        {"$group": {"_id": "$invoice_id", "count": {"$sum": 1}}}
    ]
    cursor = UdhaarTransaction.aggregate(pipeline)
    counts = await cursor.to_list()
    count_map = {c["_id"]: c["count"] for c in counts if c.get("_id")}

    result = []
    for inv in invoices:
        result.append({
            "id": str(inv.id),
            "contract_id": str(inv.contract_id),
            "customer_id": str(inv.customer_id),
            "customer_name": customer_map.get(inv.customer_id, "Unknown"),
            "vehicle_id": str(inv.vehicle_id) if inv.vehicle_id else None,
            "pump_id": str(inv.pump_id),
            "cycle_start": inv.cycle_start,
            "cycle_end": inv.cycle_end,
            "total_amount": inv.total_amount,
            "rounded_amount": inv.rounded_amount,
            "status": inv.status,
            "transaction_count": count_map.get(inv.id, 0),
            "late_interest_applied": inv.late_interest_applied,
            "deposit_utilized": inv.deposit_utilized,
            "generated_at": inv.generated_at,
            "paid_at": inv.paid_at,
        })

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "invoices": result,
    }


@router.get("/invoices/{invoice_id}")
async def get_invoice_detail(
    pump_id: str = Query(...),
    invoice_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Full invoice detail:
    - Invoice metadata
    - Customer info
    - Contract summary
    - All linked transactions
    - Vehicle breakdown
    """
    await _get_pump(pump_id, current_user)

    invoice = await UdhaarInvoice.find_one(
        UdhaarInvoice.id == PydanticObjectId(invoice_id),
        UdhaarInvoice.pump_id == PydanticObjectId(pump_id),
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    customer = await UdhaarCustomer.get(invoice.customer_id)
    contract = await UdhaarContract.get(invoice.contract_id)

    transactions = await UdhaarTransaction.find(
        UdhaarTransaction.invoice_id == invoice.id
    ).sort("created_at").to_list()

    vehicle_breakdown = {}
    for t in transactions:
        vid = str(t.vehicle_id) if t.vehicle_id else "unassigned"
        if vid not in vehicle_breakdown:
            vehicle_breakdown[vid] = {"amount": 0.0, "quantity": 0.0, "txn_count": 0}
        vehicle_breakdown[vid]["amount"] += t.amount
        vehicle_breakdown[vid]["quantity"] += t.quantity
        vehicle_breakdown[vid]["txn_count"] += 1

    return {
        "invoice": {
            "id": str(invoice.id),
            "status": invoice.status,
            "cycle_start": invoice.cycle_start,
            "cycle_end": invoice.cycle_end,
            "total_amount": invoice.total_amount,
            "rounded_amount": invoice.rounded_amount,
            "late_interest_applied": invoice.late_interest_applied,
            "deposit_utilized": invoice.deposit_utilized,
            "generated_at": invoice.generated_at,
            "paid_at": invoice.paid_at,
            "dispute_reason": invoice.dispute_reason,
        },
        "customer": {
            "id": str(customer.id) if customer else None,
            "name": customer.name if customer else "Unknown",
            "contact_phone": customer.contact_phone if customer else None,
            "contact_email": customer.contact_email if customer else None,
        },
        "contract_summary": {
            "id": str(contract.id) if contract else None,
            "version": contract.version if contract else None,
            "billing_frequency": contract.billing_frequency if contract else None,
            "bill_by": contract.bill_by if contract else None,
            "security_deposit": contract.security_deposit if contract else None,
            "late_payment_interest": contract.late_payment_interest if contract else None,
            "invoice_dispute_days": contract.invoice_dispute_days if contract else None,
        },
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
                "meter_photo_url": t.meter_photo_url,
                "vehicle_photo_url": t.vehicle_photo_url,
                "fueling_video_url": t.fueling_video_url,
                "driver_verified": t.driver_verified,
                "invoice_id": str(t.invoice_id) if t.invoice_id else None,
                "created_at": t.created_at
            }
            for t in transactions
        ],
        "vehicle_breakdown": vehicle_breakdown,
        "transaction_count": len(transactions),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE STATUS MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.patch("/invoices/{invoice_id}/mark-paid")
async def mark_invoice_paid(
    pump_id: str = Query(...),
    invoice_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Mark an invoice as paid"""
    await _get_pump(pump_id, current_user)

    invoice = await UdhaarInvoice.find_one(
        UdhaarInvoice.id == PydanticObjectId(invoice_id),
        UdhaarInvoice.pump_id == PydanticObjectId(pump_id),
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    await invoice.save()

    return {"message": "Invoice marked as paid", "invoice_id": invoice_id}


@router.patch("/invoices/{invoice_id}/raise-dispute")
async def raise_invoice_dispute(
    pump_id: str = Query(...),
    invoice_id: str = Path(...),
    reason: str = Query(..., description="Reason for dispute"),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Raise a dispute on an invoice.
    Only allowed within invoice_dispute_days window set in contract.
    """
    await _get_pump(pump_id, current_user)

    invoice = await UdhaarInvoice.find_one(
        UdhaarInvoice.id == PydanticObjectId(invoice_id),
        UdhaarInvoice.pump_id == PydanticObjectId(pump_id),
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    contract = await UdhaarContract.get(invoice.contract_id)

    if contract and contract.invoice_dispute_days:
        days_since_generated = (datetime.utcnow() - invoice.generated_at).days
        if days_since_generated > contract.invoice_dispute_days:
            raise HTTPException(
                status_code=400,
                detail=f"Dispute window of {contract.invoice_dispute_days} days has passed"
            )

    invoice.status = "disputed"
    invoice.disputed_at = datetime.utcnow()
    invoice.dispute_reason = reason
    await invoice.save()

    return {
        "message": "Dispute raised",
        "invoice_id": invoice_id,
        "reason": reason,
    }


@router.post("/invoices/{invoice_id}/apply-interest")
async def apply_late_interest(
    pump_id: str = Query(...),
    invoice_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Manually apply late payment interest to an overdue invoice.
    Interest % is taken from the contract's late_payment_interest field.
    """
    await _get_pump(pump_id, current_user)

    invoice = await UdhaarInvoice.find_one(
        UdhaarInvoice.id == PydanticObjectId(invoice_id),
        UdhaarInvoice.pump_id == PydanticObjectId(pump_id),
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.status == "paid":
        raise HTTPException(status_code=400, detail="Cannot apply interest to a paid invoice")

    contract = await UdhaarContract.get(invoice.contract_id)

    if not contract or not contract.late_payment_interest:
        raise HTTPException(
            status_code=400,
            detail="No late payment interest configured in contract"
        )

    interest_amount = round(invoice.rounded_amount * (contract.late_payment_interest / 100), 2)
    invoice.late_interest_applied = interest_amount
    invoice.status = "overdue"
    await invoice.save()

    return {
        "message": "Late interest applied",
        "invoice_id": invoice_id,
        "base_amount": invoice.rounded_amount,
        "interest_percent": contract.late_payment_interest,
        "interest_amount": interest_amount,
        "total_due": round(invoice.rounded_amount + interest_amount, 2),
    }


@router.post("/invoices/{invoice_id}/utilize-deposit")
async def utilize_security_deposit(
    pump_id: str = Query(...),
    invoice_id: str = Path(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Use security deposit to settle an unpaid/overdue invoice.
    Validates deposit_utilization_days from contract before allowing.
    """
    await _get_pump(pump_id, current_user)

    invoice = await UdhaarInvoice.find_one(
        UdhaarInvoice.id == PydanticObjectId(invoice_id),
        UdhaarInvoice.pump_id == PydanticObjectId(pump_id),
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.status == "paid":
        raise HTTPException(status_code=400, detail="Invoice already paid")

    contract = await UdhaarContract.get(invoice.contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    if contract.deposit_utilization_days:
        days_unpaid = (datetime.utcnow() - invoice.generated_at).days
        if days_unpaid < contract.deposit_utilization_days:
            raise HTTPException(
                status_code=400,
                detail=f"Deposit can only be used after {contract.deposit_utilization_days} days of non-payment. Current: {days_unpaid} days."
            )

    total_due = invoice.rounded_amount + invoice.late_interest_applied
    deposit = contract.security_deposit

    if deposit <= 0:
        raise HTTPException(status_code=400, detail="No security deposit available")

    utilized = min(deposit, total_due)
    remaining_due = max(0.0, total_due - utilized)

    invoice.deposit_utilized = round(utilized, 2)
    contract.security_deposit = round(deposit - utilized, 2)

    if remaining_due == 0:
        invoice.status = "paid"
        invoice.paid_at = datetime.utcnow()
    else:
        invoice.status = "overdue"

    await invoice.save()
    await contract.save()

    return {
        "message": "Security deposit utilized",
        "invoice_id": invoice_id,
        "total_due": round(total_due, 2),
        "deposit_utilized": round(utilized, 2),
        "remaining_due": round(remaining_due, 2),
        "deposit_remaining": round(contract.security_deposit, 2),
        "invoice_status": invoice.status,
    }