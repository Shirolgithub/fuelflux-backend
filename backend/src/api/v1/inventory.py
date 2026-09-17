"""
Inventory management module for fuel stations.
Fully migrated to async Beanie (MongoDB ODM).

Covers: Item Groups, Stock Items, Stock Purchases, Stock Adjustments, Stock Summary

Business Rules implemented:
- Loss-type adjustments (damage/loss/theft/expired) → quantity stored as NEGATIVE
- Purchase LOCK → triggers inventory recalculation (qty, avg_rate, valuation)
- Items inherit tax settings from Group (auto-fill on group_id select)
- Dispensed items (bulk fuel) require fuel sample data on purchase
- Closing qty = Opening + Purchase - Sales + Adjustments
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from beanie import PydanticObjectId

from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.core.feature_gate import require_feature

from src.db.models.inventory import (
    ItemGroup, StockItem, StockPurchase, StockPurchaseLine,
    StockAdjustment, AdjustmentReason
)
from src.db.models.sales import SaleLog
from src.db.schemas.inventory import (
    ItemGroupCreate, ItemGroupUpdate, ItemGroupResponse,
    StockItemCreate, StockItemUpdate, StockItemResponse, SetRateRequest,
    StockPurchaseCreate, StockPurchaseResponse,
    StockAdjustmentCreate, StockAdjustmentResponse,
    StockSummaryResponse, StockSummaryRow,
    PurchaseLineResponse,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])

# ═══════════════════════════════════════════════════════════════════════════════
# ── HELPERS ──────────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

LOSS_REASONS = {AdjustmentReason.damage, AdjustmentReason.loss,
                AdjustmentReason.theft, AdjustmentReason.expired}


async def _verify_pump_owner(pump_id_str: str, user: User) -> Pump:
    """Ensure pump exists and belongs to current user."""
    try:
        oid = PydanticObjectId(str(pump_id_str))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")
    pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == user.id)
    if not pump:
        raise HTTPException(status_code=403, detail="Pump not found or access denied")
    return pump


async def _recalculate_item_inventory(item: StockItem) -> None:
    """
    Recalculates: current_quantity, average_rate, current_valuation.
    Called after a purchase is locked or an adjustment is saved.
    """
    # Sum all LOCKED purchase quantities for this item
    purchase_lines = await StockPurchaseLine.find(
        StockPurchaseLine.item_id == item.id
    ).to_list()

    locked_purchases_ids = set()
    for line in purchase_lines:
        purchase = await StockPurchase.get(line.purchase_id)
        if purchase and purchase.is_locked:
            locked_purchases_ids.add(line.purchase_id)

    total_purchase_qty = 0.0
    total_purchase_value = 0.0
    for line in purchase_lines:
        if line.purchase_id in locked_purchases_ids:
            total_purchase_qty += line.quantity
            total_purchase_value += line.after_tax_amount

    # Sum all adjustments
    adjustments = await StockAdjustment.find(
        StockAdjustment.item_id == item.id
    ).to_list()
    adj_qty = sum(a.quantity for a in adjustments)

    # Sum all sales matched by item name
    all_sales = await SaleLog.find(
        SaleLog.pump_id == item.pump_id,
        SaleLog.is_deleted == False,
    ).to_list()
    sales_qty = sum(
        s.quantity for s in all_sales
        if s.item_name and s.item_name.lower() == item.name.lower()
    )

    new_quantity = total_purchase_qty + adj_qty - sales_qty
    new_avg_rate = (
        total_purchase_value / total_purchase_qty
        if total_purchase_qty > 0 else item.average_rate
    )

    item.current_quantity = max(0.0, new_quantity)
    item.average_rate = round(new_avg_rate, 4)
    item.current_valuation = round(item.current_quantity * new_avg_rate, 2)
    item.updated_at = datetime.utcnow()
    await item.save()


async def _build_item_response(item: StockItem) -> dict:
    """Build item dict with group_name included."""
    group = await ItemGroup.get(item.group_id) if item.group_id else None
    return {
        "id": str(item.id),
        "pump_id": str(item.pump_id),
        "group_id": str(item.group_id) if item.group_id else None,
        "group_name": group.name if group else None,
        "name": item.name,
        "item_code": item.item_code,
        "hsn_code": item.hsn_code,
        "category": item.category,
        "unit": item.unit,
        "is_dispensed_item": item.is_dispensed_item,
        "package_details": item.package_details,
        "current_quantity": item.current_quantity,
        "average_rate": item.average_rate,
        "current_valuation": item.current_valuation,
        "selling_rate": item.selling_rate,
        "override_tax": item.override_tax,
        "vat_rate": item.vat_rate,
        "surcharge_rate": item.surcharge_rate,
        "cess_rate": item.cess_rate,
        "is_active": item.is_active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


async def _build_purchase_response(purchase: StockPurchase) -> dict:
    """Build purchase dict with enriched line items."""
    lines = await StockPurchaseLine.find(
        StockPurchaseLine.purchase_id == purchase.id
    ).to_list()
    line_data = []
    for line in lines:
        item = await StockItem.get(line.item_id)
        line_data.append({
            "id": str(line.id),
            "purchase_id": str(line.purchase_id),
            "item_id": str(line.item_id),
            "item_name": item.name if item else None,
            "quantity": line.quantity,
            "basic_rate": line.basic_rate,
            "rebate": line.rebate,
            "vat_amount": line.vat_amount,
            "surcharge_amount": line.surcharge_amount,
            "cess_amount": line.cess_amount,
            "license_fees": line.license_fees,
            "dealer_commission": line.dealer_commission,
            "after_tax_amount": line.after_tax_amount,
        })
    return {
        "id": str(purchase.id),
        "pump_id": str(purchase.pump_id),
        "supplier_name": purchase.supplier_name,
        "invoice_number": purchase.invoice_number,
        "invoice_date": purchase.invoice_date,
        "purchase_timestamp": purchase.purchase_timestamp,
        "is_locked": purchase.is_locked,
        "locked_at": purchase.locked_at,
        "sample_ref_number": purchase.sample_ref_number,
        "sample_quantity": purchase.sample_quantity,
        "challan_density_15c": purchase.challan_density_15c,
        "observed_density": purchase.observed_density,
        "tanker_seal_numbers": purchase.tanker_seal_numbers,
        "vehicle_number": purchase.vehicle_number,
        "transporter_name": purchase.transporter_name,
        "transporter_phone": purchase.transporter_phone,
        "driver_name": purchase.driver_name,
        "driver_license": purchase.driver_license,
        "delivery_comments": purchase.delivery_comments,
        "toll_amount": purchase.toll_amount,
        "toll_receipt_url": purchase.toll_receipt_url,
        "extra_charges": purchase.extra_charges,
        "extra_charges_note": purchase.extra_charges_note,
        "created_at": purchase.created_at,
        "line_items": line_data,
    }


async def _build_adjustment_response(adj: StockAdjustment) -> dict:
    item = await StockItem.get(adj.item_id)
    return {
        "id": str(adj.id),
        "pump_id": str(adj.pump_id),
        "item_id": str(adj.item_id),
        "item_name": item.name if item else None,
        "reason": adj.reason,
        "quantity": adj.quantity,
        "notes": adj.notes,
        "created_at": adj.created_at,
        "created_by": str(adj.created_by) if adj.created_by else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ── MODULE 1 : ITEM GROUPS ───────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/groups", status_code=201)
async def create_item_group(
    payload: ItemGroupCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Create a new item group (e.g. 'Dispensing Products')."""
    pump = await _verify_pump_owner(str(payload.pump_id), current_user)

    group = ItemGroup(
        pump_id=pump.id,
        name=payload.name,
        item_class=payload.item_class,
        category=payload.category,
        description=payload.description,
        hsn_code=payload.hsn_code,
        tax_type=payload.tax_type,
        rate_tax_type=payload.rate_tax_type,
        valuation_method=payload.valuation_method,
        vat_rate=payload.vat_rate,
        surcharge_rate=payload.surcharge_rate,
        cess_rate=payload.cess_rate,
        additional_cess_rate=payload.additional_cess_rate,
        sales_account=payload.sales_account,
        purchase_account=payload.purchase_account,
        commission_account=payload.commission_account,
        license_fee_account=payload.license_fee_account,
        rebate_account=payload.rebate_account,
    )
    await group.insert()
    return {
        "id": str(group.id),
        "pump_id": str(group.pump_id),
        "name": group.name,
        "item_class": group.item_class,
        "category": group.category,
        "tax_type": group.tax_type,
        "vat_rate": group.vat_rate,
        "surcharge_rate": group.surcharge_rate,
        "cess_rate": group.cess_rate,
        "is_active": group.is_active,
        "created_at": group.created_at,
    }


