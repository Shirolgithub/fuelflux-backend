import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from src.db.models.pump import Pump
from src.db.models.user import User

async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    await init_beanie(database=client.fuelflux, document_models=[Pump, User])
    
    pumps = await Pump.find_all().to_list()
    print(f"Total pumps: {len(pumps)}")
    for p in pumps:
        owner = await User.get(p.owner_id)
        owner_email = owner.email if owner else "Unknown Owner"
        print(f"Pump Name: {p.name}, ID: {p.id}, Owner Email: {owner_email}")

if __name__ == "__main__":
    asyncio.run(main())
