"""
Invoice Generation Service
===========================
Core logic used by BOTH the scheduler and the manual API.
Single source of truth — no duplication.
Fully migrated to async Beanie (MongoDB ODM).
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple
from beanie import PydanticObjectId

from src.db.models.udhaar import (
    UdhaarContract,
    UdhaarInvoice,
    UdhaarTransaction,
    BillingFrequency,
    BillingCycle,
    BillBy,
    ContractStatus,
)


def get_current_billing_period(contract: UdhaarContract, reference_date: datetime) -> Tuple[datetime, datetime]:
    """
    Calculate the billing period (start, end) for a given reference date.
    Based on contract billing_cycle and billing_start_date.
    """
    start = contract.billing_start_date or contract.valid_from

    if contract.billing_cycle == BillingCycle.weekly:
        delta_days = 7
    elif contract.billing_cycle == BillingCycle.fortnightly:
        delta_days = 14
    else:
        # monthly — use calendar month boundaries
        delta_days = None

    if delta_days:
        # Calculate which interval we're in
        elapsed = (reference_date - start).days
        intervals_passed = elapsed // delta_days
        period_start = start + timedelta(days=intervals_passed * delta_days)
        period_end = period_start + timedelta(days=delta_days) - timedelta(seconds=1)
    else:
        # Monthly — use 1st of each month relative to start
        period_start = start.replace(
            year=reference_date.year,
            month=reference_date.month,
            day=start.day,
            hour=0, minute=0, second=0, microsecond=0
        )
        # If we're before the start day this month, go back one month
        if reference_date < period_start:
            if period_start.month == 1:
                period_start = period_start.replace(year=period_start.year - 1, month=12)
            else:
                period_start = period_start.replace(month=period_start.month - 1)

        # Period end = one month later minus 1 second
        if period_start.month == 12:
            period_end = period_start.replace(year=period_start.year + 1, month=1) - timedelta(seconds=1)
        else:
            period_end = period_start.replace(month=period_start.month + 1) - timedelta(seconds=1)

    return period_start, period_end


async def invoice_already_exists(
    contract_id: PydanticObjectId,
    vehicle_id: Optional[PydanticObjectId],
    period_start: datetime,
    period_end: datetime,
) -> bool:
    """
    Duplicate check — has an invoice already been generated
    for this contract + vehicle + period?
    """
    query = {
        "contract_id": contract_id,
        "cycle_start": period_start,
        "cycle_end": period_end,
    }
    if vehicle_id:
        query["vehicle_id"] = vehicle_id
    else:
        query["vehicle_id"] = None

    existing = await UdhaarInvoice.find_one(query)
    return existing is not None


async def generate_invoice_for_contract(
    contract: UdhaarContract,
    reference_date: Optional[datetime] = None,
    force: bool = False,
) -> dict:
    """
    Core invoice generation logic.

    Args:
        contract   : Active UdhaarContract object
        reference_date : Date to calculate billing period from (default: now)
        force      : Skip duplicate check (use with caution)

    Returns:
        dict with status, invoice_id (if created), message
    """
    now = reference_date or datetime.utcnow()

    # One-time contracts — generate only once
    if contract.billing_frequency == BillingFrequency.one_time:
        existing = await UdhaarInvoice.find_one(
            UdhaarInvoice.contract_id == contract.id
        )
        if existing and not force:
            return {
                "status": "skipped",
                "reason": "One-time invoice already generated",
                "existing_invoice_id": str(existing.id),
            }
        period_start = contract.valid_from
        period_end = contract.valid_to
    else:
        period_start, period_end = get_current_billing_period(contract, now)

    results = []

    if contract.bill_by == BillBy.vehicle:
        # One invoice per vehicle
        pipeline = [
            {
                "$match": {
                    "contract_id": contract.id,
                    "created_at": {"$gte": period_start, "$lte": period_end},
                    "vehicle_id": {"$ne": None}
                }
            },
            {
                "$group": {
                    "_id": "$vehicle_id"
                }
            }
        ]
        cursor = UdhaarTransaction.aggregate(pipeline)
        distinct_vehicles = await cursor.to_list()
        vehicle_ids = [v["_id"] for v in distinct_vehicles if v.get("_id")]

        for vid in vehicle_ids:
            result = await _create_single_invoice(
                contract=contract,
                vehicle_id=vid,
                period_start=period_start,
                period_end=period_end,
                force=force,
            )
            results.append(result)

        if not vehicle_ids:
            return {
                "status": "skipped",
                "reason": "No transactions found in this billing period",
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            }

    else:
        # One consolidated invoice for all vehicles
        result = await _create_single_invoice(
            contract=contract,
            vehicle_id=None,
            period_start=period_start,
            period_end=period_end,
            force=force,
        )
        results.append(result)

    created = [r for r in results if r["status"] == "created"]
    skipped = [r for r in results if r["status"] == "skipped"]

    return {
        "status": "done",
        "created_count": len(created),
        "skipped_count": len(skipped),
        "invoices": results,
    }


async def _create_single_invoice(
    contract: UdhaarContract,
    vehicle_id: Optional[PydanticObjectId],
    period_start: datetime,
    period_end: datetime,
    force: bool = False,
) -> dict:
    """Create one invoice for a contract+vehicle (or contract-level if vehicle_id=None)"""

    # Duplicate check
    if not force and await invoice_already_exists(contract.id, vehicle_id, period_start, period_end):
        return {
            "status": "skipped",
            "reason": "Invoice already exists for this period",
            "vehicle_id": str(vehicle_id) if vehicle_id else None,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        }

    # Aggregate transactions
    txn_query = {
        "contract_id": contract.id,
        "created_at": {"$gte": period_start, "$lte": period_end}
    }
    if vehicle_id:
        txn_query["vehicle_id"] = vehicle_id

    transactions = await UdhaarTransaction.find(txn_query).to_list()

    if not transactions:
        return {
            "status": "skipped",
            "reason": "No transactions in this billing period",
            "vehicle_id": str(vehicle_id) if vehicle_id else None,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        }

    total_amount = sum(t.amount for t in transactions)

    # Round off if enabled (ceiling to nearest 1 unit)
    import math
    rounded_amount = math.ceil(total_amount) if contract.round_off else total_amount

    invoice = UdhaarInvoice(
        contract_id=contract.id,
        customer_id=contract.customer_id,
        vehicle_id=vehicle_id,
        pump_id=contract.pump_id,
        cycle_start=period_start,
        cycle_end=period_end,
        total_amount=round(total_amount, 2),
        rounded_amount=round(rounded_amount, 2),
        status="unpaid",
        late_interest_applied=0.0,
        deposit_utilized=0.0,
    )
    await invoice.insert()

    # Link all transactions to this invoice
    txn_ids = [t.id for t in transactions]
    await UdhaarTransaction.find(UdhaarTransaction.id.in_(txn_ids)).update({"$set": {"invoice_id": invoice.id}})

    return {
        "status": "created",
        "invoice_id": str(invoice.id),
        "vehicle_id": str(vehicle_id) if vehicle_id else None,
        "total_amount": round(total_amount, 2),
        "rounded_amount": round(rounded_amount, 2),
        "transaction_count": len(transactions),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }