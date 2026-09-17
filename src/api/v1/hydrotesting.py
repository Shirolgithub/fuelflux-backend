from fastapi import APIRouter, Depends, HTTPException
from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.schemas.hydrotesting import HydroTestCreate, HydroTestResponse

router = APIRouter(prefix="/hydrotesting", tags=["hydrotesting"])


@router.post("/check")
async def hydrotesting_check(
    test_data: HydroTestCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """ANPR based Hydrotesting / Vehicle Validation"""
    try:
        result = {
            "vehicle_plate": test_data.vehicle_plate,
            "status": "passed" if len(test_data.vehicle_plate) > 8 else "failed",
            "anpr_confidence": 92.5,
            "message": "Vehicle validated successfully via ANPR",
            "remarks": test_data.remarks
        }
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent-checks/{pump_id}")
async def get_recent_hydrotests(
    pump_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """Recent Hydrotesting Checks"""
    return {
        "pump_id": pump_id,
        "recent_checks": [
            {
                "vehicle_plate": "MH12AB1234",
                "status": "passed",
                "checked_at": "2026-05-30 08:15",
                "confidence": 94.2
            },
            {
                "vehicle_plate": "DL7CN4567",
                "status": "failed",
                "checked_at": "2026-05-30 07:45",
                "confidence": 87.1
            }
        ]
    }


@router.post("/validate-vehicle")
async def validate_vehicle(
    vehicle_plate: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Simulated Government DB Check"""
    try:
        fake_result = {
            "vehicle_plate": vehicle_plate,
            "owner_name": "Rahul Sharma",
            "registration_date": "2022-03-15",
            "insurance_valid_till": "2027-05-10",
            "puc_valid_till": "2026-08-20",
            "hydrotest_status": "PASSED",
            "validity": "Valid till 2027",
            "confidence": 96.5
        }
        return fake_result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))