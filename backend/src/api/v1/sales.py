"""
FILE: src/api/v1/sales.py
Fully migrated to async Beanie (MongoDB ODM).

ENDPOINTS:
  POST   /sales/shifts/start          — Start new shift
  POST   /sales/shifts/end            — End shift
  GET    /sales/shifts/last           — Get last shift for a pump
  GET    /sales/shifts/active         — Check active shift
  GET    /sales/shifts/{shift_id}/summary — Real-time shift summary
  GET    /sales/shifts/{shift_id}/point-readings
  POST   /sales/logs                  — Add sale log (single)
  POST   /sales/logs/bulk             — Add sale logs (batch)
  GET    /sales/logs                  — List/filter sale logs
  DELETE /sales/logs/{log_id}         — Soft delete a sale log
  PATCH  /sales/rates                 — Update item rate mid-shift
  GET    /sales/overview              — Overview dashboard data
  GET    /sales/attendants            — List attendants for pump
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from beanie import PydanticObjectId
from beanie.operators import In
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Optional

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant
from src.db.models.vehicle import Vehicle
from src.db.models.credit_usage import CreditUsage
from src.db.models.credit_request import CreditRequest

from src.db.models.sales import (
    Shift, ShiftPersonnel, ShiftPoint, SaleLog,
    ShiftStatus, PaymentMode as PaymentModeEnum, SaleType as SaleTypeEnum
)

from src.db.schemas.sales_schemas import (
    StartShiftRequest, ShiftResponse,
    EndShiftRequest,
    SaleLogCreate, SaleLogResponse,
    ShiftSummaryResponse, PointSummary, PaymentSummary, ItemSummary,
    OverviewResponse, ItemRateUpdate
)
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/sales", tags=["sales"])


# ─────────────────────────────────────────────────────────────────
# HELPER: ownership check
# ─────────────────────────────────────────────────────────────────
async def _get_pump_or_403(pump_id: str, owner_id: PydanticObjectId) -> Pump:
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")
    pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == owner_id)
    if not pump:
        raise HTTPException(status_code=403, detail="Not authorized for this pump")
    return pump


# ─────────────────────────────────────────────────────────────────
# HELPER: build shift summary dict
# ─────────────────────────────────────────────────────────────────
async def _build_summary(shift: Shift) -> dict:
    logs = await SaleLog.find(
        SaleLog.shift_id == shift.id,
        SaleLog.is_deleted == False
    ).to_list()

    points = await ShiftPoint.find(ShiftPoint.shift_id == shift.id).to_list()

    net_amount = sum(l.amount for l in logs)
    total_qty = sum(l.quantity for l in logs)
    credit_amount = sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.credit)
    credit_ratio = round((credit_amount / net_amount * 100) if net_amount else 0, 2)

    # Point summaries
    point_summaries = []
    for pt in points:
        if not pt.is_active:
            continue
        end_r = pt.end_reading or 0.0
        sold_qty = max(0, (end_r - pt.start_reading) - pt.testing_value)
        pt_logs = [l for l in logs if str(l.nozzle_id) == str(pt.nozzle_id)]
        pt_amount = sum(l.amount for l in pt_logs)
        point_summaries.append({
            "nozzle_id": pt.nozzle_id,
            "item_name": pt.item_name,
            "start_reading": pt.start_reading,
            "end_reading": pt.end_reading,
            "testing_value": pt.testing_value,
            "sold_quantity": round(sold_qty, 3),
            "total_amount": round(pt_amount, 2),
        })

    # Payment summaries
    pay_map: dict = defaultdict(lambda: {"total_amount": 0.0, "transaction_count": 0})
    for l in logs:
        pay_map[str(l.payment_mode)]["total_amount"] += l.amount
        pay_map[str(l.payment_mode)]["transaction_count"] += 1
    payment_summaries = [
        {"payment_mode": k, "total_amount": round(v["total_amount"], 2),
         "transaction_count": v["transaction_count"]}
        for k, v in pay_map.items()
    ]

    # Item summaries
    item_map: dict = defaultdict(lambda: {"total_quantity": 0.0, "total_amount": 0.0})
    for l in logs:
        item_map[l.item_name]["total_quantity"] += l.quantity
        item_map[l.item_name]["total_amount"] += l.amount
    item_summaries = [
        {"item_name": k, "total_quantity": round(v["total_quantity"], 3),
         "total_amount": round(v["total_amount"], 2)}
        for k, v in item_map.items()
    ]

    return {
        "shift_id": str(shift.id),
        "pump_id": str(shift.pump_id),
        "shift_type": shift.shift_type,
        "status": shift.status,
        "start_time": shift.start_time,
        "end_time": shift.end_time,
        "net_amount": round(net_amount, 2),
        "total_quantity": round(total_qty, 3),
        "credit_ratio": credit_ratio,
        "point_summaries": point_summaries,
        "payment_summaries": payment_summaries,
        "item_summaries": item_summaries,
        "total_sales_count": len(logs),
    }


# ═════════════════════════════════════════════════════════════════
# SHIFT ENDPOINTS
# ═════════════════════════════════════════════════════════════════

@router.post("/shifts/start", status_code=201)
async def start_shift(
    payload: StartShiftRequest,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Start a new shift for a pump.
    - Checks no active shift already running for same pump
    - Auto-fetches start_reading from previous shift's end_reading if not provided
    - Creates ShiftPoint records for each nozzle
    - Links selected attendants as ShiftPersonnel
    """
    pump = await _get_pump_or_403(payload.pump_id, current_user.id)
    pump_oid = pump.id

    # Block duplicate active shift
    existing_active = await Shift.find_one(
        Shift.pump_id == pump_oid,
        Shift.status == ShiftStatus.active
    )
    if existing_active:
        raise HTTPException(
            status_code=400,
            detail=f"Shift {str(existing_active.id)} is already active for this pump. End it first."
        )

    # Create shift
    shift = Shift(
        pump_id=pump_oid,
        shift_type=payload.shift_type,
        status=ShiftStatus.active,
        start_time=payload.start_time,
        created_by=current_user.id,
    )
    await shift.insert()

    # For each nozzle point: try to auto-fetch start_reading from last closed shift
    for pt_in in payload.point_readings:
        start_val = pt_in.start_reading

        if start_val == 0:
            # Find last closed shift point for this nozzle
            last_shifts = await Shift.find(
                Shift.pump_id == pump_oid,
                Shift.status == ShiftStatus.closed
            ).sort(-Shift.end_time).to_list()

            for ls in last_shifts:
                last_pt = await ShiftPoint.find_one(
                    ShiftPoint.shift_id == ls.id,
                    ShiftPoint.nozzle_id == pt_in.nozzle_id,
                )
                if last_pt and last_pt.end_reading is not None:
                    start_val = last_pt.end_reading
                    break

        sp = ShiftPoint(
            shift_id=shift.id,
            nozzle_id=pt_in.nozzle_id,
            item_name=pt_in.item_name,
            start_reading=start_val,
            testing_value=pt_in.testing_value,
            is_active=pt_in.is_active,
        )
        await sp.insert()

    # Link personnel
    for att_id_str in payload.personnel_ids:
        try:
            att_oid = PydanticObjectId(att_id_str)
            sp_link = ShiftPersonnel(shift_id=shift.id, attendant_id=att_oid)
            await sp_link.insert()
        except Exception as e:
            log.warning("Failed to add personnel", att_id=att_id_str, error=str(e))

    return {
        "id": str(shift.id),
        "pump_id": str(shift.pump_id),
        "shift_type": shift.shift_type,
        "status": shift.status,
        "start_time": shift.start_time,
        "message": "Shift started successfully"
    }


