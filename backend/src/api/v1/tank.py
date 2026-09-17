from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.tank import Tank
from src.db.schemas.tank import TankCreate, TankResponse
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/tank", tags=["inventory"])


@router.post("/")
async def add_tank(
    tank_data: TankCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        pump = await Pump.find_one(
            Pump.id == PydanticObjectId(tank_data.pump_id),
            Pump.owner_id == current_user.id
        )
        if not pump:
            raise HTTPException(status_code=403, detail="You don't own this pump")

        tank = Tank(
            **tank_data.model_dump(exclude={'pump_id'}),
            pump_id=pump.id
        )
        await tank.insert()
        return {
            "id": str(tank.id),
            "pump_id": str(tank.pump_id),
            "tank_number": tank.tank_number,
            "capacity_liters": tank.capacity_liters,
            "fuel_type": tank.fuel_type,
            "current_level_liters": tank.current_level_liters,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to add tank", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to add tank: {str(e)}")


@router.get("/list/{pump_id}")
async def get_tanks(
    pump_id: str,
    current_user: User = Depends(get_current_active_user)
):
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")
    tanks = await Tank.find(Tank.pump_id == oid).to_list()
    return [
        {
            "id": str(t.id),
            "pump_id": str(t.pump_id),
            "tank_number": t.tank_number,
            "fuel_type": t.fuel_type,
            "capacity_liters": t.capacity_liters,
            "current_level_liters": t.current_level_liters,
        }
        for t in tanks
    ]


@router.get("/level/{tank_id}")
async def get_tank_level(
    tank_id: str,
    current_user: User = Depends(get_current_active_user)
):
    try:
        oid = PydanticObjectId(tank_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid tank_id")
    tank = await Tank.get(oid)
    if not tank:
        raise HTTPException(status_code=404, detail="Tank not found")
    return {
        "id": str(tank.id),
        "pump_id": str(tank.pump_id),
        "tank_number": tank.tank_number,
        "fuel_type": tank.fuel_type,
        "capacity_liters": tank.capacity_liters,
        "current_level_liters": tank.current_level_liters,
        "temperature": tank.temperature,
        "last_updated": tank.last_updated,
    }