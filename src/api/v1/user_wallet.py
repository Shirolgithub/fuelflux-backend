"""
FastAPI Router for User Wallet & Ledger System
Implements REST endpoints for add money, verify, P2P transfer, spend, and bank withdrawals.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request, status
from typing import Optional, List, Dict, Any
from decimal import Decimal
from beanie import PydanticObjectId
from datetime import datetime
import structlog

from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.services.wallet_service import wallet_service
from src.db.models.user_wallet import UserWallet, WalletTransaction, PayoutRequest
from pydantic import BaseModel, Field

log = structlog.get_logger()
router = APIRouter(prefix="/user-wallet", tags=["user-wallet"])


# ─────────────────────────────────────────────
# Request Schemas
# ─────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, description="Amount in INR to add to wallet")

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class SpendRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    category: str = Field(..., min_length=2, description="E.g., subscription, fuel_order, udhaar_clearance")
    reference_id: str = Field(..., min_length=3)
    metadata: Optional[Dict[str, Any]] = None

class BankDetails(BaseModel):
    account_holder: str = Field(..., min_length=3)
    account_number: str = Field(..., min_length=9, max_length=18)
    ifsc: str = Field(..., min_length=11, max_length=11)
    bank_name: str = Field(..., min_length=3)

class WithdrawalRequestPayload(BaseModel):
    amount: Decimal = Field(..., description="Amount between 100 and 20000 INR")
    bank_details: BankDetails

class P2PTransferRequestPayload(BaseModel):
    receiver_email_or_phone: str
    amount: Decimal = Field(..., gt=0)
    note: Optional[str] = None


# ─────────────────────────────────────────────
# GET /user-wallet/balance
# ─────────────────────────────────────────────
@router.get("/balance")
async def get_balance(current_user: User = Depends(get_current_active_user)):
    """
    Returns the cached and available balances for the user.
    """
    wallet = await wallet_service.get_or_create_wallet(current_user.id)
    available = await wallet_service.get_available_balance(wallet)
    return {
        "status": "ok",
        "balance": wallet.balance,
        "available_balance": available,
        "currency": wallet.currency,
        "status": wallet.status
    }


# ─────────────────────────────────────────────
# GET /user-wallet/transactions (Passbook)
# ─────────────────────────────────────────────
@router.get("/transactions")
async def get_transactions(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    category: Optional[str] = Query(None),
    txn_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user)
):
    """
    Returns a paginated list of ledger transactions for the user's wallet.
    """
    wallet = await wallet_service.get_or_create_wallet(current_user.id)

    # Build filters
    filters = {"wallet_id": wallet.id}
    if category:
        filters["category"] = category
    if txn_type:
        filters["type"] = txn_type

    # Query Beanie with sorting
    total = await WalletTransaction.find(filters).count()
    txns = await WalletTransaction.find(filters).sort(-WalletTransaction.created_at).skip((page - 1) * limit).limit(limit).to_list()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "transactions": [
            {
                "id": str(t.id),
                "type": t.type,
                "amount": t.amount,
                "balance_after": t.balance_after,
                "category": t.category,
                "reference_id": t.reference_id,
                "status": t.status,
                "metadata": t.metadata,
                "created_at": t.created_at.isoformat()
            }
            for t in txns
        ]
    }


# ─────────────────────────────────────────────
# GET /user-wallet/transactions/{id}
# ─────────────────────────────────────────────
@router.get("/transactions/{id}")
async def get_transaction_detail(
    id: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Returns single transaction detail.
    """
    wallet = await wallet_service.get_or_create_wallet(current_user.id)
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid transaction ID format")

    txn = await WalletTransaction.find_one(WalletTransaction.id == oid, WalletTransaction.wallet_id == wallet.id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {
        "id": str(txn.id),
        "type": txn.type,
        "amount": txn.amount,
        "balance_after": txn.balance_after,
        "category": txn.category,
        "reference_id": txn.reference_id,
        "status": txn.status,
        "metadata": txn.metadata,
        "created_at": txn.created_at.isoformat()
    }


@router.post("/add-money/create-order")
async def create_payment_order(
    payload: CreateOrderRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Initiates payment request by creating Razorpay order.
    """
    try:
        from src.core.config import settings
        order = await wallet_service.add_money_create_order(current_user.id, payload.amount)
        return {
            "status": "ok",
            "order": order,
            "key_id": settings.RAZORPAY_KEY_ID or "rzp_test_T3I8ZwKy1bxCDx"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("razorpay_order_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to connect to gateway payment provider")


# ─────────────────────────────────────────────
# POST /user-wallet/add-money/verify
# ─────────────────────────────────────────────
@router.post("/add-money/verify")
async def verify_payment(
    payload: VerifyPaymentRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Verifies Razorpay payment signature and credits wallet immediately.
    """
    try:
        wallet = await wallet_service.add_money_verify_and_credit(
            current_user.id,
            payload.razorpay_order_id,
            payload.razorpay_payment_id,
            payload.razorpay_signature
        )
        return {
            "status": "ok",
            "message": "Payment verified and credited successfully",
            "balance": wallet.balance
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("payment_verify_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Database transaction error")


# ─────────────────────────────────────────────
# POST /user-wallet/spend
# ─────────────────────────────────────────────
@router.post("/spend")
async def spend_funds(
    payload: SpendRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Deducts wallet balance for internal platform service/order payments.
    """
    try:
        txn = await wallet_service.spend_wallet(
            current_user.id,
            payload.amount,
            payload.category,
            payload.reference_id,
            payload.metadata
        )
        return {
            "status": "ok",
            "message": "Transaction debit completed successfully",
            "transaction_id": str(txn.id),
            "balance_after": txn.balance_after
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("spend_funds_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Spend debit failed")


# ─────────────────────────────────────────────
# POST /user-wallet/withdraw
# ─────────────────────────────────────────────
@router.post("/withdraw")
async def withdraw_funds(
    payload: WithdrawalRequestPayload,
    current_user: User = Depends(get_current_active_user)
):
    """
    Initiates standard IMPS payout transfer. Minimum withdrawal ₹100, Maximum ₹20,000/day.
    """
    try:
        payout = await wallet_service.initiate_withdrawal(
            current_user.id,
            payload.amount,
            payload.bank_details.model_dump()
        )
        return {
            "status": "ok",
            "message": "Withdrawal request dispatched to payment processor",
            "payout_id": str(payout.id),
            "razorpay_payout_id": payout.razorpay_payout_id,
            "status": payout.status
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("withdraw_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Withdrawal request failed")


# ─────────────────────────────────────────────
# POST /user-wallet/p2p-transfer
# ─────────────────────────────────────────────
@router.post("/p2p-transfer")
async def p2p_transfer(
    payload: P2PTransferRequestPayload,
    current_user: User = Depends(get_current_active_user)
):
    """
    Atomic Peer-to-Peer balance transfer between users. Fee-free.
    """
    try:
        transfer = await wallet_service.p2p_transfer(
            current_user.id,
            payload.receiver_email_or_phone,
            payload.amount,
            payload.note
        )
        return {
            "status": "ok",
            "message": "Peer transfer completed atomically",
            "transfer_id": str(transfer.id),
            "amount": payload.amount
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("p2p_transfer_endpoint_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Database transaction error")


# ─────────────────────────────────────────────
# GET /admin/wallet/transactions (Admin Audit Trail)
# ─────────────────────────────────────────────
@router.get("/admin/transactions", dependencies=[Depends(require_role(["admin"]))])
async def admin_get_all_transactions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(None)
):
    """
    Admin: returns the global wallet transaction ledger and audit trail.
    """
    filters = {}
    if user_id:
        try:
            # Resolve user's wallet first
            uid = PydanticObjectId(user_id)
            wallet = await UserWallet.find_one(UserWallet.user_id == uid)
            if wallet:
                filters["wallet_id"] = wallet.id
            else:
                return {"total": 0, "page": page, "limit": limit, "transactions": []}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user_id format")

    total = await WalletTransaction.find(filters).count()
    txns = await WalletTransaction.find(filters).sort(-WalletTransaction.created_at).skip((page - 1) * limit).limit(limit).to_list()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "transactions": [
            {
                "id": str(t.id),
                "wallet_id": str(t.wallet_id),
                "type": t.type,
                "amount": t.amount,
                "balance_after": t.balance_after,
                "category": t.category,
                "reference_id": t.reference_id,
                "status": t.status,
                "created_at": t.created_at.isoformat()
            }
            for t in txns
        ]
    }