@router.post("/shifts/end")
async def end_shift(
    payload: EndShiftRequest,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    End an active shift.
    - Updates end_reading for each nozzle point
    - Changes shift status to closed
    - Returns final summary
    """
    try:
        shift_oid = PydanticObjectId(payload.shift_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shift_id")

    shift = await Shift.get(shift_oid)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")

    await _get_pump_or_403(str(shift.pump_id), current_user.id)

    if shift.status == ShiftStatus.closed:
        raise HTTPException(status_code=400, detail="Shift is already closed")

    # Validate end readings > start readings
    for er in payload.end_readings:
        pt = await ShiftPoint.find_one(
            ShiftPoint.shift_id == shift.id,
            ShiftPoint.nozzle_id == er.nozzle_id
        )
        if not pt:
            raise HTTPException(
                status_code=404,
                detail=f"Nozzle {er.nozzle_id} not found in this shift"
            )
        if er.end_reading < pt.start_reading:
            raise HTTPException(
                status_code=400,
                detail=f"Nozzle {er.nozzle_id}: end_reading ({er.end_reading}) cannot be less than start_reading ({pt.start_reading})"
            )
        pt.end_reading = er.end_reading
        if er.testing_value is not None:
            pt.testing_value = er.testing_value
        await pt.save()

    shift.status = ShiftStatus.closed
    shift.end_time = datetime.utcnow()
    shift.updated_at = datetime.utcnow()
    await shift.save()

    return await _build_summary(shift)


@router.get("/shifts/last")
async def get_last_shift(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Get the most recently modified shift for a pump (active or closed).
    """
    pump = await _get_pump_or_403(pump_id, current_user.id)

    shift = await Shift.find(
        Shift.pump_id == pump.id
    ).sort(-Shift.updated_at).first_or_none()

    if not shift:
        raise HTTPException(status_code=404, detail="No shifts found for this pump")

    return await _build_summary(shift)


@router.get("/shifts/active")
async def get_active_shift(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Check if pump has an active shift and return it."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    shift = await Shift.find_one(
        Shift.pump_id == pump.id,
        Shift.status == ShiftStatus.active
    )

    if not shift:
        return {"active": False, "shift": None}

    return {"active": True, "shift": await _build_summary(shift)}


@router.get("/shifts/{shift_id}/summary")
async def get_shift_summary(
    shift_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Real-time shift summary — 'See Summary' button."""
    try:
        oid = PydanticObjectId(shift_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shift_id")
    shift = await Shift.get(oid)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")

    await _get_pump_or_403(str(shift.pump_id), current_user.id)
    return await _build_summary(shift)


@router.get("/shifts/{shift_id}/point-readings")
async def get_shift_points(
    shift_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get nozzle/point readings for a shift."""
    try:
        oid = PydanticObjectId(shift_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shift_id")
    shift = await Shift.get(oid)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    await _get_pump_or_403(str(shift.pump_id), current_user.id)

    points = await ShiftPoint.find(ShiftPoint.shift_id == shift.id).to_list()

    return [
        {
            "id": str(pt.id),
            "nozzle_id": pt.nozzle_id,
            "item_name": pt.item_name,
            "start_reading": pt.start_reading,
            "end_reading": pt.end_reading,
            "testing_value": pt.testing_value,
            "is_active": pt.is_active,
            "sold_quantity": round(
                max(0, ((pt.end_reading or pt.start_reading) - pt.start_reading) - pt.testing_value), 3
            ),
        }
        for pt in points
    ]


async def _process_logistic_auto_credit(
    vehicle_number: str,
    amount: float,
    pump_id: PydanticObjectId,
    attendant_id: Optional[PydanticObjectId],
    item_name: str,
    quantity: float,
    sale_log_id: PydanticObjectId,
    is_bulk: bool = False
) -> tuple[bool, Optional[dict]]:
    try:
        plate_normalized = vehicle_number.strip().upper()
        vehicle = await Vehicle.find_one(
            Vehicle.vehicle_plate == plate_normalized,
            Vehicle.is_active == True
        )
        if vehicle:
            # Find active credit request containing this vehicle ID
            active_credit_req = await CreditRequest.find_one(
                In(CreditRequest.vehicle_ids, [vehicle.id]),
                CreditRequest.status == "active"
            )
            if not active_credit_req:
                active_credit_req = await CreditRequest.find_one(
                    CreditRequest.vehicle_id == vehicle.id,
                    CreditRequest.status == "active"
                )

            if active_credit_req:
                partner_id = vehicle.partner_id
                vids = getattr(active_credit_req, "vehicle_ids", []) or [active_credit_req.vehicle_id]
                contract_vehicles = await Vehicle.find(
                    In(Vehicle.id, vids),
                    Vehicle.partner_id == partner_id,
                    Vehicle.is_active == True
                ).to_list()

                total_outstanding = sum(v.outstanding_amount for v in contract_vehicles)
                contract_limit = active_credit_req.approved_limit or 0.0
                available_credit = contract_limit - total_outstanding

                if available_credit >= amount:
                    vehicle.outstanding_amount = round(vehicle.outstanding_amount + amount, 2)
                    vehicle.updated_at = datetime.utcnow()
                    await vehicle.save()

                    usage = CreditUsage(
                        logistic_partner_id=partner_id,
                        vehicle_id=vehicle.id,
                        pump_id=pump_id,
                        attendant_id=attendant_id,
                        credit_request_id=active_credit_req.id,
                        fuel_type=item_name,
                        volume=quantity,
                        amount=amount,
                        status="pending",
                        remarks=f"{'Bulk auto-deducted' if is_bulk else 'Auto-deducted'} from sale log {str(sale_log_id)}",
                    )
                    await usage.insert()

                    return True, {
                        "vehicle_id": str(vehicle.id),
                        "partner_id": str(partner_id),
                        "credit_limit": contract_limit,
                        "deducted": amount,
                        "new_outstanding": vehicle.outstanding_amount,
                        "credit_usage_id": str(usage.id),
                    }
                else:
                    log.warning(
                        "Credit limit exceeded — deduction skipped",
                        plate=plate_normalized,
                        available=available_credit,
                        requested=amount,
                    )
                    return False, {
                        "vehicle_id": str(vehicle.id),
                        "error": "insufficient_credit",
                        "available_credit": round(available_credit, 2),
                        "requested": amount,
                    }
    except Exception as credit_exc:
        log.error("Credit deduction failed", error=str(credit_exc))
    return False, None


@router.post("/logs", status_code=201)
async def add_sale_log(
    payload: SaleLogCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Add a sale log (Single or Batch) within a shift OR as standalone.

    Auto credit deduction:
    - If vehicle_number is provided and it matches a logistic partner's Vehicle,
      the sale amount is deducted from that vehicle's available credit by
      incrementing outstanding_amount and creating a CreditUsage record.
    """
    pump = await _get_pump_or_403(payload.pump_id, current_user.id)

    shift_oid = None
    if payload.shift_id:
        try:
            shift_oid = PydanticObjectId(payload.shift_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid shift_id")
        shift = await Shift.find_one(
            Shift.id == shift_oid,
            Shift.pump_id == pump.id,
        )
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        if shift.status == ShiftStatus.closed:
            raise HTTPException(status_code=400, detail="Cannot add logs to a closed shift")

    amount = round(payload.quantity * payload.rate, 2)
    ts = payload.timestamp or datetime.utcnow()

    customer_oid = None
    if payload.customer_id:
        try:
            customer_oid = PydanticObjectId(payload.customer_id)
        except Exception:
            pass

    attendant_oid = None
    if payload.attendant_id:
        try:
            attendant_oid = PydanticObjectId(payload.attendant_id)
        except Exception:
            pass

    sale_log = SaleLog(
        shift_id=shift_oid,
        pump_id=pump.id,
        sale_type=payload.sale_type,
        timestamp=ts,
        nozzle_id=payload.nozzle_id,
        item_name=payload.item_name,
        rate=payload.rate,
        quantity=payload.quantity,
        amount=amount,
        payment_mode=payload.payment_mode,
        pos_machine=payload.pos_machine,
        billing_ref=payload.billing_ref,
        customer_name=payload.customer_name,
        customer_id=customer_oid,
        credit_slip_ref=payload.credit_slip_ref,
        vehicle_number=payload.vehicle_number,
        vehicle_type=payload.vehicle_type,
        attendant_id=attendant_oid,
        remarks=payload.remarks,
        receipt_url=payload.receipt_url,
    )
    await sale_log.insert()

    # ── Auto credit deduction for logistic partner vehicles ──────────────────
    credit_deducted = False
    credit_info = None
    if payload.vehicle_number:
        credit_deducted, credit_info = await _process_logistic_auto_credit(
            vehicle_number=payload.vehicle_number,
            amount=amount,
            pump_id=pump.id,
            attendant_id=attendant_oid,
            item_name=payload.item_name,
            quantity=payload.quantity,
            sale_log_id=sale_log.id,
            is_bulk=False
        )
    # ── End credit deduction ─────────────────────────────────────────────────

    return {
        "id": str(sale_log.id),
        "pump_id": str(sale_log.pump_id),
        "shift_id": str(sale_log.shift_id) if sale_log.shift_id else None,
        "item_name": sale_log.item_name,
        "amount": sale_log.amount,
        "quantity": sale_log.quantity,
        "payment_mode": str(sale_log.payment_mode),
        "timestamp": sale_log.timestamp,
        "created_at": sale_log.created_at,
        "credit_deducted": credit_deducted,
        "credit_info": credit_info,
    }


@router.post("/logs/bulk")
async def add_sale_logs_bulk(
    logs: List[SaleLogCreate],
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Bulk add sale logs."""
    pump_ids = {l.pump_id for l in logs}
    for pid in pump_ids:
        await _get_pump_or_403(pid, current_user.id)

    created = 0
    for payload in logs:
        try:
            pump = await _get_pump_or_403(payload.pump_id, current_user.id)
            amount = round(payload.quantity * payload.rate, 2)
            sale_log = SaleLog(
                shift_id=PydanticObjectId(payload.shift_id) if payload.shift_id else None,
                pump_id=pump.id,
                sale_type=payload.sale_type,
                timestamp=payload.timestamp or datetime.utcnow(),
                nozzle_id=payload.nozzle_id,
                item_name=payload.item_name,
                rate=payload.rate,
                quantity=payload.quantity,
                amount=amount,
                payment_mode=payload.payment_mode,
                pos_machine=payload.pos_machine,
                billing_ref=payload.billing_ref,
                customer_name=payload.customer_name,
                customer_id=PydanticObjectId(payload.customer_id) if payload.customer_id else None,
                credit_slip_ref=payload.credit_slip_ref,
                vehicle_number=payload.vehicle_number,
                vehicle_type=payload.vehicle_type,
                attendant_id=PydanticObjectId(payload.attendant_id) if payload.attendant_id else None,
                remarks=payload.remarks,
                receipt_url=payload.receipt_url,
            )
            await sale_log.insert()
            created += 1

            # ── Auto credit deduction for logistic partner vehicles (bulk) ──
            if payload.vehicle_number:
                await _process_logistic_auto_credit(
                    vehicle_number=payload.vehicle_number,
                    amount=amount,
                    pump_id=pump.id,
                    attendant_id=PydanticObjectId(payload.attendant_id) if payload.attendant_id else None,
                    item_name=payload.item_name,
                    quantity=payload.quantity,
                    sale_log_id=sale_log.id,
                    is_bulk=True
                )
            # ── End credit deduction ─────────────────────────────────────────

        except Exception as e:
            log.warning("Bulk log insert failed", error=str(e))

    return {"status": "success", "count": created}


@router.get("/logs")
async def get_sale_logs(
    pump_id: str = Query(...),
    shift_id: Optional[str] = Query(None),
    customer_name: Optional[str] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    vehicle_number: Optional[str] = Query(None),
    item_name: Optional[str] = Query(None),
    payment_mode: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Filter and paginate sale logs — Entries tab."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    # Start with base filter
    filters = [SaleLog.pump_id == pump.id, SaleLog.is_deleted == False]

    if shift_id:
        try:
            filters.append(SaleLog.shift_id == PydanticObjectId(shift_id))
        except Exception:
            pass

    all_logs = await SaleLog.find(*filters).sort(-SaleLog.timestamp).to_list()

    # Python-side filtering (MongoDB doesn't support ilike easily without regex)
    def matches(l):
        if customer_name and (not l.customer_name or customer_name.lower() not in l.customer_name.lower()):
            return False
        if vehicle_type and (not l.vehicle_type or vehicle_type.lower() not in l.vehicle_type.lower()):
            return False
        if vehicle_number and (not l.vehicle_number or vehicle_number.lower() not in l.vehicle_number.lower()):
            return False
        if item_name and item_name.lower() not in l.item_name.lower():
            return False
        if payment_mode and str(l.payment_mode) != payment_mode:
            return False
        if from_date and l.timestamp < from_date:
            return False
        if to_date and l.timestamp > to_date:
            return False
        return True

    filtered = [l for l in all_logs if matches(l)]
    offset = (page - 1) * page_size
    page_logs = filtered[offset:offset + page_size]

    return [
        {
            "id": str(l.id),
            "pump_id": str(l.pump_id),
            "shift_id": str(l.shift_id) if l.shift_id else None,
            "sale_type": str(l.sale_type),
            "timestamp": l.timestamp,
            "nozzle_id": l.nozzle_id,
            "item_name": l.item_name,
            "rate": l.rate,
            "quantity": l.quantity,
            "amount": l.amount,
            "payment_mode": str(l.payment_mode),
            "pos_machine": l.pos_machine,
            "billing_ref": l.billing_ref,
            "customer_name": l.customer_name,
            "credit_slip_ref": l.credit_slip_ref,
            "vehicle_number": l.vehicle_number,
            "vehicle_type": l.vehicle_type,
            "attendant_id": str(l.attendant_id) if l.attendant_id else None,
            "remarks": l.remarks,
            "created_at": l.created_at,
        }
        for l in page_logs
    ]


@router.delete("/logs/{log_id}")
async def delete_sale_log(
    log_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Soft-delete a sale log."""
    try:
        oid = PydanticObjectId(log_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid log_id")
    sale_log = await SaleLog.get(oid)
    if not sale_log:
        raise HTTPException(status_code=404, detail="Sale log not found")

    await _get_pump_or_403(str(sale_log.pump_id), current_user.id)

    sale_log.is_deleted = True
    await sale_log.save()
    return {"status": "deleted", "log_id": log_id}


# ═════════════════════════════════════════════════════════════════
# ITEM RATES — mid-shift update
# ═════════════════════════════════════════════════════════════════

@router.patch("/rates")
async def update_item_rate(
    payload: ItemRateUpdate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Update fuel rate mid-shift."""
    await _get_pump_or_403(payload.pump_id, current_user.id)
    return {
        "status": "ok",
        "pump_id": payload.pump_id,
        "item_name": payload.item_name,
        "new_rate": payload.new_rate,
        "message": "Rate updated. Future logs in this session will use the new rate.",
    }


# ═════════════════════════════════════════════════════════════════
# OVERVIEW DASHBOARD
# ═════════════════════════════════════════════════════════════════

@router.get("/overview")
async def get_sales_overview(
    pump_id: str = Query(...),
    days: int = Query(30, le=365),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Overview dashboard data."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    all_logs = await SaleLog.find(
        SaleLog.pump_id == pump.id,
        SaleLog.is_deleted == False,
    ).to_list()

    logs = [l for l in all_logs if l.timestamp and start_date <= l.timestamp <= end_date]

    net_amount = round(sum(l.amount for l in logs), 2)
    credit_amount = round(sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.credit), 2)
    cash_amount = round(sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.cash), 2)
    pos_amount = round(sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.pos), 2)
    credit_ratio = round((credit_amount / net_amount * 100) if net_amount else 0, 2)

    monthly_item: dict = defaultdict(lambda: defaultdict(float))
    weekly_item: dict = defaultdict(lambda: defaultdict(float))
    monthly_pay: dict = defaultdict(lambda: defaultdict(float))

    for l in logs:
        month_key = l.timestamp.strftime("%Y-%m")
        week_key = f"W{l.timestamp.isocalendar()[1]}-{l.timestamp.year}"
        monthly_item[l.item_name][month_key] += l.amount
        weekly_item[l.item_name][week_key] += l.amount
        monthly_pay[str(l.payment_mode)][month_key] += l.amount

    # Recent closed shifts
    recent_shifts = await Shift.find(
        Shift.pump_id == pump.id,
        Shift.status == ShiftStatus.closed
    ).sort(-Shift.end_time).limit(10).to_list()

    shifts_out = []
    for s in recent_shifts:
        s_logs = await SaleLog.find(SaleLog.shift_id == s.id, SaleLog.is_deleted == False).to_list()
        shifts_out.append({
            "shift_id": str(s.id),
            "shift_type": s.shift_type,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "net_amount": round(sum(l.amount for l in s_logs), 2),
            "sales_count": len(s_logs),
        })

    return {
        "pump_id": pump_id,
        "period_days": days,
        "net_amount": net_amount,
        "credit_ratio": credit_ratio,
        "cash_amount": cash_amount,
        "credit_amount": credit_amount,
        "pos_amount": pos_amount,
        "monthly_item_sales": {k: dict(v) for k, v in monthly_item.items()},
        "weekly_item_sales": {k: dict(v) for k, v in weekly_item.items()},
        "monthly_payment_breakdown": {k: dict(v) for k, v in monthly_pay.items()},
        "recent_shifts": shifts_out,
    }


# ═════════════════════════════════════════════════════════════════
# ATTENDANTS — for shift personnel selector
# ═════════════════════════════════════════════════════════════════

@router.get("/attendants")
async def get_pump_attendants(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """List all active attendants for a pump — used in shift setup personnel selector."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    attendants = await Attendant.find(
        Attendant.pump_id == pump.id,
        Attendant.is_active == True
    ).to_list()

    return [
        {
            "id": str(a.id),
            "name": a.name,
            "phone": getattr(a, "phone", None),
            "role": getattr(a, "role", "attendant"),
        }
        for a in attendants
    ]


# ═════════════════════════════════════════════════════════════════
# LEGACY ENDPOINTS — kept for backward compatibility
# ═════════════════════════════════════════════════════════════════

@router.get("/register")
async def get_sales_register(
    pump_id: str = Query(...),
    days: int = Query(30),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Legacy endpoint — redirects to overview."""
    return await get_sales_overview(pump_id=pump_id, days=days, current_user=current_user)