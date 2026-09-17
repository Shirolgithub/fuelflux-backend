from fastapi import APIRouter, Depends
from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump

router = APIRouter(prefix="/investor", tags=["investor"])


@router.get("/dashboard")
async def investor_dashboard(
    current_user: User = Depends(require_role(["investor"]))
):
    """Investor Read-Only Dashboard"""
    total_pumps = await Pump.count()
    active_pumps = await Pump.find(Pump.is_active == True).count()
    return {
        "platform_stats": {
            "total_pumps": total_pumps,
            "total_active_pumps": active_pumps,
            "monthly_revenue": 1245000,
            "total_users": 156,
            "growth_this_month": "+18%"
        },
        "top_performing_pumps": [
            {"name": "Shree Krishna Petrol Pump", "sales": 245000},
            {"name": "Bharat Fuel Station", "sales": 189000},
            {"name": "Hindustan Pump", "sales": 167500}
        ],
        "recent_alerts": [
            "3 pumps have high reconciliation mismatch",
            "2 new pumps approved this week"
        ],
        "message": "Investor Dashboard - Read Only Access"
    }


@router.get("/all-pumps")
async def get_all_pumps_summary(
    current_user: User = Depends(require_role(["investor"]))
):
    """Overall platform performance for investors"""
    total_pumps = await Pump.count()
    active_pumps = await Pump.find(Pump.is_active == True).count()
    return {
        "total_pumps": total_pumps,
        "active_pumps": active_pumps,
        "average_daily_sales_per_pump": 18500,
        "message": "Platform wide summary"
    }