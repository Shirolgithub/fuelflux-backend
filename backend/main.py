from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import structlog
import traceback
from src.core.config import settings
from src.core.database import init_db, close_db
from src.api.v1 import (auth, pumps, attendants, events, sales, reconciliation,
                    dashboard, live, crm, udhaar, admin, tank, reports, logistic,
                    employee, investor, hydrotesting, accounting, credit,
                    inventory, payment, invoice_api, subscriptions,admin_subscriptions)
from src.api.v1 import settings as settings_router
from src.services.invoice_scheduler import start_scheduler, stop_scheduler

log = structlog.get_logger()

from fastapi.staticfiles import StaticFiles
import os

load_dotenv()


app = FastAPI(
    title="Fuelflux Backend",
    description="AI-Powered Petrol Pump Management System",
    version="1.0.0"
)
os.makedirs("public/uploads/logos", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="public/uploads"), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth.router, prefix=settings.API_V1_STR, tags=["auth"])
app.include_router(pumps.router, prefix=settings.API_V1_STR, tags=["pumps"])
app.include_router(attendants.router, prefix=settings.API_V1_STR, tags=["attendants"])
app.include_router(events.router, prefix=settings.API_V1_STR)
app.include_router(sales.router, prefix=settings.API_V1_STR)
app.include_router(reconciliation.router, prefix=settings.API_V1_STR)
app.include_router(dashboard.router, prefix=settings.API_V1_STR)
app.include_router(accounting.router, prefix=settings.API_V1_STR)
app.include_router(crm.router, prefix=settings.API_V1_STR)
app.include_router(udhaar.router, prefix=settings.API_V1_STR)
app.include_router(admin.router, prefix=settings.API_V1_STR)
app.include_router(tank.router, prefix=settings.API_V1_STR)
app.include_router(reports.router, prefix=settings.API_V1_STR)
app.include_router(logistic.router, prefix=settings.API_V1_STR)
app.include_router(employee.router, prefix=settings.API_V1_STR)
app.include_router(investor.router, prefix=settings.API_V1_STR)
app.include_router(hydrotesting.router, prefix=settings.API_V1_STR)
app.include_router(live.router, prefix=settings.API_V1_STR)
app.include_router(credit.router, prefix=settings.API_V1_STR)
app.include_router(payment.router, prefix=settings.API_V1_STR)
app.include_router(inventory.router, prefix=settings.API_V1_STR)
app.include_router(invoice_api.router, prefix=settings.API_V1_STR)
app.include_router(settings_router.router, prefix=settings.API_V1_STR)
app.include_router(subscriptions.router, prefix=settings.API_V1_STR)
app.include_router(admin_subscriptions.router, prefix=settings.API_V1_STR)

start_scheduler()

@app.on_event("shutdown")
async def shutdown_event():
    stop_scheduler()
    await close_db()


@app.on_event("startup")
async def startup_event():
    try:
        # Initialize MongoDB + Beanie
        await init_db()
        log.info("MongoDB connected successfully")

        # Seed support tickets if collection is empty
        from src.db.models.support import seed_support_tickets
        await seed_support_tickets()
        log.info("Support tickets checked/seeded successfully")

    except Exception as e:
        log.error("Database startup error", error=str(e))
        traceback.print_exc()


@app.get("/")
async def root():
    return {"message": "Fuelflux Backend is running 🚀", "status": "healthy", "db": "MongoDB"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)