from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.customer import Customer
from src.db.schemas.customer import CustomerCreate, CustomerResponse
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/crm", tags=["crm"])


@router.post("/customers")
async def add_customer(
    customer_data: CustomerCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        pump = await Pump.find_one(
            Pump.id == PydanticObjectId(customer_data.pump_id),
            Pump.owner_id == current_user.id
        )
        if not pump:
            raise HTTPException(status_code=403, detail="You don't own this pump")

        customer = Customer(
            **customer_data.model_dump(exclude={'pump_id'}),
            pump_id=pump.id
        )
        await customer.insert()
        return {
            "id": str(customer.id),
            "name": customer.name,
            "phone": customer.phone,
            "vehicle_plate": customer.vehicle_plate,
            "pump_id": str(customer.pump_id),
            "created_at": customer.created_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to add customer", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to add customer: {str(e)}")


@router.get("/customers")
async def get_customers(
    pump_id: str,
    current_user: User = Depends(get_current_active_user)
):
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")
    customers = await Customer.find(Customer.pump_id == oid).to_list()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "phone": c.phone,
            "vehicle_plate": c.vehicle_plate,
            "credit_limit": c.credit_limit,
            "outstanding_amount": c.outstanding_amount,
            "is_fleet": c.is_fleet,
        }
        for c in customers
    ]


@router.get("/risk-alerts")
async def get_risk_alerts(
    pump_id: str,
    current_user: User = Depends(get_current_active_user)
):
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")

    customers = await Customer.find(
        Customer.pump_id == oid,
        Customer.outstanding_amount > 0
    ).to_list()

    alerts = []
    for c in customers:
        if c.outstanding_amount > c.credit_limit * 0.8:
            alerts.append({
                "customer_id": str(c.id),
                "name": c.name,
                "vehicle_plate": c.vehicle_plate,
                "outstanding": c.outstanding_amount,
                "credit_limit": c.credit_limit,
                "risk_level": "HIGH" if c.outstanding_amount > c.credit_limit else "MEDIUM"
            })

    return {
        "total_risk_customers": len(alerts),
        "alerts": alerts
    }