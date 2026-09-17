# FuelFlux Backend — Wallet & Payments Integration Guide

This document describes how to configure, test, and run the production-grade, ledger-based user wallet system with Razorpay and RazorpayX Payouts.

---

## 1. Local Configuration

Ensure the following variables are set in your `.env` file (copied from `.env.example`):

```env
# Razorpay Standard (Add Money)
RAZORPAY_KEY_ID=rzp_test_T3I8ZwKy1bxCDx
RAZORPAY_KEY_SECRET=CKwiJsQ44xlxG7w0ZUpz3JDZ
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret_here

# RazorpayX (Withdrawal Payouts)
RAZORPAYX_ACCOUNT_NUMBER=7894320000000000
```

*Note: If `RAZORPAYX_ACCOUNT_NUMBER` is missing or keys are set to dummy/test credentials, the system automatically falls back to an internal mock simulation mode so development flows and tests do not block.*

---

## 2. API Endpoints

All endpoints are hosted under `/api/v1` and require standard JWT bearer authorization headers (except webhooks):

### Wallet Management
* **`GET`** `/user-wallet/balance` — Get current wallet balance (cached and available).
* **`GET`** `/user-wallet/transactions?page=1&limit=10` — Paginated passbook list of transactions.
* **`GET`** `/user-wallet/transactions/{id}` — Single transaction details.

### Add Money (Razorpay Standard)
* **`POST`** `/user-wallet/add-money/create-order` — Request order creation from backend.
  * *Payload:* `{"amount": 1000.00}`
* **`POST`** `/user-wallet/add-money/verify` — Verify gateway signature and credit balance immediately.
  * *Payload:* `{"razorpay_order_id": "...", "razorpay_payment_id": "...", "razorpay_signature": "..."}`

### Internal Transactions & Transfers
* **`POST`** `/user-wallet/spend` — Spend balance atomically for internal services.
  * *Payload:* `{"amount": 500.00, "category": "subscription", "reference_id": "sub_ref_123", "metadata": {}}`
* **`POST`** `/user-wallet/p2p-transfer` — Atomically send funds to another user by email or phone.
  * *Payload:* `{"receiver_email_or_phone": "receiver@fuelflux.com", "amount": 150.00, "note": "Lunch share"}`

### Bank Withdrawals (RazorpayX IMPS Payouts)
* **`POST`** `/user-wallet/withdraw` — Request withdrawal to a bank account (Limit: min ₹100, max ₹20,000/day).
  * *Payload:*
    ```json
    {
      "amount": 5000.00,
      "bank_details": {
        "account_holder": "Rajesh Kumar",
        "account_number": "50100412345678",
        "ifsc": "HDFC0000001",
        "bank_name": "HDFC Bank"
      }
    }
    ```

### Public Webhooks
* **`POST`** `/webhooks/razorpay/payment` — Captures `payment.captured` events to credit wallet balance.
* **`POST`** `/webhooks/razorpay/payout` — Captures `payout.processed`, `payout.failed`, and `payout.reversed` events.

---

## 3. Webhook Testing Guide

Since webhooks are triggered from Razorpay's cloud nodes, your local server must be exposed to the internet.

### Option A: Using `ngrok`
1. Install ngrok and expose your FastAPI port:
   ```bash
   ngrok http 8000
   ```
2. Copy the generated public URL (e.g., `https://abcd-12-34.ngrok-free.app`).
3. Set the Webhook URL in your Razorpay Dashboard:
   * **Payment Webhook:** `https://<ngrok-url>/api/v1/webhooks/razorpay/payment`
     * *Active Events:* `payment.captured`
   * **Payout Webhook:** `https://<ngrok-url>/api/v1/webhooks/razorpay/payout`
     * *Active Events:* `payout.processed`, `payout.failed`, `payout.reversed`

### Option B: Local Webhook Signature Bypass
For developer convenience, you can simulate/bypass the webhook signature check locally by setting the header:
* `X-Razorpay-Signature: mock_signature_bypass`

This allows you to post simulated events directly using tools like Postman or `curl` to verify transactional flow logic without setting up Razorpay portal triggers.

---

## 4. Running Tests

Run the test suite using pytest to verify signature logic, daily limit validations, and data models:

```bash
pytest tests/test_user_wallet.py
```