@router.get("/groups")
async def list_item_groups(
    pump_id: str = Query(...),
    current_user: User = Depends(get_current_active_user)
):
    """List all active item groups for a pump."""
    try:
        pid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    groups = await ItemGroup.find(
        ItemGroup.pump_id == pid,
        ItemGroup.is_active == True,
    ).sort(ItemGroup.name).to_list()

    return [
        {
            "id": str(g.id),
            "pump_id": str(g.pump_id),
            "name": g.name,
            "item_class": g.item_class,
            "category": g.category,
            "description": g.description,
            "tax_type": g.tax_type,
            "vat_rate": g.vat_rate,
            "surcharge_rate": g.surcharge_rate,
            "cess_rate": g.cess_rate,
            "is_active": g.is_active,
            "created_at": g.created_at,
        }
        for g in groups
    ]


@router.get("/groups/{group_id}")
async def get_item_group(
    group_id: str,
    current_user: User = Depends(get_current_active_user)
):
    try:
        gid = PydanticObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid group_id")

    group = await ItemGroup.get(gid)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await _verify_pump_owner(str(group.pump_id), current_user)
    return {
        "id": str(group.id),
        "pump_id": str(group.pump_id),
        "name": group.name,
        "item_class": group.item_class,
        "category": group.category,
        "description": group.description,
        "tax_type": group.tax_type,
        "vat_rate": group.vat_rate,
        "surcharge_rate": group.surcharge_rate,
        "cess_rate": group.cess_rate,
        "additional_cess_rate": group.additional_cess_rate,
        "sales_account": group.sales_account,
        "purchase_account": group.purchase_account,
        "commission_account": group.commission_account,
        "license_fee_account": group.license_fee_account,
        "rebate_account": group.rebate_account,
        "is_active": group.is_active,
        "created_at": group.created_at,
    }


