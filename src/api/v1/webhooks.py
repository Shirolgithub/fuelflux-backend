"""
FastAPI Router for Razorpay & RazorpayX Webhooks
Processes captured payments and payout processed/failed events with signature verification.
"""
from fastapi import APIRouter, Request, Header, HTTPException, status
from typing import Dict, Any
from decimal import Decimal
from datetime import datetime
import json
import structlog

from src.services.razorpay_service import razorpay_service
from src.core.database import get_mongo_client
from src.db.models.user_wallet import (
    UserWallet, WalletTransaction, PaymentOrder, PayoutRequest, WalletAuditLog
)

log = structlog.get_logger()
router = APIRouter(prefix="/webhooks/razorpay", tags=["webhooks"])


@router.post("/payment")
async def handle_payment_webhook(
    request: Request,
    x_signature: str = Header(..., alias="X-Razorpay-Signature")
):
    """
    Handles Razorpay standard payment webhooks (e.g. payment.captured).
    Credits user wallet balance safely.
    """
    body = await request.body()
    
    # 1. Signature check
    verified = razorpay_service.verify_webhook_signature(body, x_signature)
    if not verified:
        log.error("webhook_signature_verification_failed", type="payment")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    # 2. Parse payload
    try:
        event = json.loads(body.decode('utf-8'))
        event_name = event.get("event")
        log.info("payment_webhook_received", event=event_name)
    except Exception as e:
        log.error("webhook_json_parse_failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if event_name != "payment.captured":
        return {"status": "skipped", "message": f"Event {event_name} not handled"}

    # Extract details
    try:
        payment_entity = event["payload"]["payment"]["entity"]
        payment_id = payment_entity["id"]
        order_id = payment_entity["order_id"]
        amount_paise = payment_entity["amount"]
        amount_inr = Decimal(amount_paise) / Decimal("100")
    except KeyError as e:
        log.error("webhook_missing_fields", error=str(e))
        raise HTTPException(status_code=400, detail="Missing required webhook payload fields")

    # 3. Process credit atomically
    client = get_mongo_client()
    async with await client.start_session() as session:
        async with session.start_transaction():
            # Check payment order
            order = await PaymentOrder.find_one(
                PaymentOrder.razorpay_order_id == order_id,
                session=session
            )
            if not order:
                log.error("webhook_order_not_found", order_id=order_id)
                return {"status": "ignored", "message": "Associated payment order not found"}

            if order.status == "paid":
                log.info("webhook_payment_already_processed", payment_id=payment_id)
                return {"status": "ok", "message": "Already processed"}

            # Idempotency check on WalletTransaction
            existing_txn = await WalletTransaction.find_one(
                WalletTransaction.reference_id == payment_id,
                session=session
            )
            if existing_txn:
                log.warn("webhook_duplicate_txn_prevented", payment_id=payment_id)
                order.status = "paid"
                await order.save(session=session)
                return {"status": "ok", "message": "Already credited"}

            # Fetch wallet
            wallet = await UserWallet.find_one(UserWallet.id == order.wallet_id, session=session)
            if not wallet:
                log.error("webhook_wallet_not_found", wallet_id=str(order.wallet_id))
                return {"status": "error", "message": "Wallet not found"}

            # Perform atomic credit
            balance_before = wallet.balance
            balance_after = balance_before + amount_inr

            # Update wallet
            wallet.balance = balance_after
            wallet.updated_at = datetime.utcnow()
            await wallet.save(session=session)

            # Update order
            order.status = "paid"
            await order.save(session=session)

            # Write ledger transaction
            txn = WalletTransaction(
                wallet_id=wallet.id,
                type="credit",
                amount=amount_inr,
                balance_after=balance_after,
                category="add_money",
                reference_id=payment_id,
                status="success",
                idempotency_key=f"add_money:{payment_id}",
                metadata={"webhook_event": event_name, "rzp_order_id": order_id}
            )
            await txn.insert(session=session)

            # Audit trail
            audit = WalletAuditLog(
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                action="ADD_MONEY_WEBHOOK",
                amount=amount_inr,
                balance_before=balance_before,
                balance_after=balance_after,
                performed_by="webhook",
                metadata_json=f'{{"payment_id": "{payment_id}", "event": "{event_name}"}}'
            )
            await audit.insert(session=session)

    log.info("webhook_payment_captured_success", payment_id=payment_id, amount=str(amount_inr))
    return {"status": "ok", "message": "Wallet credited successfully"}


@router.post("/payout")
async def handle_payout_webhook(
    request: Request,
    x_signature: str = Header(..., alias="X-Razorpay-Signature")
):
    """
    Handles RazorpayX Payout webhooks (e.g. payout.processed, payout.failed, payout.reversed).
    Settles or reverses pending withdrawal balances atomically.
    """
    body = await request.body()

    # 1. Signature check
    verified = razorpay_service.verify_webhook_signature(body, x_signature)
    if not verified:
        log.error("webhook_signature_verification_failed", type="payout")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    # 2. Parse payload
    try:
        event = json.loads(body.decode('utf-8'))
        event_name = event.get("event")
        log.info("payout_webhook_received", event=event_name)
    except Exception as e:
        log.error("webhook_json_parse_failed", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if event_name not in ("payout.processed", "payout.failed", "payout.reversed"):
        return {"status": "skipped", "message": f"Event {event_name} not handled"}

    # Extract details
    try:
        payout_entity = event["payload"]["payout"]["entity"]
        payout_id = payout_entity["id"]
        reference_id = payout_entity.get("reference_id")
        amount_paise = payout_entity["amount"]
        amount_inr = Decimal(amount_paise) / Decimal("100")
    except KeyError as e:
        log.error("payout_webhook_missing_fields", error=str(e))
        raise HTTPException(status_code=400, detail="Missing required webhook payload fields")

    # 3. Process status change atomically
    client = get_mongo_client()
    async with await client.start_session() as session:
        async with session.start_transaction():
            # Find associated payout request
            payout = await PayoutRequest.find_one(
                (PayoutRequest.razorpay_payout_id == payout_id) | (PayoutRequest.idempotency_key == reference_id),
                session=session
            )
            if not payout:
                log.error("webhook_payout_request_not_found", payout_id=payout_id, reference_id=reference_id)
                return {"status": "ignored", "message": "Payout request match not found"}

            # Find ledger transaction
            txn = await WalletTransaction.find_one(
                WalletTransaction.reference_id == payout_id,
                session=session
            )
            if not txn:
                # Fallback check on idempotency key
                txn = await WalletTransaction.find_one(
                    WalletTransaction.idempotency_key == payout.idempotency_key,
                    session=session
                )
                if not txn:
                    log.error("webhook_payout_transaction_not_found", payout_id=payout_id)
                    return {"status": "error", "message": "Ledger transaction match not found"}

            wallet = await UserWallet.find_one(UserWallet.id == payout.wallet_id, session=session)
            if not wallet:
                log.error("webhook_payout_wallet_not_found", wallet_id=str(payout.wallet_id))
                return {"status": "error", "message": "Wallet not found"}

            # Event handlers
            if event_name == "payout.processed":
                if payout.status == "processed":
                    return {"status": "ok", "message": "Already processed"}

                # Successful processed → Balance permanently deducted now
                balance_before = wallet.balance
                balance_after = balance_before - amount_inr

                # Deduct balance
                wallet.balance = balance_after
                wallet.updated_at = datetime.utcnow()
                await wallet.save(session=session)

                # Update transaction
                txn.status = "success"
                txn.balance_after = balance_after
                txn.reference_id = payout_id
                await txn.save(session=session)

                # Update payout request
                payout.status = "processed"
                payout.processed_at = datetime.utcnow()
                await payout.save(session=session)

                # Audit trail
                audit = WalletAuditLog(
                    wallet_id=wallet.id,
                    user_id=wallet.user_id,
                    action="WITHDRAWAL_PROCESSED_WEBHOOK",
                    amount=amount_inr,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    performed_by="webhook",
                    metadata_json=f'{{"payout_id": "{payout_id}"}}'
                )
                await audit.insert(session=session)
                log.info("webhook_payout_processed_success", payout_id=payout_id, user_id=str(wallet.user_id))

            elif event_name in ("payout.failed", "payout.reversed"):
                if payout.status in ("failed", "reversed"):
                    return {"status": "ok", "message": "Already failed/reversed"}

                # Release daily limit hold so they can retry
                wallet.daily_withdrawal_used = max(Decimal("0.0"), wallet.daily_withdrawal_used - amount_inr)
                await wallet.save(session=session)

                # Update transaction status (balance was never deducted, so no credit refund needed)
                txn.status = "reversed" if event_name == "payout.reversed" else "failed"
                txn.reference_id = payout_id
                await txn.save(session=session)

                # Update payout request
                payout.status = "reversed" if event_name == "payout.reversed" else "failed"
                payout.failure_reason = payout_entity.get("status_details", {}).get("description") or f"Gateway reported: {event_name}"
                await payout.save(session=session)

                # Audit trail
                audit = WalletAuditLog(
                    wallet_id=wallet.id,
                    user_id=wallet.user_id,
                    action="WITHDRAWAL_FAILED_WEBHOOK",
                    amount=amount_inr,
                    balance_before=wallet.balance,
                    balance_after=wallet.balance,
                    performed_by="webhook",
                    metadata_json=f'{{"payout_id": "{payout_id}", "status": "{event_name}"}}'
                )
                await audit.insert(session=session)
                log.info("webhook_payout_failure_reconciled", payout_id=payout_id, event=event_name)

    return {"status": "ok", "message": "Webhook processed successfully"}
