# src/core/auto_trial.py
# Auto-assigns the admin's "default" plan as a trial when a new pump is registered.
# Fully async — uses Beanie (MongoDB ODM)

from beanie import PydanticObjectId
from datetime import datetime, timedelta
import structlog

from src.db.models.subscription import SubscriptionPlan, PumpSubscription

log = structlog.get_logger()


async def auto_assign_default_trial(pump_id: PydanticObjectId, owner_id: PydanticObjectId) -> PumpSubscription | None:
    """
    Looks for the admin's default plan (is_default=True, is_active=True).
    If found, creates a 'trial' PumpSubscription using that plan's
    default_trial_days. If the default plan has 0 trial days, OR no
    default plan exists at all, the pump gets NO subscription —
    meaning every feature stays locked until the owner buys a plan
    or admin manually assigns one.

    Call this right after Pump creation in pumps.py / registration flow:

        from src.core.auto_trial import auto_assign_default_trial
        ...
        await pump.save()
        await auto_assign_default_trial(pump.id, pump.owner_id)
    """
    default_plan = await SubscriptionPlan.find_one(
        SubscriptionPlan.is_default == True,
        SubscriptionPlan.is_active == True,
    )

    if not default_plan:
        log.info("No default plan configured — pump stays locked until owner subscribes.", pump_id=str(pump_id))
        return None

    if default_plan.default_trial_days <= 0:
        log.info("Default plan has no trial days — pump stays locked.", pump_id=str(pump_id), plan=default_plan.name)
        return None

    now       = datetime.utcnow()
    trial_end = now + timedelta(days=default_plan.default_trial_days)

    sub = PumpSubscription(
        pump_id            = pump_id,
        plan_id            = default_plan.id,
        owner_id           = owner_id,
        billing_cycle      = "monthly",
        status             = "trial",
        start_date         = now,
        end_date           = now + timedelta(days=30),   # placeholder cycle end
        trial_end          = trial_end,
        trial_days_granted = default_plan.default_trial_days,
        admin_note         = f"Auto-assigned default trial on registration ({default_plan.name})",
    )
    await sub.save()

    log.info(
        "Auto-assigned default trial",
        pump_id=str(pump_id), plan=default_plan.name, trial_days=default_plan.default_trial_days,
    )
    return sub