@router.patch("/groups/{group_id}")
async def update_item_group(
    group_id: str,
    payload: ItemGroupUpdate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Update group tax settings."""
    try:
        gid = PydanticObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid group_id")

    group = await ItemGroup.get(gid)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await _verify_pump_owner(str(group.pump_id), current_user)

    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, val)
    group.updated_at = datetime.utcnow()
    await group.save()

    return {"id": str(group.id), "name": group.name, "updated_at": group.updated_at}


@router.delete("/groups/{group_id}", status_code=204)
async def delete_item_group(
    group_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Soft delete — preserves historical data."""
    try:
        gid = PydanticObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid group_id")

    group = await ItemGroup.get(gid)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    await _verify_pump_owner(str(group.pump_id), current_user)

    active_item = await StockItem.find_one(
        StockItem.group_id == group.id,
        StockItem.is_active == True,
    )
    if active_item:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete group '{group.name}' — it has active stock items. Reassign or deactivate items first."
        )

    group.is_active = False
    await group.save()


# ═══════════════════════════════════════════════════════════════════════════════
# ── MODULE 2 : STOCK ITEMS ───────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/items", status_code=201)
async def create_stock_item(
    payload: StockItemCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Create a stock item (e.g. 'Petrol', code='MS', unit='Liters')."""
    pump = await _verify_pump_owner(str(payload.pump_id), current_user)

    # Validate group belongs to same pump
    group_oid = None
    if payload.group_id:
        try:
            group_oid = PydanticObjectId(str(payload.group_id))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid group_id")
        group = await ItemGroup.find_one(
            ItemGroup.id == group_oid,
            ItemGroup.pump_id == pump.id,
            ItemGroup.is_active == True,
        )
        if not group:
            raise HTTPException(status_code=404, detail="Item group not found for this pump")

    # Check duplicate
    duplicate = await StockItem.find_one(
        StockItem.pump_id == pump.id,
        StockItem.name == payload.name.strip(),
        StockItem.is_active == True,
    )
    if duplicate:
        raise HTTPException(status_code=400, detail=f"Item '{payload.name}' already exists for this pump")

    item = StockItem(
        pump_id=pump.id,
        group_id=group_oid,
        name=payload.name.strip(),
        item_code=payload.item_code,
        hsn_code=payload.hsn_code,
        category=payload.category,
        unit=payload.unit,
        is_dispensed_item=payload.is_dispensed_item,
        package_details=payload.package_details,
        current_quantity=payload.current_quantity or 0.0,
        average_rate=payload.average_rate or 0.0,
        selling_rate=payload.selling_rate or 0.0,
        override_tax=payload.override_tax or False,
        vat_rate=payload.vat_rate,
        surcharge_rate=payload.surcharge_rate,
        cess_rate=payload.cess_rate,
    )
    item.current_valuation = round((item.current_quantity or 0.0) * (item.average_rate or 0.0), 2)
    await item.insert()

    # Create opening balance adjustment if qty > 0
    if item.current_quantity > 0.0:
        adj = StockAdjustment(
            pump_id=pump.id,
            item_id=item.id,
            reason=AdjustmentReason.other,
            quantity=item.current_quantity,
            notes="Opening Balance",
            created_by=current_user.id,
        )
        await adj.insert()

    return await _build_item_response(item)


@router.get("/items")
async def list_stock_items(
    pump_id: str = Query(...),
    group_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    _: None = Depends(require_feature("inventory")),
    current_user: User = Depends(get_current_active_user)
):
    """List stock items for a pump. Optional filter: group_id, category."""
    try:
        pid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    filters = [StockItem.pump_id == pid, StockItem.is_active == True]

    if group_id:
        try:
            filters.append(StockItem.group_id == PydanticObjectId(group_id))
        except Exception:
            pass

    if category:
        filters.append(StockItem.category == category)

    items = await StockItem.find(*filters).sort(StockItem.name).to_list()
    return [await _build_item_response(item) for item in items]


@router.patch("/items/set-rates")
async def set_item_rates(
    payload: List[SetRateRequest],
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Bulk rate update."""
    updated = []
    for rate_req in payload:
        try:
            iid = PydanticObjectId(str(rate_req.item_id))
        except Exception:
            continue
        item = await StockItem.get(iid)
        if not item:
            continue
        await _verify_pump_owner(str(item.pump_id), current_user)
        item.selling_rate = rate_req.selling_rate
        item.updated_at = datetime.utcnow()
        await item.save()
        updated.append(item)

    return [await _build_item_response(item) for item in updated]


@router.get("/items/{item_id}")
async def get_stock_item(
    item_id: str,
    current_user: User = Depends(get_current_active_user),
):
    try:
        iid = PydanticObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item_id")

    item = await StockItem.find_one(StockItem.id == iid, StockItem.is_active == True)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await _verify_pump_owner(str(item.pump_id), current_user)
    return await _build_item_response(item)


@router.patch("/items/{item_id}")
async def update_stock_item(
    item_id: str,
    payload: StockItemUpdate,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    try:
        iid = PydanticObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item_id")

    item = await StockItem.get(iid)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await _verify_pump_owner(str(item.pump_id), current_user)

    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, val)
    item.updated_at = datetime.utcnow()
    await item.save()
    return await _build_item_response(item)


@router.get("/items/{item_id}/tax-settings")
async def get_item_effective_tax(
    item_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """Returns effective tax settings regardless of whether group_id is set."""
    try:
        iid = PydanticObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item_id")

    item = await StockItem.get(iid)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await _verify_pump_owner(str(item.pump_id), current_user)

    if item.override_tax:
        return {
            "source": "item",
            "vat_rate": item.vat_rate or 0.0,
            "surcharge_rate": item.surcharge_rate or 0.0,
            "cess_rate": item.cess_rate or 0.0,
        }

    if not item.group_id:
        return {"source": "none", "vat_rate": 0.0, "surcharge_rate": 0.0, "cess_rate": 0.0}

    group = await ItemGroup.get(item.group_id)
    if not group:
        return {"source": "none", "vat_rate": 0.0, "surcharge_rate": 0.0, "cess_rate": 0.0}

    return {
        "source": "group",
        "group_name": group.name,
        "tax_type": group.tax_type,
        "vat_rate": group.vat_rate,
        "surcharge_rate": group.surcharge_rate,
        "cess_rate": group.cess_rate,
        "additional_cess_rate": group.additional_cess_rate,
        "sales_account": group.sales_account,
        "purchase_account": group.purchase_account,
        "commission_account": group.commission_account,
        "license_fee_account": group.license_fee_account,
        "rebate_account": group.rebate_account,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ── MODULE 3 : STOCK PURCHASES ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/purchases", status_code=201)
async def create_stock_purchase(
    payload: StockPurchaseCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Create a draft purchase record (multi-step form).
    Draft state: is_locked=False → inventory NOT yet updated.
    """
    pump = await _verify_pump_owner(str(payload.pump_id), current_user)

    # Validate all line item_ids exist and belong to this pump
    for line in payload.line_items:
        item = await StockItem.find_one(
            StockItem.id == PydanticObjectId(str(line.item_id)),
            StockItem.pump_id == pump.id,
            StockItem.is_active == True,
        )
        if not item:
            raise HTTPException(
                status_code=404,
                detail=f"Stock item id={line.item_id} not found for this pump"
            )

    # Build purchase header
    purchase = StockPurchase(
        pump_id=pump.id,
        supplier_name=payload.supplier_name,
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        purchase_timestamp=payload.purchase_timestamp or datetime.utcnow(),
        sample_ref_number=payload.sample_ref_number,
        sample_quantity=payload.sample_quantity,
        challan_density_15c=payload.challan_density_15c,
        observed_density=payload.observed_density,
        tanker_seal_numbers=payload.tanker_seal_numbers,
        vehicle_number=payload.vehicle_number,
        transporter_name=payload.transporter_name,
        transporter_phone=payload.transporter_phone,
        driver_name=payload.driver_name,
        driver_license=payload.driver_license,
        delivery_comments=payload.delivery_comments,
        toll_amount=payload.toll_amount or 0.0,
        toll_receipt_url=payload.toll_receipt_url,
        extra_charges=payload.extra_charges or 0.0,
        extra_charges_note=payload.extra_charges_note,
    )
    await purchase.insert()

    # Build line items
    for line in payload.line_items:
        after_tax = round(
            (line.basic_rate - line.rebate + line.vat_amount + line.surcharge_amount + line.cess_amount)
            * line.quantity,
            2,
        )
        db_line = StockPurchaseLine(
            purchase_id=purchase.id,
            item_id=PydanticObjectId(str(line.item_id)),
            quantity=line.quantity,
            basic_rate=line.basic_rate,
            rebate=line.rebate,
            vat_amount=line.vat_amount,
            surcharge_amount=line.surcharge_amount,
            cess_amount=line.cess_amount,
            license_fees=line.license_fees,
            dealer_commission=line.dealer_commission,
            after_tax_amount=after_tax,
        )
        await db_line.insert()

    return await _build_purchase_response(purchase)


@router.post("/purchases/{purchase_id}/lock")
async def lock_stock_purchase(
    purchase_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    LOCK a purchase.
    On lock:
      1. is_locked = True
      2. For each line item → recalculate qty, avg_rate, valuation
    Once locked, purchase cannot be edited (business rule).
    """
    try:
        pid = PydanticObjectId(purchase_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid purchase_id")

    purchase = await StockPurchase.get(pid)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    if purchase.is_locked:
        raise HTTPException(status_code=400, detail="Purchase is already locked")

    await _verify_pump_owner(str(purchase.pump_id), current_user)

    # Lock the purchase
    purchase.is_locked = True
    purchase.locked_at = datetime.utcnow()
    purchase.updated_at = datetime.utcnow()
    await purchase.save()

    # Trigger inventory recalculation for each affected item
    lines = await StockPurchaseLine.find(
        StockPurchaseLine.purchase_id == purchase.id
    ).to_list()
    item_ids = {line.item_id for line in lines}
    for item_id in item_ids:
        item = await StockItem.get(item_id)
        if item:
            await _recalculate_item_inventory(item)

    return await _build_purchase_response(purchase)


@router.get("/purchases")
async def list_stock_purchases(
    pump_id: str = Query(...),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    locked_only: bool = Query(False),
    current_user: User = Depends(get_current_active_user)
):
    """List purchases with optional date and lock-status filters."""
    try:
        pid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    purchases = await StockPurchase.find(
        StockPurchase.pump_id == pid
    ).sort(-StockPurchase.purchase_timestamp).to_list()

    # Python-side filtering for dates and locked status
    result = []
    for p in purchases:
        if locked_only and not p.is_locked:
            continue
        if date_from:
            dt = datetime.strptime(date_from, "%Y-%m-%d")
            if p.purchase_timestamp < dt:
                continue
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59)
            if p.purchase_timestamp > dt:
                continue
        result.append(await _build_purchase_response(p))

    return result


@router.get("/purchases/{purchase_id}")
async def get_stock_purchase(
    purchase_id: str,
    current_user: User = Depends(get_current_active_user)
):
    try:
        pid = PydanticObjectId(purchase_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid purchase_id")

    purchase = await StockPurchase.get(pid)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    await _verify_pump_owner(str(purchase.pump_id), current_user)
    return await _build_purchase_response(purchase)


@router.delete("/purchases/{purchase_id}", status_code=204)
async def delete_stock_purchase(
    purchase_id: str,
    current_user: User = Depends(require_role(["pump_owner"])),
):
    try:
        pid = PydanticObjectId(purchase_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid purchase_id")

    purchase = await StockPurchase.get(pid)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    if purchase.is_locked:
        raise HTTPException(status_code=400, detail="Locked purchases cannot be deleted")
    await _verify_pump_owner(str(purchase.pump_id), current_user)
    await purchase.delete()


# ═══════════════════════════════════════════════════════════════════════════════
# ── MODULE 4 : STOCK ADJUSTMENTS ────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/adjustments", status_code=201)
async def create_stock_adjustment(
    payload: StockAdjustmentCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Create a stock adjustment.
    KEY BUSINESS RULE: User enters positive qty.
    System automatically converts to negative for loss-type reasons.
    reason=damage/loss/theft/expired → stored as -qty
    reason=other → stored as +qty (addition)
    """
    pump = await _verify_pump_owner(str(payload.pump_id), current_user)

    try:
        iid = PydanticObjectId(str(payload.item_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item_id")

    item = await StockItem.find_one(
        StockItem.id == iid,
        StockItem.pump_id == pump.id,
        StockItem.is_active == True,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Stock item not found for this pump")

    # Business rule: loss reasons → negate quantity
    stored_qty = -abs(payload.quantity) if payload.reason in LOSS_REASONS else abs(payload.quantity)

    if stored_qty < 0 and abs(stored_qty) > item.current_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot adjust {abs(stored_qty)} {item.unit} — only {item.current_quantity} {item.unit} available in stock"
        )

    adjustment = StockAdjustment(
        pump_id=pump.id,
        item_id=item.id,
        reason=payload.reason,
        quantity=stored_qty,
        notes=payload.notes,
        created_by=current_user.id,
    )
    await adjustment.insert()

    # Immediately update item inventory
    await _recalculate_item_inventory(item)

    return await _build_adjustment_response(adjustment)


@router.get("/adjustments")
async def list_stock_adjustments(
    pump_id: str = Query(...),
    item_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user)
):
    """List adjustments with optional filters."""
    try:
        pid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    filters = [StockAdjustment.pump_id == pid]

    if item_id:
        try:
            filters.append(StockAdjustment.item_id == PydanticObjectId(item_id))
        except Exception:
            pass

    adjustments = await StockAdjustment.find(*filters).sort(-StockAdjustment.created_at).to_list()

    # Python-side date filtering
    result = []
    for adj in adjustments:
        if date_from:
            dt = datetime.strptime(date_from, "%Y-%m-%d")
            if adj.created_at < dt:
                continue
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59)
            if adj.created_at > dt:
                continue
        result.append(await _build_adjustment_response(adj))

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ── MODULE 5 : STOCK SUMMARY (Reporting) ────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/summary")
async def get_stock_summary(
    pump_id: str = Query(...),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD — Opening balance baseline"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    group_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user)
):
    """
    Aggregated stock summary.

    Columns:
      Opening Qty/Value | Purchase Qty | Sales Qty | Adjustment Qty | Closing Qty/Value

    Formula:
      closing_qty = opening_qty + purchase_qty - sales_qty + adjustment_qty
      closing_value = closing_qty × average_rate
    """
    try:
        pid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    dt_from = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
    dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59) if date_to else None

    # Fetch items
    item_filters = [StockItem.pump_id == pid, StockItem.is_active == True]
    if group_id:
        try:
            item_filters.append(StockItem.group_id == PydanticObjectId(group_id))
        except Exception:
            pass
    if category:
        item_filters.append(StockItem.category == category)

    items = await StockItem.find(*item_filters).sort(StockItem.name).to_list()

    # Prefetch all data to avoid N+1
    all_purchases = await StockPurchase.find(StockPurchase.pump_id == pid).to_list()
    locked_purchase_ids = {p.id for p in all_purchases if p.is_locked}

    all_lines = await StockPurchaseLine.find().to_list()
    all_adjustments = await StockAdjustment.find(StockAdjustment.pump_id == pid).to_list()
    all_sale_logs = await SaleLog.find(SaleLog.pump_id == pid, SaleLog.is_deleted == False).to_list()

    rows = []
    for item in items:
        # ── Purchase Qty (locked, within date range) ──
        purchase_qty = 0.0
        for line in all_lines:
            if str(line.item_id) != str(item.id):
                continue
            if line.purchase_id not in locked_purchase_ids:
                continue
            # Find the purchase
            purchase = next((p for p in all_purchases if str(p.id) == str(line.purchase_id)), None)
            if purchase:
                if dt_from and purchase.purchase_timestamp < dt_from:
                    continue
                if dt_to and purchase.purchase_timestamp > dt_to:
                    continue
            purchase_qty += line.quantity

        # ── Adjustment Qty (sum with sign) ──
        adjustment_qty = 0.0
        for adj in all_adjustments:
            if str(adj.item_id) != str(item.id):
                continue
            if dt_from and adj.created_at < dt_from:
                continue
            if dt_to and adj.created_at > dt_to:
                continue
            adjustment_qty += adj.quantity

        # ── Sales Qty (matched by item_name) ──
        sales_qty = 0.0
        for sale in all_sale_logs:
            if not sale.item_name or sale.item_name.lower() != item.name.lower():
                continue
            if dt_from and sale.timestamp < dt_from:
                continue
            if dt_to and sale.timestamp > dt_to:
                continue
            sales_qty += sale.quantity

        # ── Opening (before date_from) ──
        if dt_from:
            opening_purch = sum(
                line.quantity for line in all_lines
                if str(line.item_id) == str(item.id)
                and line.purchase_id in locked_purchase_ids
                and next((p for p in all_purchases if str(p.id) == str(line.purchase_id) and p.purchase_timestamp < dt_from), None)
            )
            opening_adj = sum(
                adj.quantity for adj in all_adjustments
                if str(adj.item_id) == str(item.id) and adj.created_at < dt_from
            )
            opening_sales = sum(
                sale.quantity for sale in all_sale_logs
                if sale.item_name and sale.item_name.lower() == item.name.lower()
                and sale.timestamp < dt_from
            )
            opening_qty = opening_purch + opening_adj - opening_sales
        else:
            opening_qty = 0.0

        opening_value = round(opening_qty * item.average_rate, 2)
        closing_qty = max(0.0, opening_qty + purchase_qty - sales_qty + adjustment_qty)
        closing_value = round(closing_qty * item.average_rate, 2)

        group = await ItemGroup.get(item.group_id) if item.group_id else None

        rows.append({
            "item_id": str(item.id),
            "item_name": item.name,
            "item_code": item.item_code,
            "unit": item.unit,
            "group_name": group.name if group else None,
            "category": item.category,
            "opening_quantity": round(opening_qty, 3),
            "opening_value": opening_value,
            "purchase_quantity": round(purchase_qty, 3),
            "sales_quantity": round(sales_qty, 3),
            "adjustment_quantity": round(adjustment_qty, 3),
            "closing_quantity": round(closing_qty, 3),
            "closing_value": closing_value,
            "average_rate": item.average_rate,
        })

    total_closing = round(sum(r["closing_value"] for r in rows), 2)

    return {
        "pump_id": pump_id,
        "date_from": date_from,
        "date_to": date_to,
        "group_id": group_id,
        "category": category,
        "items": rows,
        "total_closing_value": total_closing,
    }