# src/core/feature_gate.py
# Feature Gate — Har protected route pe laga do
# Usage: Depends(require_feature("inventory"))
# Fully async — uses Beanie (MongoDB ODM)

from fastapi import Depends, HTTPException, status, Query
from datetime import datetime

from src.core.dependencies import get_current_active_user
from src.db.models.user import User
from src.db.models.subscription import PumpSubscription, SubscriptionPlan
from beanie import PydanticObjectId

# ── Feature name → DB field mapping ─────────────────────────
FEATURE_MAP = {
    "sales":            "feature_sales",
    "inventory":        "feature_inventory",
    "crm":              "feature_crm",
    "udhaar":           "feature_udhaar",
    "analytics":        "feature_analytics",
    "accounting":       "feature_accounting",
    "logistic":         "feature_logistic",
    "multi_pump":       "feature_multi_pump",
    "api_access":       "feature_api_access",
    "priority_support": "feature_priority_support",
}


async def get_pump_subscription(pump_id: PydanticObjectId) -> tuple[PumpSubscription | None, SubscriptionPlan | None]:
    """Returns active/trial subscription + plan for a pump."""
    sub = await PumpSubscription.find_one(
        PumpSubscription.pump_id == pump_id,
        {"status": {"$in": ["active", "trial"]}},
    )

    if not sub:
        return None, None

    # Check trial expiry
    if sub.status == "trial" and sub.trial_end:
        if datetime.utcnow() > sub.trial_end:
            sub.status = "expired"
            await sub.save()
            return None, None

    # Check subscription expiry
    if sub.end_date and datetime.utcnow() > sub.end_date:
        sub.status = "expired"
        await sub.save()
        return None, None

    plan = await SubscriptionPlan.get(sub.plan_id)
    return sub, plan


def require_feature(feature_name: str):
    """
    FastAPI dependency — blocks route if pump doesn't have this feature.

    Usage in any route:
        @router.get("/inventory")
        async def get_inventory(
            pump_id: str = Query(...),
            _: None = Depends(require_feature("inventory")),
            ...
        ):

    Returns 403 with clear message if feature is locked.
    """
    async def checker(
        pump_id: str  = Query(..., description="Pump ID"),
        user:    User = Depends(get_current_active_user),
    ):
        if feature_name not in FEATURE_MAP:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unknown feature: '{feature_name}'",
            )

        try:
            oid = PydanticObjectId(pump_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pump_id format")

        sub, plan = await get_pump_subscription(oid)

        if not sub or not plan:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code":    "NO_SUBSCRIPTION",
                    "feature": feature_name,
                    "message": "No active subscription. Please contact admin or upgrade your plan.",
                    "locked":  True,
                }
            )

        col_name    = FEATURE_MAP[feature_name]
        has_feature = getattr(plan, col_name, False)

        if not has_feature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code":      "FEATURE_LOCKED",
                    "feature":   feature_name,
                    "plan":      plan.name,
                    "message":   f"'{feature_name}' is not available in your '{plan.name}' plan. Upgrade to unlock.",
                    "locked":    True,
                }
            )

        return user  # Pass through if feature allowed

    return checker