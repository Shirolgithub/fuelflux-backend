# src/api/v1/admin_subscriptions.py
# Admin-only Subscription Management API
# All routes here require a valid admin token (scope="admin").
# Mounted at /admin/subscriptions/... so the frontend's simple
# `startsWith('/admin')` token-routing rule works correctly.

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import structlog
from beanie import PydanticObjectId
from beanie.operators import In

from src.db.models.subscription import SubscriptionPlan, PumpSubscription
from src.db.models.pump import Pump
from src.db.models.user import User
from src.api.v1.admin import get_current_admin
from src.api.v1.subscriptions import plan_to_dict  # reuse the same serializer

log = structlog.get_logger()
router = APIRouter(prefix="/admin/subscriptions", tags=["admin-subscriptions"])


# ═══════════════════════════════════════════════════════════════
#  ADMIN — PLAN MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@router.get("/plans")
async def admin_list_plans(
    admin: User = Depends(get_current_admin),
):
    """All subscription plans (active + inactive). Admin-only."""
    plans = await SubscriptionPlan.find_all().sort("price_monthly").to_list()
    return {"success": True, "data": {"plans": [plan_to_dict(p) for p in plans]}}


@router.post("/plans")
async def admin_create_plan(
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Admin creates a new subscription plan.
    """
    features = payload.get("features", {})
    limits   = payload.get("limits", {})

    existing = await SubscriptionPlan.find_one(
        SubscriptionPlan.name == payload.get("name")
    )
    if existing:
        return JSONResponse(status_code=400, content={"success": False, "message": "Plan name already exists."})

    plan = SubscriptionPlan(
        name          = payload.get("name"),
        description   = payload.get("description", ""),
        price_monthly = float(payload.get("price_monthly", 0)),
        price_annual  = float(payload.get("price_annual", 0)) if payload.get("price_annual") else None,
        is_active     = payload.get("is_active", True),
        is_default    = payload.get("is_default", False),
        created_by    = PydanticObjectId(admin.id),

        # Features
        feature_sales            = features.get("sales", True),
        feature_inventory        = features.get("inventory", False),
        feature_crm              = features.get("crm", False),
        feature_udhaar           = features.get("udhaar", False),
        feature_analytics        = features.get("analytics", False),
        feature_accounting       = features.get("accounting", False),
        feature_logistic         = features.get("logistic", False),
        feature_multi_pump       = features.get("multi_pump", False),
        feature_api_access       = features.get("api_access", False),
        feature_priority_support = features.get("priority_support", False),

        # Limits
        max_staff           = int(limits.get("max_staff", 3)),
        max_pumps           = int(limits.get("max_pumps", 1)),
        max_tanks           = int(limits.get("max_tanks", 2)),
        report_history_days = int(limits.get("report_history_days", 30)),
        default_trial_days  = int(payload.get("default_trial_days", 0)),
    )

    # If this is default, unset others
    if plan.is_default:
        await SubscriptionPlan.find(SubscriptionPlan.is_default == True).update({"$set": {"is_default": False}})

    await plan.insert()
    return {"success": True, "message": f"Plan '{plan.name}' created.", "data": {"plan": plan_to_dict(plan)}}


@router.patch("/plans/{plan_id}")
async def admin_update_plan(
    plan_id: str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """Update any field of a subscription plan."""
    plan = await SubscriptionPlan.get(PydanticObjectId(plan_id))
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    features = payload.get("features", {})
    limits   = payload.get("limits", {})

    # Scalar fields
    for field in ["name", "description", "price_monthly", "price_annual", "is_active", "is_default", "default_trial_days"]:
        if field in payload:
            setattr(plan, field, payload[field])

    # Feature flags
    feature_map = {
        "sales": "feature_sales", "inventory": "feature_inventory",
        "crm": "feature_crm", "udhaar": "feature_udhaar",
        "analytics": "feature_analytics", "accounting": "feature_accounting",
        "logistic": "feature_logistic", "multi_pump": "feature_multi_pump",
        "api_access": "feature_api_access", "priority_support": "feature_priority_support",
    }
    for key, col in feature_map.items():
        if key in features:
            setattr(plan, col, features[key])

    # Limits
    for field in ["max_staff", "max_pumps", "max_tanks", "report_history_days"]:
        if field in limits:
            setattr(plan, field, limits[field])

    if payload.get("is_default"):
        await SubscriptionPlan.find(
            SubscriptionPlan.id != plan.id, SubscriptionPlan.is_default == True
        ).update({"$set": {"is_default": False}})

    plan.updated_at = datetime.utcnow()
    await plan.save()
    return {"success": True, "message": "Plan updated.", "data": {"plan": plan_to_dict(plan)}}


@router.delete("/plans/{plan_id}")
async def admin_delete_plan(
    plan_id: str,
    admin:   User = Depends(get_current_admin),
):
    """Soft delete — marks plan inactive."""
    plan = await SubscriptionPlan.get(PydanticObjectId(plan_id))
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    plan.is_active = False
    await plan.save()
    return {"success": True, "message": f"Plan '{plan.name}' deactivated."}


# ═══════════════════════════════════════════════════════════════
#  ADMIN — PUMP SUBSCRIPTION MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@router.get("/pump-subscriptions")
async def admin_list_pump_subscriptions(
    status: str = Query(None),
    page:   int = Query(1, ge=1),
    limit:  int = Query(20, ge=1, le=100),
    admin:  User = Depends(get_current_admin),
):
    """All pump subscriptions with plan + pump details. Admin-only."""
    find_query = {}
    if status:
        find_query["status"] = status

    query = PumpSubscription.find(find_query)
    total = await query.count()
    subs  = await query.skip((page-1)*limit).limit(limit).to_list()

    # Pre-fetch plans & pumps
    plan_ids = list({s.plan_id for s in subs})
    pump_ids = list({s.pump_id for s in subs})

    plans = await SubscriptionPlan.find(In(SubscriptionPlan.id, plan_ids)).to_list()
    pumps = await Pump.find(In(Pump.id, pump_ids)).to_list()

    plan_map = {p.id: p for p in plans}
    pump_map = {p.id: p for p in pumps}

    response_subs = []
    for s in subs:
        plan = plan_map.get(s.plan_id)
        pump = pump_map.get(s.pump_id)
        response_subs.append({
            "id":            str(s.id),
            "pump_id":       str(s.pump_id),
            "pump_name":     pump.name if pump else "N/A",
            "plan_id":       str(s.plan_id),
            "plan_name":     plan.name if plan else "N/A",
            "billing_cycle": s.billing_cycle,
            "status":        s.status,
            "start_date":    s.start_date.isoformat() if s.start_date else None,
            "end_date":      s.end_date.isoformat() if s.end_date else None,
            "trial_end":     s.trial_end.isoformat() if s.trial_end else None,
            "trial_days_granted": s.trial_days_granted,
            "admin_note":    s.admin_note,
            "features":      plan_to_dict(plan)["features"] if plan else {},
            "limits":        plan_to_dict(plan)["limits"] if plan else {},
        })

    return {
        "success": True,
        "data": {
            "subscriptions": response_subs,
            "total": total, "page": page,
        }
    }


@router.post("/pump-subscriptions/assign")
async def admin_assign_subscription(
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Admin assigns a plan to a pump (with optional trial).
    """
    pump_id    = payload.get("pump_id")
    plan_id    = payload.get("plan_id")
    trial_days = int(payload.get("trial_days", 0))
    note       = payload.get("admin_note", "")
    cycle      = payload.get("billing_cycle", "monthly")

    pump = await Pump.get(PydanticObjectId(pump_id))
    plan = await SubscriptionPlan.get(PydanticObjectId(plan_id))
    if not pump: raise HTTPException(status_code=404, detail="Pump not found.")
    if not plan: raise HTTPException(status_code=404, detail="Plan not found.")

    # Deactivate existing active/trial subscriptions
    await PumpSubscription.find(
        PumpSubscription.pump_id == pump.id,
        In(PumpSubscription.status, ["active", "trial"])
    ).update({"$set": {"status": "cancelled"}})

    now       = datetime.utcnow()
    trial_end = now + timedelta(days=trial_days) if trial_days > 0 else None

    if cycle == "annual":
        end_date = now + timedelta(days=365)
    else:
        end_date = now + timedelta(days=30)

    sub = PumpSubscription(
        pump_id            = pump.id,
        plan_id            = plan.id,
        owner_id           = pump.owner_id,
        billing_cycle      = cycle,
        status             = "trial" if trial_days > 0 else "active",
        start_date         = now,
        end_date           = end_date,
        trial_end          = trial_end,
        trial_days_granted = trial_days,
        admin_note         = note,
        activated_by       = admin.id,
        activated_at       = now,
    )
    await sub.insert()

    # Formatted output
    sub_dict = {
        "id":            str(sub.id),
        "pump_id":       str(sub.pump_id),
        "pump_name":     pump.name,
        "plan_id":       str(sub.plan_id),
        "plan_name":     plan.name,
        "billing_cycle": sub.billing_cycle,
        "status":        sub.status,
        "start_date":    sub.start_date.isoformat() if sub.start_date else None,
        "end_date":      sub.end_date.isoformat() if sub.end_date else None,
        "trial_end":     sub.trial_end.isoformat() if sub.trial_end else None,
        "trial_days_granted": sub.trial_days_granted,
        "admin_note":    sub.admin_note,
        "features":      plan_to_dict(plan)["features"],
        "limits":        plan_to_dict(plan)["limits"],
    }

    return {
        "success": True,
        "message": f"Plan '{plan.name}' assigned to '{pump.name}'." + (f" Trial: {trial_days} days." if trial_days else ""),
        "data": {"subscription": sub_dict},
    }


@router.patch("/pump-subscriptions/{sub_id}/trial")
async def admin_update_trial(
    sub_id:  str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """Extend or set trial for a specific pump."""
    sub = await PumpSubscription.get(PydanticObjectId(sub_id))
    if not sub: raise HTTPException(status_code=404, detail="Subscription not found.")

    trial_days             = int(payload.get("trial_days", 0))
    sub.trial_days_granted = trial_days
    sub.trial_end          = datetime.utcnow() + timedelta(days=trial_days)
    sub.status             = "trial"
    sub.admin_note         = payload.get("admin_note", sub.admin_note)
    sub.updated_at         = datetime.utcnow()
    await sub.save()

    return {"success": True, "message": f"Trial updated to {trial_days} days."}


@router.patch("/pump-subscriptions/{sub_id}/activate")
async def admin_activate_subscription(
    sub_id:  str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """Manually activate a subscription (e.g. after offline payment)."""
    sub = await PumpSubscription.get(PydanticObjectId(sub_id))
    if not sub: raise HTTPException(status_code=404, detail="Subscription not found.")

    cycle    = payload.get("billing_cycle", sub.billing_cycle)
    now      = datetime.utcnow()
    end_date = now + timedelta(days=365 if cycle == "annual" else 30)

    sub.status       = "active"
    sub.start_date   = now
    sub.end_date     = end_date
    sub.activated_by = admin.id
    sub.activated_at = now
    sub.admin_note   = payload.get("admin_note", sub.admin_note)
    sub.updated_at   = datetime.utcnow()
    await sub.save()

    return {"success": True, "message": "Subscription activated."}