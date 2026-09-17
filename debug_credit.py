import asyncio
from src.core.database import init_db
from src.db.models.credit_request import CreditRequest
from src.db.models.user import User
from src.db.models.pump import Pump
from beanie import PydanticObjectId

async def main():
    await init_db()
    
    req = await CreditRequest.get(PydanticObjectId("6a3f90f47de8fbf6a31b537f"))
    if not req:
        print("Request not found!")
        return
    
    print(f"=== Credit Request {req.id} ===")
    print(f"logistic_partner_id: {req.logistic_partner_id}")
    print(f"pump_id:             {req.pump_id}")
    print(f"status:              {req.status}")
    print(f"logistic_signed:     {req.logistic_signed}")
    print(f"pump_owner_signed:   {req.pump_owner_signed}")
    
    # Get the pump to find the pump owner
    pump = await Pump.get(req.pump_id)
    if pump:
        print(f"\n=== Pump ===")
        print(f"pump.owner_id:       {pump.owner_id}")
        
        # Check if pump owner == logistic partner
        print(f"\n=== Identity Check ===")
        print(f"pump.owner_id == req.logistic_partner_id ? {str(pump.owner_id) == str(req.logistic_partner_id)}")
        
        # Get both users
        logistic = await User.get(req.logistic_partner_id)
        owner = await User.get(pump.owner_id)
        
        if logistic:
            print(f"\nLogistic User: {logistic.email} | roles: {logistic.roles} | id: {logistic.id}")
        if owner:
            print(f"Pump Owner:    {owner.email} | roles: {owner.roles} | id: {owner.id}")
        
        if logistic and owner:
            print(f"\nSame user? {str(logistic.id) == str(owner.id)}")
    
asyncio.run(main())
