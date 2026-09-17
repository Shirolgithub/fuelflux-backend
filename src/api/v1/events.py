from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId
import structlog
from src.core.dependencies import get_current_active_user
from src.db.models.user import User
from src.db.models.event import Event
from src.db.schemas.event import EventCreate, EventResponse

log = structlog.get_logger()
router = APIRouter(prefix="/events", tags=["events"])


@router.post("/")
async def receive_edge_event(
    event_data: EventCreate,
    current_user: User = Depends(get_current_active_user)
):
    try:
        log.info("Edge Event Received",
                station_id=str(event_data.station_id),
                event_type=event_data.event_type)

        event = Event(
            station_id=PydanticObjectId(str(event_data.station_id)),
            event_type=event_data.event_type,
            payload=event_data.payload
        )
        await event.insert()

        log.info("Event saved", event_id=str(event.id))
        return {
            "id": str(event.id),
            "station_id": str(event.station_id),
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "processed": event.processed,
        }

    except Exception as e:
        log.error("Event save failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save event")