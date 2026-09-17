from fastapi import APIRouter, Depends
from beanie import PydanticObjectId
from datetime import datetime, date, timedelta
from collections import defaultdict
from src.core.dependencies import get_current_active_user
from src.db.models.user import User
from src.db.models.transaction import Transaction
from src.db.models.attendant import Attendant
from src.db.models.pump import Pump
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
async def get_dashboard_overview(
    current_user: User = Depends(get_current_active_user)
):
    """
    Returns dashboard KPI stats for the current logged-in pump owner.
    Includes: pump info, total sales, active attendants, today's revenue,
    7-day trend, top attendants, and recent forecourt activity.
    """
    pump = await Pump.find_one(
        Pump.owner_id == current_user.id,
        Pump.is_active == True
    )

    if not pump:
        return {
            "status": "no_pump",
            "message": "No pump found. Please register a pump first.",
            "pump": None,
            "stats": None,
            "weekly_trend": [],
            "top_attendants": [],
            "forecourt_activities": [],
        }

    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)

    all_txns = await Transaction.find(Transaction.pump_id == pump.id).to_list()

    total_sales_count = len(all_txns)

    today_txns = [t for t in all_txns if t.created_at and today_start <= t.created_at < today_end]
    today_sales_count = len(today_txns)

    today_revenue = sum(
        t.amount for t in today_txns if t.event_type == "nozzle_sale"
    )

    active_attendants = await Attendant.find(
        Attendant.pump_id == pump.id,
        Attendant.is_active == True
    ).count()

    # ── Weekly Trend: last 7 days ─────────────────────────────────────────────
    week_start = today_start - timedelta(days=6)
    weekly_txns = [
        t for t in all_txns
        if t.event_type == "nozzle_sale" and t.created_at and week_start <= t.created_at < today_end
    ]

    day_revenue: dict = defaultdict(float)
    day_count: dict = defaultdict(int)
    for t in weekly_txns:
        day_key = t.created_at.strftime("%a")
        day_revenue[day_key] += t.amount
        day_count[day_key] += 1

    DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekly_trend = [
        {
            "day": day,
            "revenue": round(day_revenue.get(day, 0), 2),
            "count": day_count.get(day, 0),
        }
        for day in DAY_LABELS
    ]

    # ── Top Attendants ────────────────────────────────────────────────────────
    att_revenue: dict = defaultdict(float)
    att_volume: dict = defaultdict(float)
    for t in weekly_txns:
        if t.attendant_id:
            att_revenue[str(t.attendant_id)] += t.amount
            att_volume[str(t.attendant_id)] += t.volume

    all_attendants = await Attendant.find(Attendant.pump_id == pump.id).to_list()
    all_attendants_map = {str(a.id): a.name for a in all_attendants}

    top_attendants_list = []
    for att_id, revenue in sorted(att_revenue.items(), key=lambda x: -x[1])[:5]:
        name = all_attendants_map.get(att_id, f"Attendant #{att_id[:6]}")
        vol = att_volume.get(att_id, 0)
        top_attendants_list.append({
            "id": att_id,
            "name": name,
            "sold_liters": round(vol, 2),
            "total_amount": round(revenue, 2),
        })

    # ── Forecourt Activities: last 10 ─────────────────────────────────────────
    recent_txns = sorted(
        [t for t in all_txns if t.event_type == "nozzle_sale"],
        key=lambda t: t.created_at or datetime.min,
        reverse=True
    )[:10]

    forecourt_activities_list = [
        {
            "id": str(t.id),
            "nozzle_id": t.nozzle_id or 0,
            "vehicle_plate": t.vehicle_plate,
            "volume": round(t.volume, 2),
            "amount": round(t.amount, 2),
            "timestamp": t.created_at.isoformat() if t.created_at else None,
        }
        for t in recent_txns
    ]

    all_pumps = await Pump.find(
        Pump.owner_id == current_user.id,
        Pump.is_active == True
    ).to_list()

    return {
        "status": "ok",
        "pump": {
            "id": str(pump.id),
            "name": pump.name,
            "address": pump.address,
            "contact_number": pump.contact_number,
            "opening_time": pump.opening_time,
            "closing_time": pump.closing_time,
            "status": pump.status,
            "city": pump.city,
            "state": pump.state,
            "pincode": pump.pincode,
            "gst": pump.gst,
            "license": pump.license,
            "fuel_types": pump.fuel_types,
            "tanks_count": pump.tanks_count,
            "nozzles_count": pump.nozzles_count,
            "daily_capacity": pump.daily_capacity,
            "latitude": pump.latitude,
            "longitude": pump.longitude,
            "created_at": pump.created_at.isoformat() if pump.created_at else None,
        },
        "stats": {
            "total_sales_count": total_sales_count,
            "today_sales_count": today_sales_count,
            "today_revenue": today_revenue,
            "active_attendants": active_attendants,
        },
        "weekly_trend": weekly_trend,
        "top_attendants": top_attendants_list,
        "forecourt_activities": forecourt_activities_list,
        "pumps_count": len(all_pumps),
        "message": "Dashboard data loaded successfully"
    }


@router.get("/pumps")
async def get_owner_pumps(
    current_user: User = Depends(get_current_active_user)
):
    """
    Returns all pumps for the current logged-in user.
    Used by the frontend pump switcher in TopNavbar.
    """
    pumps = await Pump.find(
        Pump.owner_id == current_user.id,
        Pump.is_active == True
    ).to_list()

    return {
        "status": "ok",
        "pumps": [
            {
                "id": str(pump.id),
                "name": pump.name,
                "address": pump.address,
                "contact_number": pump.contact_number,
                "opening_time": pump.opening_time,
                "closing_time": pump.closing_time,
                "status": pump.status,
                "city": pump.city,
                "state": pump.state,
                "pincode": pump.pincode,
                "gst": pump.gst,
                "license": pump.license,
                "fuel_types": pump.fuel_types,
                "tanks_count": pump.tanks_count,
                "nozzles_count": pump.nozzles_count,
                "daily_capacity": pump.daily_capacity,
                "latitude": pump.latitude,
                "longitude": pump.longitude,
                "created_at": pump.created_at.isoformat() if pump.created_at else None,
            }
            for pump in pumps
        ]
    }