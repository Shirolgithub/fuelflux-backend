"""
Run from backend folder: python fix_pending_payments.py
Fixes existing SubscriptionPayment rows stuck at "pending" 
even though their PumpSubscription is already active.
"""
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.database import SessionLocal
from src.db.models.subscription import SubscriptionPayment, PumpSubscription
from datetime import datetime

def fix_pending():
    db = SessionLocal()
    try:
        pending = db.query(SubscriptionPayment).filter(
            SubscriptionPayment.status == "pending",
            SubscriptionPayment.razorpay_payment_id.isnot(None),  # had a payment_id = actually paid
        ).all()

        fixed = 0
        for pay in pending:
            active_sub = db.query(PumpSubscription).filter(
                PumpSubscription.pump_id == pay.pump_id,
                PumpSubscription.status  == "active",
            ).first()

            if active_sub:
                pay.status = "paid"
                pay.pump_subscription_id = active_sub.id
                if not pay.paid_at:
                    pay.paid_at = pay.created_at or datetime.utcnow()
                fixed += 1
                print(f"✅ Fixed payment #{pay.id} → paid (sub #{active_sub.id})")

        db.commit()
        print(f"\n🎉 Fixed {fixed} payment record(s).")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    fix_pending()