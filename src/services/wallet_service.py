"""
User Wallet Business Logic & Ledger Operations
Uses MongoDB ACID transactions for all monetary operations.
"""
from decimal import Decimal
from datetime import datetime, date
from typing import Optional, Dict, Any, List
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError
import structlog

from beanie.operators import In, Or
from src.core.database import get_mongo_client
from src.db.models.user import User
from src.db.models.user_wallet import (
    UserWallet, WalletTransaction, PaymentOrder, PayoutRequest, P2PTransfer, WalletAuditLog
)
from src.services.razorpay_service import razorpay_service

log = structlog.get_logger()


class WalletService:
    async def get_or_create_wallet(self, user_id: PydanticObjectId, session=None) -> UserWallet:
        """
        Retrieves the wallet for a user, or creates one with zero balance if it does not exist.
        """
        wallet = await UserWallet.find_one(UserWallet.user_id == user_id, session=session)
        if not wallet:
            wallet = UserWallet(
                user_id=user_id,
                balance=Decimal("0.0"),
                currency="INR",
                status="active",
                daily_withdrawal_used=Decimal("0.0"),
                daily_withdrawal_date=date.today(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            await wallet.insert(session=session)
            log.info("user_wallet_created", user_id=str(user_id), wallet_id=str(wallet.id))
        return wallet

    async def get_available_balance(self, wallet: UserWallet, session=None) -> Decimal:
        """
        Calculates the available balance for a wallet by subtracting all pending payouts
        from the cached balance.
        """
        # Sum of all payout requests that are queued or processing
        pending_payouts = await PayoutRequest.find(
            PayoutRequest.wallet_id == wallet.id,
            In(PayoutRequest.status, ["queued", "processing"]),
            session=session
        ).to_list()

        pending_total = sum(p.amount for p in pending_payouts)
        available = wallet.balance - pending_total
        return max(Decimal("0.0"), available)

    async def add_money_create_order(self, user_id: PydanticObjectId, amount: Decimal) -> Dict[str, Any]:
        """
        Prepares a top-up request by creating an internal PaymentOrder and a Razorpay Order.
        """
        if amount <= 0:
            raise ValueError("Recharge amount must be greater than zero")

        wallet = await self.get_or_create_wallet(user_id)
        if wallet.status == "frozen":
            raise ValueError("Wallet is frozen")

        # Generate unique receipt ID
        receipt_id = f"rcpt_{int(datetime.utcnow().timestamp())}_{str(user_id)[:6]}"

        # Call Razorpay to create order
        rzp_order = razorpay_service.create_order(amount, receipt_id)

        # Record payment order in database
        order = PaymentOrder(
            wallet_id=wallet.id,
            razorpay_order_id=rzp_order["id"],
            amount=rzp_order["amount"],
            amount_inr=amount,
            status="created"
        )
        await order.insert()
        return rzp_order

    async def add_money_verify_and_credit(
        self, user_id: PydanticObjectId, rzp_order_id: str, rzp_payment_id: str, rzp_signature: str
    ) -> UserWallet:
        """
        Verifies client-side signature and credits balance inside an ACID transaction.
        """
        verified = razorpay_service.verify_payment_signature(rzp_order_id, rzp_payment_id, rzp_signature)
        if not verified:
            raise ValueError("Invalid Razorpay payment signature")

        client = get_mongo_client()
        async with await client.start_session() as session:
            async with session.start_transaction():
                wallet = await self.get_or_create_wallet(user_id, session=session)
                if wallet.status == "frozen":
                    raise ValueError("Wallet is frozen")

                # Verify order exists
                order = await PaymentOrder.find_one(
                    PaymentOrder.razorpay_order_id == rzp_order_id,
                    PaymentOrder.wallet_id == wallet.id,
                    session=session
                )
                if not order:
                    raise ValueError("Payment order not found")

                if order.status == "paid":
                    return wallet  # Already processed

                # Idempotency check: check if reference payment ID already exists in transactions
                existing_txn = await WalletTransaction.find_one(
                    WalletTransaction.reference_id == rzp_payment_id,
                    session=session
                )
                if existing_txn:
                    log.warn("add_money_payment_already_credited", payment_id=rzp_payment_id)
                    order.status = "paid"
                    await order.save(session=session)
                    return wallet

                # Mark order paid
                order.status = "paid"
                await order.save(session=session)

                # Insert ledger transaction
                balance_before = wallet.balance
                balance_after = balance_before + order.amount_inr
                
                # Update cached wallet balance
                wallet.balance = balance_after
                wallet.updated_at = datetime.utcnow()
                await wallet.save(session=session)

                # Create transaction record
                txn = WalletTransaction(
                    wallet_id=wallet.id,
                    type="credit",
                    amount=order.amount_inr,
                    balance_after=balance_after,
                    category="add_money",
                    reference_id=rzp_payment_id,
                    status="success",
                    idempotency_key=f"add_money:{rzp_payment_id}",
                    metadata={"rzp_order_id": rzp_order_id}
                )
                await txn.insert(session=session)

                # Write audit log
                audit = WalletAuditLog(
                    wallet_id=wallet.id,
                    user_id=user_id,
                    action="ADD_MONEY",
                    amount=order.amount_inr,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    performed_by="user",
                    metadata_json=f'{{"payment_id": "{rzp_payment_id}", "order_id": "{rzp_order_id}"}}'
                )
                await audit.insert(session=session)

                log.info("wallet_credited", user_id=str(user_id), amount=str(order.amount_inr), new_balance=str(balance_after))
                return wallet

    async def spend_wallet(
        self, user_id: PydanticObjectId, amount: Decimal, category: str, reference_id: str, metadata: Optional[Dict] = None
    ) -> WalletTransaction:
        """
        Debits a user's wallet for internal platform services using a strict ledger transaction.
        """
        if amount <= 0:
            raise ValueError("Spend amount must be greater than zero")

        client = get_mongo_client()
        async with await client.start_session() as session:
            async with session.start_transaction():
                wallet = await self.get_or_create_wallet(user_id, session=session)
                if wallet.status == "frozen":
                    raise ValueError("Wallet is frozen")

                # Get true available balance
                available = await self.get_available_balance(wallet, session=session)
                if available < amount:
                    raise ValueError(f"Insufficient available wallet balance. Required: {amount}, Available: {available}")

                # Idempotency check
                idempotency_key = f"spend:{reference_id}"
                existing_txn = await WalletTransaction.find_one(
                    WalletTransaction.idempotency_key == idempotency_key,
                    session=session
                )
                if existing_txn:
                    log.warn("spend_wallet_duplicate_prevented", reference_id=reference_id)
                    return existing_txn

                balance_before = wallet.balance
                balance_after = balance_before - amount

                # Update wallet balance
                wallet.balance = balance_after
                wallet.updated_at = datetime.utcnow()
                await wallet.save(session=session)

                # Insert WalletTransaction
                txn = WalletTransaction(
                    wallet_id=wallet.id,
                    type="debit",
                    amount=amount,
                    balance_after=balance_after,
                    category=category,
                    reference_id=reference_id,
                    status="success",
                    idempotency_key=idempotency_key,
                    metadata=metadata
                )
                await txn.insert(session=session)

                # Audit Log
                audit = WalletAuditLog(
                    wallet_id=wallet.id,
                    user_id=user_id,
                    action=f"SPEND_{category.upper()}",
                    amount=amount,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    performed_by="user",
                    metadata_json=f'{{"reference_id": "{reference_id}"}}'
                )
                await audit.insert(session=session)

                log.info("wallet_spend_success", user_id=str(user_id), amount=str(amount), reference_id=reference_id)
                return txn

    async def initiate_withdrawal(
        self, user_id: PydanticObjectId, amount: Decimal, bank_details: Dict[str, Any]
    ) -> PayoutRequest:
        """
        Registers a withdrawal request, locks available balance, and initiates a RazorpayX Payout.
        """
        # 1. Validation Checks
        if amount < Decimal("100") or amount > Decimal("20000"):
            raise ValueError("Withdrawal amount must be between ₹100 and ₹20,000")

        # 2. Start Database Transaction
        client = get_mongo_client()
        async with await client.start_session() as session:
            async with session.start_transaction():
                wallet = await self.get_or_create_wallet(user_id, session=session)
                if wallet.status == "frozen":
                    raise ValueError("Wallet is frozen")

                # Verify daily limit
                today = date.today()
                if wallet.daily_withdrawal_date != today:
                    wallet.daily_withdrawal_used = Decimal("0.0")
                    wallet.daily_withdrawal_date = today
                    await wallet.save(session=session)

                if wallet.daily_withdrawal_used + amount > Decimal("20000"):
                    remaining = Decimal("20000") - wallet.daily_withdrawal_used
                    raise ValueError(f"Daily withdrawal limit exceeded. Remaining limit for today: ₹{remaining:,.2f}")

                # Check available balance
                available = await self.get_available_balance(wallet, session=session)
                if available < amount:
                    raise ValueError(f"Insufficient available balance to withdraw. Required: {amount}, Available: {available}")

                # Create unique idempotency key
                txn_time = int(datetime.utcnow().timestamp())
                idempotency_key = f"withdraw:{str(user_id)}:{amount}:{txn_time}"

                # Link fund account in RazorpayX
                user = await User.find_one(User.id == user_id, session=session)
                if not wallet.fund_account_id:
                    # Create Contact
                    contact_id = await razorpay_service.create_razorpayx_contact(
                        name=bank_details["account_holder"],
                        email=user.email,
                        reference_id=str(user_id)
                    )
                    if not contact_id:
                        raise ValueError("Failed to register payout contact with gateway provider")

                    # Create Fund Account
                    fund_acc_id = await razorpay_service.create_razorpayx_fund_account(
                        contact_id=contact_id,
                        name=bank_details["account_holder"],
                        ifsc=bank_details["ifsc"],
                        account_number=bank_details["account_number"]
                    )
                    if not fund_acc_id:
                        raise ValueError("Failed to create verified fund destination account")
                    
                    wallet.fund_account_id = fund_acc_id
                    await wallet.save(session=session)

                # Keep track of limits
                wallet.daily_withdrawal_used += amount
                await wallet.save(session=session)

                # Create PayoutRequest (status: queued)
                payout = PayoutRequest(
                    wallet_id=wallet.id,
                    amount=amount,
                    bank_account_holder=bank_details["account_holder"],
                    bank_account_number="XXXX" + bank_details["account_number"][-4:],
                    bank_ifsc=bank_details["ifsc"],
                    fund_account_id=wallet.fund_account_id,
                    status="queued",
                    idempotency_key=idempotency_key
                )
                await payout.insert(session=session)

                # Create pending ledger transaction (type: debit, category: withdrawal, status: pending)
                # Note: wallet.balance is NOT updated yet (remains intact until webhook processed)
                txn = WalletTransaction(
                    wallet_id=wallet.id,
                    type="debit",
                    amount=amount,
                    balance_after=wallet.balance,  # snapshot before final deduction
                    category="withdrawal",
                    reference_id=idempotency_key,  # placeholder before payout ID is fetched
                    status="pending",
                    idempotency_key=idempotency_key,
                    metadata={"bank_name": bank_details.get("bank_name", "Corporate Bank")}
                )
                await txn.insert(session=session)

        # 3. Call RazorpayX API outside the main DB write lock (to prevent blocking connection pool)
        try:
            rzp_payout = await razorpay_service.initiate_razorpayx_payout(
                fund_account_id=wallet.fund_account_id,
                amount_inr=amount,
                idempotency_key=idempotency_key
            )
            payout_id = rzp_payout.get("id")

            # Update payout with details
            payout.razorpay_payout_id = payout_id
            payout.status = "processing" if rzp_payout.get("status") in ("processing", "processed") else "queued"
            await payout.save()

            # Update transaction reference ID to Razorpay's payout ID
            txn.reference_id = payout_id
            await txn.save()

        except Exception as ex:
            log.error("razorpayx_payout_dispatch_failed", error=str(ex))
            # Reverse daily limit usage & mark failed
            wallet.daily_withdrawal_used -= amount
            await wallet.save()

            payout.status = "failed"
            payout.failure_reason = f"Gateway dispatch failed: {str(ex)}"
            await payout.save()

            txn.status = "failed"
            await txn.save()

        return payout

    async def p2p_transfer(
        self, sender_user_id: PydanticObjectId, receiver_email_or_phone: str, amount: Decimal, note: Optional[str] = None
    ) -> P2PTransfer:
        """
        Executes a user-to-user wallet transfer atomically within a single database transaction.
        No gateway fees applied.
        """
        if amount <= 0:
            raise ValueError("Transfer amount must be positive")

        # Find receiver user
        receiver = await User.find_one(
            Or(User.email == receiver_email_or_phone, User.phone == receiver_email_or_phone)
        )
        if not receiver:
            raise ValueError(f"Receiver user '{receiver_email_or_phone}' not found")

        if receiver.id == sender_user_id:
            raise ValueError("Cannot transfer wallet funds to yourself")

        client = get_mongo_client()
        async with await client.start_session() as session:
            async with session.start_transaction():
                # Fetch wallets
                sender_wallet = await self.get_or_create_wallet(sender_user_id, session=session)
                receiver_wallet = await self.get_or_create_wallet(receiver.id, session=session)

                if sender_wallet.status == "frozen":
                    raise ValueError("Your wallet is frozen")
                if receiver_wallet.status == "frozen":
                    raise ValueError("Receiver's wallet is frozen")

                # Verify sender available balance
                available = await self.get_available_balance(sender_wallet, session=session)
                if available < amount:
                    raise ValueError(f"Insufficient available balance. Required: {amount}, Available: {available}")

                # Unique idempotency key
                txn_time = int(datetime.utcnow().timestamp())
                sender_key = f"p2p_sent:{str(sender_user_id)}:{str(receiver.id)}:{amount}:{txn_time}"
                receiver_key = f"p2p_rcvd:{str(sender_user_id)}:{str(receiver.id)}:{amount}:{txn_time}"

                # Update sender balance
                sender_before = sender_wallet.balance
                sender_after = sender_before - amount
                sender_wallet.balance = sender_after
                sender_wallet.updated_at = datetime.utcnow()
                await sender_wallet.save(session=session)

                # Update receiver balance
                receiver_before = receiver_wallet.balance
                receiver_after = receiver_before + amount
                receiver_wallet.balance = receiver_after
                receiver_wallet.updated_at = datetime.utcnow()
                await receiver_wallet.save(session=session)

                # Create sender debit record
                sender_txn = WalletTransaction(
                    wallet_id=sender_wallet.id,
                    type="debit",
                    amount=amount,
                    balance_after=sender_after,
                    category="p2p_sent",
                    reference_id=sender_key,
                    status="success",
                    idempotency_key=sender_key,
                    metadata={"recipient_email": receiver.email, "note": note}
                )
                await sender_txn.insert(session=session)

                # Create receiver credit record
                receiver_txn = WalletTransaction(
                    wallet_id=receiver_wallet.id,
                    type="credit",
                    amount=amount,
                    balance_after=receiver_after,
                    category="p2p_received",
                    reference_id=receiver_key,
                    status="success",
                    idempotency_key=receiver_key,
                    metadata={"sender_email": receiver.email, "note": note}
                )
                await receiver_txn.insert(session=session)

                # Create P2PTransfer linkage
                p2p = P2PTransfer(
                    from_wallet_id=sender_wallet.id,
                    to_wallet_id=receiver_wallet.id,
                    amount=amount,
                    sender_txn_id=sender_txn.id,
                    receiver_txn_id=receiver_txn.id,
                    note=note,
                    status="completed"
                )
                await p2p.insert(session=session)

                # Audit logs for both
                sender_audit = WalletAuditLog(
                    wallet_id=sender_wallet.id,
                    user_id=sender_user_id,
                    action="P2P_SENT",
                    amount=amount,
                    balance_before=sender_before,
                    balance_after=sender_after,
                    performed_by="user",
                    metadata_json=f'{{"recipient": "{receiver.email}", "transfer_id": "{str(p2p.id)}"}}'
                )
                await sender_audit.insert(session=session)

                receiver_audit = WalletAuditLog(
                    wallet_id=receiver_wallet.id,
                    user_id=receiver.id,
                    action="P2P_RECEIVED",
                    amount=amount,
                    balance_before=receiver_before,
                    balance_after=receiver_after,
                    performed_by="user",
                    metadata_json=f'{{"sender": "{receiver.email}", "transfer_id": "{str(p2p.id)}"}}'
                )
                await receiver_audit.insert(session=session)

                log.info("p2p_transfer_success", sender=str(sender_user_id), receiver=str(receiver.id), amount=str(amount))
                return p2p


# Singleton instance
wallet_service = WalletService()
