# src/api/v1/subscriptions.py
# Public + Pump Owner Subscription API
# (Admin-only routes have moved to src/api/v1/admin_subscriptions.py)

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import structlog
from beanie import PydanticObjectId
from beanie.operators import In
from dotenv import load_dotenv
load_dotenv()

from src.db.models.subscription import SubscriptionPlan, PumpSubscription, SubscriptionPayment
from src.db.models.pump import Pump
from src.db.models.user import User
from src.core.dependencies import get_current_active_user, require_role

log = structlog.get_logger()
router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# ── Helpers ──────────────────────────────────────────────────
def plan_to_dict(p: SubscriptionPlan) -> dict:
    return {
        "id":            str(p.id),
        "name":          p.name,
        "description":   p.description,
        "price_monthly": p.price_monthly,
        "price_annual":  p.price_annual,
        "is_active":     p.is_active,
        "is_default":    p.is_default,
        "features": {
            "sales":            p.feature_sales,
            "inventory":        p.feature_inventory,
            "crm":              p.feature_crm,
            "udhaar":           p.feature_udhaar,
            "analytics":        p.feature_analytics,
            "accounting":       p.feature_accounting,
            "logistic":         p.feature_logistic,
            "multi_pump":       p.feature_multi_pump,
            "api_access":       p.feature_api_access,
            "priority_support": p.feature_priority_support,
        },
        "limits": {
            "max_staff":           p.max_staff,
            "max_pumps":           p.max_pumps,
            "max_tanks":           p.max_tanks,
            "report_history_days": p.report_history_days,
        },
        "default_trial_days": p.default_trial_days,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════
#  PUBLIC — PLANS LIST (for pump owner to see available plans)
# ═══════════════════════════════════════════════════════════════

@router.get("/plans")
async def list_active_plans():
    """Returns all active plans — shown to pump owner on upgrade page."""
    plans = await SubscriptionPlan.find(SubscriptionPlan.is_active == True).sort("price_monthly").to_list()
    return {"success": True, "data": {"plans": [plan_to_dict(p) for p in plans]}}


# ═══════════════════════════════════════════════════════════════
#  PUMP OWNER — MY SUBSCRIPTION
# ═══════════════════════════════════════════════════════════════

@router.get("/my-subscription")
async def get_my_subscription(
    pump_id: str = Query(...),
    user:    User    = Depends(require_role(["pump_owner"])),
):
    """
    Returns current active/trial subscription for a pump.
    """
    sub = await PumpSubscription.find_one(
        PumpSubscription.pump_id  == PydanticObjectId(pump_id),
        PumpSubscription.owner_id == PydanticObjectId(user.id),
        In(PumpSubscription.status, ["active", "trial"]),
    )

    if not sub:
        return {
            "success": True,
            "data": {
                "status":   "no_plan",
                "plan":     None,
                "features": {},
                "message":  "No active subscription. Choose a plan.",
            }
        }

    plan = await SubscriptionPlan.get(sub.plan_id)

    is_trial = sub.status == "trial"
    trial_days_left = None
    if is_trial and sub.trial_end:
        delta = sub.trial_end - datetime.utcnow()
        trial_days_left = max(0, delta.days)
        if trial_days_left == 0:
            sub.status = "expired"
            await sub.save()

    return {
        "success": True,
        "data": {
            "subscription_id":  str(sub.id),
            "status":           sub.status,
            "billing_cycle":    sub.billing_cycle,
            "start_date":       sub.start_date.isoformat() if sub.start_date else None,
            "end_date":         sub.end_date.isoformat() if sub.end_date else None,
            "is_trial":         is_trial,
            "trial_days_left":  trial_days_left,
            "trial_end":        sub.trial_end.isoformat() if sub.trial_end else None,
            "plan":             plan_to_dict(plan) if plan else None,
            "features":         plan_to_dict(plan)["features"] if plan else {},
            "limits":           plan_to_dict(plan)["limits"] if plan else {},
        }
    }


# ═══════════════════════════════════════════════════════════════
#  RAZORPAY — CREATE ORDER + VERIFY PAYMENT
# ═══════════════════════════════════════════════════════════════

@router.post("/create-order")
async def create_razorpay_order(
    payload: dict,
    user:    User    = Depends(require_role(["pump_owner"])),
):
    """
    Creates a Razorpay order for subscription payment.
    """
    try:
        import os, uuid

        pump_id = payload.get("pump_id")
        plan_id = payload.get("plan_id")
        cycle   = payload.get("billing_cycle", "monthly")

        plan = await SubscriptionPlan.get(PydanticObjectId(plan_id))
        if not plan: raise HTTPException(status_code=404, detail="Plan not found.")

        amount = plan.price_annual if (cycle == "annual" and plan.price_annual) else plan.price_monthly
        amount_paise = int(amount * 100)

        key_id     = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")

        is_mock = (key_id == "rzp_test_xxx" or key_secret == "secret_xxx" or not key_id)

        if is_mock:
            mock_order_id = f"mock_order_{uuid.uuid4().hex[:16]}"
            log.info("Razorpay mock order created (dev mode)", order_id=mock_order_id)
            return {
                "success": True,
                "mock":    True,
                "data": {
                    "order_id":  mock_order_id,
                    "amount":    amount_paise,
                    "currency":  "INR",
                    "key_id":    key_id or "mock_key",
                    "plan_name": plan.name,
                }
            }

        import razorpay
        client = razorpay.Client(auth=(key_id, key_secret))
        order  = client.order.create({
            "amount":   amount_paise,
            "currency": "INR",
            "notes": {
                "pump_id":  str(pump_id),
                "plan_id":  str(plan_id),
                "owner_id": str(user.id),
                "cycle":    cycle,
            }
        })

        payment = SubscriptionPayment(
            pump_subscription_id = PydanticObjectId("000000000000000000000000"), # placeholder before verification
            pump_id              = PydanticObjectId(pump_id),
            owner_id             = PydanticObjectId(user.id),
            plan_id              = PydanticObjectId(plan_id),
            amount               = amount,
            billing_cycle        = cycle,
            status               = "pending",
            razorpay_order_id    = order["id"],
        )
        await payment.insert()

        return {
            "success": True,
            "data": {
                "order_id":  order["id"],
                "amount":    amount_paise,
                "currency":  "INR",
                "key_id":    key_id,
                "plan_name": plan.name,
            }
        }

    except ImportError:
        return JSONResponse(status_code=500, content={"success": False, "message": "razorpay package not installed."})
    except HTTPException:
        raise
    except Exception as e:
        log.error("Razorpay order error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-payment")
async def verify_razorpay_payment(
    payload: dict,
    user:    User    = Depends(require_role(["pump_owner"])),
):
    """
    Verifies Razorpay payment signature and activates subscription.
    """
    try:
        import os, hmac, hashlib

        order_id   = payload.get("razorpay_order_id")
        payment_id = payload.get("razorpay_payment_id")
        signature  = payload.get("razorpay_signature")
        pump_id    = payload.get("pump_id")
        plan_id    = payload.get("plan_id")
        cycle      = payload.get("billing_cycle", "monthly")

        # Signature verification
        secret  = os.getenv("RAZORPAY_KEY_SECRET", "secret_xxx").encode()
        msg     = f"{order_id}|{payment_id}".encode()
        gen_sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()

        if gen_sig != signature:
            return JSONResponse(status_code=400, content={"success": False, "message": "Payment signature invalid."})

        # Find the pending payment record
        pay = await SubscriptionPayment.find_one(
            SubscriptionPayment.razorpay_order_id == order_id
        )
        if not pay:
            log.error("Payment record not found for order_id", order_id=order_id)
            return JSONResponse(status_code=404, content={"success": False, "message": "Payment record not found."})

        # Cancel any active/trial subscription for the pump
        await PumpSubscription.find(
            PumpSubscription.pump_id == PydanticObjectId(pump_id),
            In(PumpSubscription.status, ["active", "trial"])
        ).update({"$set": {"status": "cancelled"}})

        # Create new active subscription
        now      = datetime.utcnow()
        end_date = now + timedelta(days=365 if cycle == "annual" else 30)

        sub = PumpSubscription(
            pump_id       = PydanticObjectId(pump_id),
            plan_id       = PydanticObjectId(plan_id),
            owner_id      = PydanticObjectId(user.id),
            billing_cycle = cycle,
            status        = "active",
            start_date    = now,
            end_date      = end_date,
            activated_at  = now,
        )
        await sub.insert()

        # Update payment record
        pay.pump_subscription_id = sub.id
        pay.razorpay_payment_id  = payment_id
        pay.razorpay_signature   = signature
        pay.status               = "paid"
        pay.paid_at              = now
        await pay.save()

        log.info("Subscription payment verified & activated", payment_id=str(pay.id), sub_id=str(sub.id), status=pay.status)
        return {"success": True, "message": "Payment verified. Subscription activated!"}

    except Exception as e:
        log.error("Payment verify error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))