"""
FILE: src/api/v1/sales.py
Fully migrated to async Beanie (MongoDB ODM).

ENDPOINTS:
  POST   /sales/shifts/start          — Start new shift
  POST   /sales/shifts/end            — End shift
  GET    /sales/shifts/last           — Get last shift for a pump
  GET    /sales/shifts/active         — Check active shift
  GET    /sales/shifts/{shift_id}/summary — Real-time shift summary
  GET    /sales/shifts/{shift_id}/point-readings
  POST   /sales/logs                  — Add sale log (single)
  POST   /sales/logs/bulk             — Add sale logs (batch)
  GET    /sales/logs                  — List/filter sale logs
  DELETE /sales/logs/{log_id}         — Soft delete a sale log
  PATCH  /sales/rates                 — Update item rate mid-shift
  GET    /sales/overview              — Overview dashboard data
  GET    /sales/attendants            — List attendants for pump
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from beanie import PydanticObjectId
from beanie.operators import In
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Optional
import os, re, uuid

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.attendant import Attendant
from src.db.models.vehicle import Vehicle
from src.db.models.credit_usage import CreditUsage
from src.db.models.credit_request import CreditRequest
from src.db.models.payment import PaymentRequest

from src.db.models.sales import (
    Shift, ShiftPersonnel, ShiftPoint, SaleLog,
    ShiftStatus, PaymentMode as PaymentModeEnum, SaleType as SaleTypeEnum
)
from src.db.models.udhaar import (
    UdhaarTransaction, UdhaarContract, UdhaarVehicle, ContractStatus
)

from src.db.schemas.sales_schemas import (
    StartShiftRequest, ShiftResponse,
    EndShiftRequest,
    SaleLogCreate, SaleLogResponse,
    ShiftSummaryResponse, PointSummary, PaymentSummary, ItemSummary,
    OverviewResponse, ItemRateUpdate
)
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/sales", tags=["sales"])

# ─────────────────────────────────────────────────────────────────
# OCR — EasyOCR global reader (lazy-init on first use, CPU-only)
# Initialized once per worker process; ~2-4s cold start, then fast.
# ─────────────────────────────────────────────────────────────────
_ocr_reader = None

def _get_ocr_reader():
    """Lazy-initialize the EasyOCR reader. CPU mode, English only."""
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
            _ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            log.info("EasyOCR reader initialized successfully")
        except ImportError:
            log.error("easyocr not installed. Run: pip install easyocr")
            raise RuntimeError("EasyOCR not installed on server")
    return _ocr_reader


def _parse_receipt_text(lines: list[str]) -> dict:
    """
    Smart multi-line OCR parser for fuel station credit slips.

    EasyOCR on thermal receipts often splits EVERY word onto its own line,
    producing 60+ lines for a short slip. Strategy:
      - Build sliding N-line windows so labels and values separated across
        lines can still be matched together.
      - Use a "label → next-line value" lookup for named fields.
      - Validate Indian vehicle state codes to correct common OCR char errors.

    Extracts: amount, vehicle_no, customer_name_suggested.
    Never raises — all fields default to None if not found.
    """
    log.info("Parsing OCR lines", total_lines=len(lines))
    amount: Optional[float] = None
    vehicle_no: Optional[str] = None
    customer_name: Optional[str] = None

    # ── Clean & build text representations ───────────────────────────────────
    cleaned_lines = [l.strip() for l in lines if l.strip()]
    n = len(cleaned_lines)

    # full_text: all lines joined — used only for amount (safe, no bleed risk)
    full_text = " ".join(cleaned_lines)

    # windows[i] = cleaned_lines[i..i+W-1] joined — catches multi-line fields
    WINDOW = 4
    windows = [
        " ".join(cleaned_lines[i: i + WINDOW])
        for i in range(n)
    ]

    # ── 1. AMOUNT PARSING ─────────────────────────────────────────────────────
    amt_keywords_rx = r"(?:rs\.?|inr|amount|amt|total|net|sale|value|charge|payment|₹)"
    number_rx = r"([\d,]+(?:\.\d{1,2})?)"
    pattern_kf = re.compile(amt_keywords_rx + r"\s*[^0-9a-zA-Z]{0,6}\s*" + number_rx, re.IGNORECASE)
    pattern_nf = re.compile(number_rx + r"\s*(?:rs\.?|inr|₹)", re.IGNORECASE)

    skip_amt_kw = ["rate", "qty", "volume", "ltr", "liter", "price", "tel", "receipt", "fcc", "fip"]

    # Pass 1: line-by-line
    for line in cleaned_lines:
        if any(w in line.lower() for w in skip_amt_kw):
            continue
        for pat in (pattern_kf, pattern_nf):
            m = pat.search(line)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if 10 <= val <= 500000:
                        amount = val
                        log.info("Amount matched line-by-line", line=line, amount=amount)
                        break
                except ValueError:
                    pass
        if amount:
            break

    # Pass 2: 4-line window (label and value may span lines)
    if not amount:
        for win in windows:
            if any(w in win.lower() for w in ["rate", "qty", "volume", "ltr", "liter", "price"]):
                continue
            m = pattern_kf.search(win)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if 10 <= val <= 500000:
                        amount = val
                        log.info("Amount matched window", amount=amount)
                        break
                except ValueError:
                    pass

    # Pass 3: standalone number fallback
    if not amount:
        for num_str in re.findall(r"\b\d{2,5}(?:\.\d{1,2})?\b", full_text):
            try:
                val = float(num_str)
                if 50 <= val <= 30000:
                    idx = full_text.find(num_str)
                    ctx = full_text[max(0, idx - 20): idx + len(num_str) + 20].lower()
                    if any(x in ctx for x in ["id", "tel", "receipt", "no.", "fcc", "date", "time", "nozzle", "fip"]):
                        continue
                    amount = val
                    log.info("Amount matched fallback standalone", amount=amount)
                    break
            except ValueError:
                pass

    # ── 2. VEHICLE NUMBER PARSING ─────────────────────────────────────────────
    # Indian plates: STATE(2) DISTRICT(2 digits) ALPHA(1-3) NUMBER(4 digits)
    # OCR common char errors: O↔0, I↔1, S↔5/3, B↔8, H↔E/F, Z↔2
    #
    # Valid Indian 2-letter state/UT codes (for post-extraction correction)
    VALID_STATE_CODES = {
        "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA",
        "GJ", "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH",
        "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY", "RJ",
        "SK", "TG", "TN", "TR", "UK", "UP", "WB",
    }
    # Common single-char OCR confusions in state codes (both directions)
    CHAR_CONFUSION = {
        "E": "H", "F": "P", "0": "D", "1": "I", "6": "G", "8": "B",
    }

    def _correct_state(st: str) -> str:
        """Try to correct a 2-char OCR state code to a valid Indian state code."""
        if st in VALID_STATE_CODES:
            return st
        # Try flipping each char using confusion map
        for i in range(2):
            corrected = list(st)
            if corrected[i] in CHAR_CONFUSION:
                corrected[i] = CHAR_CONFUSION[corrected[i]]
                candidate = "".join(corrected)
                if candidate in VALID_STATE_CODES:
                    log.info("State code corrected via OCR confusion map", original=st, corrected=candidate)
                    return candidate
        return st  # return as-is if no correction found

    veh_pattern = re.compile(
        r"\b([A-Z]{2})[\s\-]*([0-9O]{1,2})[\s\-]*([A-Z0-9IO135]{1,3})[\s\-]*([0-9O]{4})\b",
        re.IGNORECASE,
    )

    def _normalize_vehicle(vm_match) -> str:
        state = _correct_state(vm_match.group(1).upper())
        dist = vm_match.group(2).upper().replace("O", "0")
        mid = (vm_match.group(3).upper()
               .replace("0", "O").replace("1", "I")
               .replace("5", "S").replace("8", "B").replace("3", "S"))
        num = (vm_match.group(4).upper()
               .replace("O", "0").replace("I", "1")
               .replace("Z", "2").replace("S", "5").replace("B", "8"))
        return f"{state}{dist}{mid}{num}"

    # Label patterns that explicitly mark a vehicle number field
    veh_label_rx = re.compile(
        r"(?:veh(?:icle)?[\s\.]*no\.?|reg(?:istration)?[\s\.]*no\.?)\s*[:\-]?\s*"
        r"([A-Z]{2}[\s\-]*[0-9O]{1,2}[\s\-]*[A-Z0-9IO1358]{1,3}[\s\-]*[0-9O]{4})",
        re.IGNORECASE,
    )
    # Label-only pattern (label and value may be on different lines)
    veh_label_only_rx = re.compile(
        r"^(?:veh(?:icle)?[\s\.]*(?:no\.?)?|reg(?:istration)?[\s\.]*no\.?)$",
        re.IGNORECASE,
    )

    # Pass 1: label + value on same/adjacent lines (4-line window)
    for win in windows:
        m = veh_label_rx.search(win)
        if m:
            vm = veh_pattern.search(m.group(1))
            if vm:
                vehicle_no = _normalize_vehicle(vm)
                log.info("Vehicle number matched via VEH NO label (window)", raw=m.group(0), normalized=vehicle_no)
                break

    # Pass 2: label on one line, value on next 1-2 lines
    if not vehicle_no:
        for i, line in enumerate(cleaned_lines):
            # Check if this line IS the label (e.g. "VEH NO" or "VEH NO :")
            label_check = re.search(
                r"(?:veh(?:icle)?[\s\.]*no\.?|reg(?:istration)?[\s\.]*no\.?)",
                line, re.IGNORECASE
            )
            if label_check:
                # Look for plate in next 3 lines
                for j in range(i + 1, min(i + 4, n)):
                    vm = veh_pattern.search(cleaned_lines[j])
                    if vm:
                        vehicle_no = _normalize_vehicle(vm)
                        log.info("Vehicle number found on next line after label",
                                 label_line=line, value_line=cleaned_lines[j], normalized=vehicle_no)
                        break
                if vehicle_no:
                    break

    # Pass 3: skip tel/receipt/fcc lines and match any plate pattern
    if not vehicle_no:
        skip_rx = re.compile(r"(?:tel|telephone|phone|receipt|fcc|fip|nozzle|lst|vat)[\s\.]*no\.?", re.IGNORECASE)
        for line in cleaned_lines:
            if skip_rx.search(line):
                continue
            vm = veh_pattern.search(line)
            if vm:
                vehicle_no = _normalize_vehicle(vm)
                log.info("Vehicle number matched fallback (line scan)", raw=vm.group(0), normalized=vehicle_no)
                break

    # ── 3. CUSTOMER NAME SUGGESTION ───────────────────────────────────────────
    # OCR splits every word to its own line.
    # Strategy: find the line containing the label keyword, then take the
    # text value from the NEXT non-empty line (or same line if inline).
    #
    LABEL_NAME_KW = re.compile(
        r"^(?:customer[\s]*name|party[\s]*name|customer|party|client|firm|name)[\s:.\-]*$",
        re.IGNORECASE,
    )
    LABEL_NAME_INLINE = re.compile(
        r"(?:customer[\s]*name|party[\s]*name)\s*[:\-\.]*\s*([A-Za-z]\w*)",
        re.IGNORECASE,
    )
    NAME_BLACKLIST = {
        "NAME", "NO", "YES", "VOID", "SLIP", "VEH", "BIKE", "CAR", "TRUCK",
        "DATE", "TIME", "CASH", "CREDIT", "MODE", "PRODUCT", "NOZZLE",
        "VOLUME", "RATE", "AMOUNT", "TOTAL", "AVAILABLE", "ATTENDENT",
        "ATTENDANT", "WELCOME", "RECEIPT", "SAVE", "FUEL", "THANKS",
    }

    def _is_valid_name(word: str) -> bool:
        if len(word) < 3:
            return False
        if re.match(r"^\d+$", word):
            return False
        if word.upper() in NAME_BLACKLIST:
            return False
        return True

    # Pass 1: Inline label+value on same line ("CUSTOMER NAME: Ramdhyal")
    for line in cleaned_lines:
        m = LABEL_NAME_INLINE.search(line)
        if m:
            candidate = m.group(1).strip()
            if _is_valid_name(candidate):
                customer_name = candidate
                log.info("Customer name matched inline label", customer_name=customer_name)
                break

    # Pass 2: Label-only line → value on next 1-3 lines
    # Handles OCR split: line[i]="CUSTOMER", line[i+1]="NAME:", line[i+2]="Ramdhyal"
    if not customer_name:
        for i, line in enumerate(cleaned_lines):
            # Check if this line (alone or joined with next) looks like a name label
            is_label = bool(LABEL_NAME_KW.match(line.strip()))
            # Also check 2-line combo: "CUSTOMER" + "NAME:" → "CUSTOMER NAME:"
            is_split_label = False
            if not is_label and i + 1 < n:
                combo = (line + " " + cleaned_lines[i + 1]).strip()
                is_split_label = bool(LABEL_NAME_KW.match(combo))

            if is_label or is_split_label:
                # The value starts on the next line (or 2 lines ahead if split)
                value_start = i + 1 if is_label else i + 2
                for j in range(value_start, min(value_start + 3, n)):
                    candidate = cleaned_lines[j].strip()
                    # Take first word only to avoid bleeding into the next field
                    first_word = candidate.split()[0] if candidate.split() else ""
                    if _is_valid_name(first_word):
                        customer_name = first_word
                        log.info("Customer name found after label line",
                                 label=line, value_line=candidate, customer_name=customer_name)
                        break
                if customer_name:
                    break

    # Pass 3: Broader keyword search in 4-line windows
    if not customer_name:
        broad_label_rx = re.compile(
            r"(?:customer|party|client|firm|transport|company)\s*[^a-zA-Z0-9]{0,6}\s*([A-Za-z]\w+)",
            re.IGNORECASE,
        )
        for win in windows:
            m = broad_label_rx.search(win)
            if m:
                candidate = m.group(1).strip()
                if _is_valid_name(candidate):
                    customer_name = candidate
                    log.info("Customer name matched broad keyword (window)", customer_name=customer_name)
                    break

    # Pass 4: Fallback — first line that looks like a proper name
    if not customer_name:
        name_skip_kw = [
            "welcome", "receipt", "invoice", "payment", "cash", "credit",
            "phone", "tel", "nozzle", "shift", "save", "fuel", "thanks",
            "toll", "product", "mode", "veh", "type", "date", "time",
            "volume", "ltr", "rate", "amount", "total", "fcc", "fip",
            "attendent", "attendant", "available",
        ]
        for line in cleaned_lines:
            lw = line.lower()
            if (len(line) >= 4
                    and not re.search(r"\d", line)
                    and not any(kw in lw for kw in name_skip_kw)
                    and re.match(r"^[A-Za-z][A-Za-z\s\.]+$", line)):
                first_word = line.strip().split()[0]
                if _is_valid_name(first_word):
                    customer_name = first_word
                    log.info("Customer name matched fallback line", customer_name=customer_name)
                    break

    return {
        "total_amount": amount,
        "vehicle_no": vehicle_no,
        "customer_name_suggested": customer_name,
    }



# ─────────────────────────────────────────────────────────────────

# HELPER: ownership check
# ─────────────────────────────────────────────────────────────────
async def _get_pump_or_403(pump_id: str, owner_id: PydanticObjectId) -> Pump:
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id")
    pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == owner_id)
    if not pump:
        raise HTTPException(status_code=403, detail="Not authorized for this pump")
    return pump


# ─────────────────────────────────────────────────────────────────
# HELPER: build shift summary dict
# ─────────────────────────────────────────────────────────────────
async def _build_summary(shift: Shift) -> dict:
    logs = await SaleLog.find(
        SaleLog.shift_id == shift.id,
        SaleLog.is_deleted == False
    ).to_list()

    points = await ShiftPoint.find(ShiftPoint.shift_id == shift.id).to_list()

    net_amount = sum(l.amount for l in logs)
    total_qty = sum(l.quantity for l in logs)
    credit_amount = sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.credit)
    credit_ratio = round((credit_amount / net_amount * 100) if net_amount else 0, 2)

    # Point summaries
    point_summaries = []
    for pt in points:
        if not pt.is_active:
            continue
        end_r = pt.end_reading or 0.0
        sold_qty = max(0, (end_r - pt.start_reading) - pt.testing_value)
        pt_logs = [l for l in logs if str(l.nozzle_id) == str(pt.nozzle_id)]
        pt_amount = sum(l.amount for l in pt_logs)
        point_summaries.append({
            "nozzle_id": pt.nozzle_id,
            "item_name": pt.item_name,
            "start_reading": pt.start_reading,
            "end_reading": pt.end_reading,
            "testing_value": pt.testing_value,
            "sold_quantity": round(sold_qty, 3),
            "total_amount": round(pt_amount, 2),
        })

    # Payment summaries
    pay_map: dict = defaultdict(lambda: {"total_amount": 0.0, "transaction_count": 0})
    for l in logs:
        pay_map[l.payment_mode.value]["total_amount"] += l.amount
        pay_map[l.payment_mode.value]["transaction_count"] += 1
    payment_summaries = [
        {"payment_mode": k, "total_amount": round(v["total_amount"], 2),
         "transaction_count": v["transaction_count"]}
        for k, v in pay_map.items()
    ]

    # Item summaries
    item_map: dict = defaultdict(lambda: {"total_quantity": 0.0, "total_amount": 0.0})
    for l in logs:
        item_map[l.item_name]["total_quantity"] += l.quantity
        item_map[l.item_name]["total_amount"] += l.amount
    item_summaries = [
        {"item_name": k, "total_quantity": round(v["total_quantity"], 3),
         "total_amount": round(v["total_amount"], 2)}
        for k, v in item_map.items()
    ]

    return {
        "shift_id": str(shift.id),
        "pump_id": str(shift.pump_id),
        "shift_type": shift.shift_type,
        "status": shift.status,
        "start_time": shift.start_time,
        "end_time": shift.end_time,
        "net_amount": round(net_amount, 2),
        "total_quantity": round(total_qty, 3),
        "credit_ratio": credit_ratio,
        "point_summaries": point_summaries,
        "payment_summaries": payment_summaries,
        "item_summaries": item_summaries,
        "total_sales_count": len(logs),
    }


# ═════════════════════════════════════════════════════════════════
# SHIFT ENDPOINTS
# ═════════════════════════════════════════════════════════════════

@router.post("/shifts/start", status_code=201)
async def start_shift(
    payload: StartShiftRequest,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Start a new shift for a pump.
    - Checks no active shift already running for same pump
    - Auto-fetches start_reading from previous shift's end_reading if not provided
    - Creates ShiftPoint records for each nozzle
    - Links selected attendants as ShiftPersonnel
    """
    pump = await _get_pump_or_403(payload.pump_id, current_user.id)
    pump_oid = pump.id

    # Block duplicate active shift
    existing_active = await Shift.find_one(
        Shift.pump_id == pump_oid,
        Shift.status == ShiftStatus.active
    )
    if existing_active:
        raise HTTPException(
            status_code=400,
            detail=f"Shift {str(existing_active.id)} is already active for this pump. End it first."
        )

    # Create shift
    shift = Shift(
        pump_id=pump_oid,
        shift_type=payload.shift_type,
        status=ShiftStatus.active,
        start_time=payload.start_time,
        created_by=current_user.id,
    )
    await shift.insert()

    # For each nozzle point: try to auto-fetch start_reading from last closed shift
    for pt_in in payload.point_readings:
        start_val = pt_in.start_reading

        if start_val == 0:
            # Find last closed shift point for this nozzle
            last_shifts = await Shift.find(
                Shift.pump_id == pump_oid,
                Shift.status == ShiftStatus.closed
            ).sort(-Shift.end_time).to_list()

            for ls in last_shifts:
                last_pt = await ShiftPoint.find_one(
                    ShiftPoint.shift_id == ls.id,
                    ShiftPoint.nozzle_id == pt_in.nozzle_id,
                )
                if last_pt and last_pt.end_reading is not None:
                    start_val = last_pt.end_reading
                    break

        sp = ShiftPoint(
            shift_id=shift.id,
            nozzle_id=pt_in.nozzle_id,
            item_name=pt_in.item_name,
            start_reading=start_val,
            testing_value=pt_in.testing_value,
            is_active=pt_in.is_active,
        )
        await sp.insert()

    # Link personnel
    for att_id_str in payload.personnel_ids:
        try:
            att_oid = PydanticObjectId(att_id_str)
            sp_link = ShiftPersonnel(shift_id=shift.id, attendant_id=att_oid)
            await sp_link.insert()
        except Exception as e:
            log.warning("Failed to add personnel", att_id=att_id_str, error=str(e))

    return {
        "id": str(shift.id),
        "pump_id": str(shift.pump_id),
        "shift_type": shift.shift_type,
        "status": shift.status,
        "start_time": shift.start_time,
        "message": "Shift started successfully"
    }


@router.post("/shifts/end")
async def end_shift(
    payload: EndShiftRequest,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    End an active shift.
    - Updates end_reading for each nozzle point
    - Changes shift status to closed
    - Returns final summary
    """
    try:
        shift_oid = PydanticObjectId(payload.shift_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shift_id")

    shift = await Shift.get(shift_oid)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")

    await _get_pump_or_403(str(shift.pump_id), current_user.id)

    if shift.status == ShiftStatus.closed:
        raise HTTPException(status_code=400, detail="Shift is already closed")

    # Validate end readings > start readings
    for er in payload.end_readings:
        pt = await ShiftPoint.find_one(
            ShiftPoint.shift_id == shift.id,
            ShiftPoint.nozzle_id == er.nozzle_id
        )
        if not pt:
            raise HTTPException(
                status_code=404,
                detail=f"Nozzle {er.nozzle_id} not found in this shift"
            )
        if er.end_reading < pt.start_reading:
            raise HTTPException(
                status_code=400,
                detail=f"Nozzle {er.nozzle_id}: end_reading ({er.end_reading}) cannot be less than start_reading ({pt.start_reading})"
            )
        pt.end_reading = er.end_reading
        if er.testing_value is not None:
            pt.testing_value = er.testing_value
        await pt.save()

    shift.status = ShiftStatus.closed
    shift.end_time = datetime.utcnow()
    shift.updated_at = datetime.utcnow()
    await shift.save()

    return await _build_summary(shift)


@router.get("/shifts/last")
async def get_last_shift(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Get the most recently modified shift for a pump (active or closed).
    """
    pump = await _get_pump_or_403(pump_id, current_user.id)

    shift = await Shift.find(
        Shift.pump_id == pump.id
    ).sort(-Shift.updated_at).first_or_none()

    if not shift:
        raise HTTPException(status_code=404, detail="No shifts found for this pump")

    return await _build_summary(shift)


@router.get("/shifts/active")
async def get_active_shift(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Check if pump has an active shift and return it."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    shift = await Shift.find_one(
        Shift.pump_id == pump.id,
        Shift.status == ShiftStatus.active
    )

    if not shift:
        return {"active": False, "shift": None}

    return {"active": True, "shift": await _build_summary(shift)}


@router.get("/shifts/{shift_id}/summary")
async def get_shift_summary(
    shift_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Real-time shift summary — 'See Summary' button."""
    try:
        oid = PydanticObjectId(shift_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shift_id")
    shift = await Shift.get(oid)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")

    await _get_pump_or_403(str(shift.pump_id), current_user.id)
    return await _build_summary(shift)


@router.get("/shifts/{shift_id}/point-readings")
async def get_shift_points(
    shift_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Get nozzle/point readings for a shift."""
    try:
        oid = PydanticObjectId(shift_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shift_id")
    shift = await Shift.get(oid)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    await _get_pump_or_403(str(shift.pump_id), current_user.id)

    points = await ShiftPoint.find(ShiftPoint.shift_id == shift.id).to_list()

    return [
        {
            "id": str(pt.id),
            "nozzle_id": pt.nozzle_id,
            "item_name": pt.item_name,
            "start_reading": pt.start_reading,
            "end_reading": pt.end_reading,
            "testing_value": pt.testing_value,
            "is_active": pt.is_active,
            "sold_quantity": round(
                max(0, ((pt.end_reading or pt.start_reading) - pt.start_reading) - pt.testing_value), 3
            ),
        }
        for pt in points
    ]


async def _process_udhaar_credit_transaction(
    customer_id: PydanticObjectId,
    pump_id: PydanticObjectId,
    amount: float,
    quantity: float,
    item_name: str,
    vehicle_number: Optional[str] = None,
    slip_ref: Optional[str] = None,
) -> bool:
    """
    When payment_mode=credit AND customer_id belongs to an UdhaarCustomer,
    this helper:
      1. Finds the customer's active UdhaarContract.
      2. Validates credit limit is not exceeded.
      3. Increments contract.current_spend.
      4. Creates an UdhaarTransaction record.
    Returns True if credit was recorded, False otherwise.
    """
    try:
        from src.db.models.udhaar import UdhaarCustomer as _UC
        customer = await _UC.find_one(_UC.id == customer_id, _UC.deleted_at == None)
        if not customer:
            return False  # Not a registered Udhaar customer

        contract = await UdhaarContract.find_one(
            UdhaarContract.customer_id == customer_id,
            UdhaarContract.status == ContractStatus.active,
        )
        if not contract:
            return False  # No active contract

        # Guard: do not exceed credit limit
        remaining = contract.total_credit_limit - contract.current_spend
        if remaining < amount:
            log.warning(
                "Udhaar credit limit would be exceeded — not recording",
                customer_id=str(customer_id),
                remaining=remaining,
                requested=amount,
            )
            return False

        # Find matching UdhaarVehicle (optional — soft link)
        udhaar_vehicle_id = None
        if vehicle_number:
            clean = re.sub(r'[^A-Z0-9]', '', vehicle_number.upper())
            udhaar_veh = await UdhaarVehicle.find_one(
                UdhaarVehicle.customer_id == customer_id,
                UdhaarVehicle.deleted_at == None,
            )
            # Try to match by plate
            all_vehs = await UdhaarVehicle.find(
                UdhaarVehicle.customer_id == customer_id,
                UdhaarVehicle.deleted_at == None,
            ).to_list()
            for vc in all_vehs:
                if re.sub(r'[^A-Z0-9]', '', vc.number_plate.upper()) == clean:
                    udhaar_vehicle_id = vc.id
                    break

        # Update contract spend
        contract.current_spend = round(contract.current_spend + amount, 2)
        contract.current_slips_used = contract.current_slips_used + 1
        contract.updated_at = datetime.utcnow()
        await contract.save()

        # Create transaction record
        txn = UdhaarTransaction(
            contract_id=contract.id,
            customer_id=customer_id,
            vehicle_id=udhaar_vehicle_id,
            pump_id=pump_id,
            item_name=item_name,
            quantity=quantity,
            amount=amount,
            slip_number=slip_ref,
        )
        await txn.insert()

        log.info(
            "Udhaar credit transaction recorded",
            customer_id=str(customer_id),
            contract_id=str(contract.id),
            amount=amount,
            new_spend=contract.current_spend,
        )
        return True
    except Exception as e:
        log.error("_process_udhaar_credit_transaction failed", error=str(e))
        return False


async def _process_logistic_auto_credit(
    vehicle_number: str,
    amount: float,
    pump_id: PydanticObjectId,
    attendant_id: Optional[PydanticObjectId],
    item_name: str,
    quantity: float,
    sale_log_id: PydanticObjectId,
    is_bulk: bool = False
) -> tuple[bool, Optional[dict]]:
    try:
        clean_plate = re.sub(r'[^A-Z0-9]', '', vehicle_number.upper())
        if not clean_plate:
            return False, None

        # Find the vehicle matching the plate (space and hyphen insensitive)
        vehicles = await Vehicle.find(Vehicle.is_active == True).to_list()
        vehicle = None
        for v in vehicles:
            if re.sub(r'[^A-Z0-9]', '', v.vehicle_plate.upper()) == clean_plate:
                vehicle = v
                break

        # Check 1: approved, unused fuel voucher matching this vehicle number/id and pump
        vouchers = await PaymentRequest.find(
            PaymentRequest.payment_type == "fuel_voucher",
            PaymentRequest.status == "approved",
            PaymentRequest.pump_id == pump_id
        ).to_list()

        matched_voucher = None
        for v in vouchers:
            form_data = v.logistic_form_data or {}
            v_plate = form_data.get("vehicle_plate", "")
            if re.sub(r'[^A-Z0-9]', '', v_plate.upper()) == clean_plate:
                # Expiry check
                expiry_str = form_data.get("expiry_date", "")
                if expiry_str:
                    try:
                        expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
                        if expiry_dt.date() < datetime.utcnow().date():
                            continue  # Expired
                    except Exception:
                        pass
                matched_voucher = v
                break

        if matched_voucher:
            # Redeem voucher
            matched_voucher.status = "used"
            matched_voucher.reviewed_at = datetime.utcnow()
            await matched_voucher.save()

            # Create credit usage
            partner_id = matched_voucher.logistic_partner_id
            usage = CreditUsage(
                logistic_partner_id=partner_id,
                vehicle_id=vehicle.id if vehicle else None,
                pump_id=pump_id,
                attendant_id=attendant_id,
                voucher_id=matched_voucher.id,
                sale_log_id=sale_log_id,
                fuel_type=item_name,
                volume=quantity,
                amount=amount,
                status="settled",
                remarks=f"Redeemed Voucher Ref {str(matched_voucher.id)[-6:]} (Sale Log: {str(sale_log_id)})"
            )
            await usage.insert()

            return True, {
                "type": "voucher",
                "voucher_id": str(matched_voucher.id),
                "partner_id": str(partner_id),
                "deducted": amount,
                "credit_usage_id": str(usage.id),
            }

        # Check 2: active credit request
        if vehicle:
            active_credit_req = await CreditRequest.find_one(
                CreditRequest.pump_id == pump_id,
                CreditRequest.status == "active",
                In(CreditRequest.vehicle_ids, [vehicle.id])
            )
            if not active_credit_req:
                active_credit_req = await CreditRequest.find_one(
                    CreditRequest.pump_id == pump_id,
                    CreditRequest.status == "active",
                    CreditRequest.vehicle_id == vehicle.id
                )

            if active_credit_req:
                partner_id = vehicle.partner_id
                vids = getattr(active_credit_req, "vehicle_ids", []) or [active_credit_req.vehicle_id]
                contract_vehicles = await Vehicle.find(
                    In(Vehicle.id, vids),
                    Vehicle.partner_id == partner_id,
                    Vehicle.is_active == True
                ).to_list()

                total_outstanding = sum(v.outstanding_amount for v in contract_vehicles)
                contract_limit = active_credit_req.approved_limit or 0.0
                available_credit = contract_limit - total_outstanding

                if available_credit >= amount:
                    vehicle.outstanding_amount = round(vehicle.outstanding_amount + amount, 2)
                    vehicle.updated_at = datetime.utcnow()
                    await vehicle.save()

                    usage = CreditUsage(
                        logistic_partner_id=partner_id,
                        vehicle_id=vehicle.id,
                        pump_id=pump_id,
                        attendant_id=attendant_id,
                        credit_request_id=active_credit_req.id,
                        sale_log_id=sale_log_id,
                        fuel_type=item_name,
                        volume=quantity,
                        amount=amount,
                        status="pending",
                        remarks=f"{'Bulk auto-deducted' if is_bulk else 'Auto-deducted'} from sale log {str(sale_log_id)}",
                    )
                    await usage.insert()

                    return True, {
                        "type": "credit",
                        "vehicle_id": str(vehicle.id),
                        "partner_id": str(partner_id),
                        "credit_limit": contract_limit,
                        "deducted": amount,
                        "new_outstanding": vehicle.outstanding_amount,
                        "credit_usage_id": str(usage.id),
                    }
                else:
                    log.warning(
                        "Credit limit exceeded — deduction skipped",
                        plate=vehicle.vehicle_plate,
                        available=available_credit,
                        requested=amount,
                    )
                    return False, {
                        "vehicle_id": str(vehicle.id),
                        "error": "insufficient_credit",
                        "available_credit": round(available_credit, 2),
                        "requested": amount,
                    }
    except Exception as credit_exc:
        log.error("Credit/Voucher processing failed", error=str(credit_exc))
    return False, None



@router.get("/check-vehicle")
async def check_vehicle_payment_info(
    vehicle_plate: str = Query(...),
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Check if a vehicle plate number has an active credit line or approved fuel voucher at this pump.
    """
    try:
        pump_id_oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump_id format")

    await _get_pump_or_403(pump_id, current_user.id)

    clean_plate = re.sub(r'[^A-Z0-9]', '', vehicle_plate.upper())
    if not clean_plate:
        return {"status": "none"}

    # Find the vehicle matching the plate (space and hyphen insensitive)
    vehicles = await Vehicle.find(Vehicle.is_active == True).to_list()
    vehicle = None
    for v in vehicles:
        if re.sub(r'[^A-Z0-9]', '', v.vehicle_plate.upper()) == clean_plate:
            vehicle = v
            break

    # Look for approved, unused fuel vouchers for this vehicle at this pump
    vouchers = await PaymentRequest.find(
        PaymentRequest.payment_type == "fuel_voucher",
        PaymentRequest.status == "approved",
        PaymentRequest.pump_id == pump_id_oid
    ).to_list()

    matched_voucher = None
    for v in vouchers:
        form_data = v.logistic_form_data or {}
        v_plate = form_data.get("vehicle_plate", "")
        if re.sub(r'[^A-Z0-9]', '', v_plate.upper()) == clean_plate:
            expiry_str = form_data.get("expiry_date", "")
            if expiry_str:
                try:
                    expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
                    if expiry_dt.date() < datetime.utcnow().date():
                        continue  # Expired
                except Exception:
                    pass
            matched_voucher = v
            break

    if matched_voucher:
        partner = await User.get(matched_voucher.logistic_partner_id)
        return {
            "status": "voucher",
            "voucher_id": str(matched_voucher.id),
            "amount": matched_voucher.amount,
            "notes": (matched_voucher.logistic_form_data or {}).get("notes", ""),
            "vehicle_id": str(vehicle.id) if vehicle else None,
            "partner_name": partner.company_name if partner else "Logistic Partner",
            "vehicle_plate_normalized": vehicle.vehicle_plate if vehicle else matched_voucher.logistic_form_data.get("vehicle_plate", vehicle_plate)
        }

    # Look for active credit request
    if vehicle:
        active_credit_req = await CreditRequest.find_one(
            CreditRequest.pump_id == pump_id_oid,
            CreditRequest.status == "active",
            In(CreditRequest.vehicle_ids, [vehicle.id])
        )
        if not active_credit_req:
            active_credit_req = await CreditRequest.find_one(
                CreditRequest.pump_id == pump_id_oid,
                CreditRequest.status == "active",
                CreditRequest.vehicle_id == vehicle.id
            )

        if active_credit_req:
            partner = await User.get(vehicle.partner_id)
            vids = getattr(active_credit_req, "vehicle_ids", []) or [active_credit_req.vehicle_id]
            contract_vehicles = await Vehicle.find(
                In(Vehicle.id, vids),
                Vehicle.partner_id == vehicle.partner_id,
                Vehicle.is_active == True
            ).to_list()

            total_outstanding = sum(v.outstanding_amount for v in contract_vehicles)
            contract_limit = active_credit_req.approved_limit or 0.0
            available_credit = max(0.0, contract_limit - total_outstanding)

            return {
                "status": "credit",
                "available_credit": available_credit,
                "partner_name": partner.company_name if partner else "Logistic Partner",
                "vehicle_id": str(vehicle.id),
                "vehicle_plate_normalized": vehicle.vehicle_plate
            }

    # ── Check 3: Udhaar customer's registered vehicle ─────────────────────────
    # This handles pump-owner's DIRECT credit customers (not logistic partners).
    # MH24W2021 could be registered under an UdhaarCustomer's vehicle list.
    udhaar_vehicles = await UdhaarVehicle.find(
        UdhaarVehicle.pump_id == pump_id_oid,
        UdhaarVehicle.deleted_at == None,
        UdhaarVehicle.is_active == True,
    ).to_list()

    matched_udhaar_veh = None
    for uv in udhaar_vehicles:
        if re.sub(r'[^A-Z0-9]', '', uv.number_plate.upper()) == clean_plate:
            matched_udhaar_veh = uv
            break

    if matched_udhaar_veh:
        # Get the customer
        from src.db.models.udhaar import UdhaarCustomer as _UdhaarCustomer
        customer = await _UdhaarCustomer.find_one(
            _UdhaarCustomer.id == matched_udhaar_veh.customer_id,
            _UdhaarCustomer.deleted_at == None,
        )
        if customer:
            # Get their active contract
            contract = await UdhaarContract.find_one(
                UdhaarContract.customer_id == customer.id,
                UdhaarContract.status == ContractStatus.active,
            )
            if contract:
                remaining = max(0.0, contract.total_credit_limit - contract.current_spend)
                return {
                    "status": "udhaar_credit",
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "vehicle_id": str(matched_udhaar_veh.id),
                    "available_credit": remaining,
                    "credit_limit": contract.total_credit_limit,
                    "current_spend": contract.current_spend,
                    "contact_phone": customer.contact_phone,
                }
            else:
                # Vehicle matched but no active contract — still inform the user
                return {
                    "status": "udhaar_no_contract",
                    "customer_id": str(customer.id),
                    "customer_name": customer.name,
                    "vehicle_id": str(matched_udhaar_veh.id),
                }

    return {"status": "none"}


@router.post("/logs", status_code=201)
async def add_sale_log(
    payload: SaleLogCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Add a sale log (Single or Batch) within a shift OR as standalone.

    Auto credit deduction:
    - If vehicle_number is provided and it matches a logistic partner's Vehicle,
      the sale amount is deducted from that vehicle's available credit by
      incrementing outstanding_amount and creating a CreditUsage record.
    """
    pump = await _get_pump_or_403(payload.pump_id, current_user.id)

    shift_oid = None
    if payload.shift_id:
        try:
            shift_oid = PydanticObjectId(payload.shift_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid shift_id")
        shift = await Shift.find_one(
            Shift.id == shift_oid,
            Shift.pump_id == pump.id,
        )
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        if shift.status == ShiftStatus.closed:
            raise HTTPException(status_code=400, detail="Cannot add logs to a closed shift")

    amount = round(payload.quantity * payload.rate, 2)
    ts = payload.timestamp or datetime.utcnow()

    customer_oid = None
    if payload.customer_id:
        try:
            customer_oid = PydanticObjectId(payload.customer_id)
        except Exception:
            pass

    if not customer_oid and payload.vehicle_number and payload.payment_mode == PaymentModeEnum.credit:
        clean_plate = re.sub(r'[^A-Z0-9]', '', payload.vehicle_number.upper())
        if clean_plate:
            all_u_vehs = await UdhaarVehicle.find(
                UdhaarVehicle.pump_id == pump.id,
                UdhaarVehicle.deleted_at == None,
                UdhaarVehicle.is_active == True,
            ).to_list()
            for uv in all_u_vehs:
                if re.sub(r'[^A-Z0-9]', '', uv.number_plate.upper()) == clean_plate:
                    customer_oid = uv.customer_id
                    break

    attendant_oid = None
    if payload.attendant_id:
        try:
            attendant_oid = PydanticObjectId(payload.attendant_id)
        except Exception:
            pass

    sale_log = SaleLog(
        shift_id=shift_oid,
        pump_id=pump.id,
        sale_type=payload.sale_type,
        timestamp=ts,
        nozzle_id=payload.nozzle_id,
        item_name=payload.item_name,
        rate=payload.rate,
        quantity=payload.quantity,
        amount=amount,
        payment_mode=payload.payment_mode,
        pos_machine=payload.pos_machine,
        billing_ref=payload.billing_ref,
        customer_name=payload.customer_name,
        customer_id=customer_oid,
        credit_slip_ref=payload.credit_slip_ref,
        vehicle_number=payload.vehicle_number,
        vehicle_type=payload.vehicle_type,
        attendant_id=attendant_oid,
        remarks=payload.remarks,
        receipt_url=payload.receipt_url,
    )
    await sale_log.insert()

    # ── Auto credit deduction for logistic partner vehicles ──────────────────
    credit_deducted = False
    credit_info = None
    if payload.vehicle_number:
        credit_deducted, credit_info = await _process_logistic_auto_credit(
            vehicle_number=payload.vehicle_number,
            amount=amount,
            pump_id=pump.id,
            attendant_id=attendant_oid,
            item_name=payload.item_name,
            quantity=payload.quantity,
            sale_log_id=sale_log.id,
            is_bulk=False
        )
        if credit_deducted:
            sale_log.payment_mode = PaymentModeEnum.credit
            if credit_info and credit_info.get("type") == "voucher":
                sale_log.remarks = (sale_log.remarks or "") + f" [Voucher {credit_info['voucher_id'][-6:]} applied]"
                sale_log.billing_ref = credit_info["voucher_id"]
            await sale_log.save()
    # ── End logistic credit deduction ────────────────────────────────────────

    # ── Udhaar contract credit tracking (pump_owner's direct credit customers) ─
    if (
        customer_oid is not None
        and sale_log.payment_mode == PaymentModeEnum.credit
    ):
        await _process_udhaar_credit_transaction(
            customer_id=customer_oid,
            pump_id=pump.id,
            amount=amount,
            quantity=payload.quantity,
            item_name=payload.item_name,
            vehicle_number=payload.vehicle_number,
            slip_ref=payload.credit_slip_ref or payload.billing_ref,
        )
    # ── End Udhaar credit tracking ───────────────────────────────────────────

    return {
        "id": str(sale_log.id),
        "pump_id": str(sale_log.pump_id),
        "shift_id": str(sale_log.shift_id) if sale_log.shift_id else None,
        "item_name": sale_log.item_name,
        "amount": sale_log.amount,
        "quantity": sale_log.quantity,
        "payment_mode": sale_log.payment_mode.value,
        "timestamp": sale_log.timestamp,
        "created_at": sale_log.created_at,
        "credit_deducted": credit_deducted,
        "credit_info": credit_info,
    }


@router.post("/logs/bulk")
async def add_sale_logs_bulk(
    logs: List[SaleLogCreate],
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Bulk add sale logs."""
    pump_ids = {l.pump_id for l in logs}
    for pid in pump_ids:
        await _get_pump_or_403(pid, current_user.id)

    created = 0
    for payload in logs:
        try:
            pump = await _get_pump_or_403(payload.pump_id, current_user.id)
            amount = round(payload.quantity * payload.rate, 2)
            # Resolve customer ID
            c_oid = None
            if payload.customer_id:
                try:
                    c_oid = PydanticObjectId(payload.customer_id)
                except Exception:
                    pass

            if not c_oid and payload.vehicle_number and payload.payment_mode == PaymentModeEnum.credit:
                clean_plate = re.sub(r'[^A-Z0-9]', '', payload.vehicle_number.upper())
                if clean_plate:
                    all_u_vehs = await UdhaarVehicle.find(
                        UdhaarVehicle.pump_id == pump.id,
                        UdhaarVehicle.deleted_at == None,
                        UdhaarVehicle.is_active == True,
                    ).to_list()
                    for uv in all_u_vehs:
                        if re.sub(r'[^A-Z0-9]', '', uv.number_plate.upper()) == clean_plate:
                            c_oid = uv.customer_id
                            break

            sale_log = SaleLog(
                shift_id=PydanticObjectId(payload.shift_id) if payload.shift_id else None,
                pump_id=pump.id,
                sale_type=payload.sale_type,
                timestamp=payload.timestamp or datetime.utcnow(),
                nozzle_id=payload.nozzle_id,
                item_name=payload.item_name,
                rate=payload.rate,
                quantity=payload.quantity,
                amount=amount,
                payment_mode=payload.payment_mode,
                pos_machine=payload.pos_machine,
                billing_ref=payload.billing_ref,
                customer_name=payload.customer_name,
                customer_id=c_oid,
                credit_slip_ref=payload.credit_slip_ref,
                vehicle_number=payload.vehicle_number,
                vehicle_type=payload.vehicle_type,
                attendant_id=PydanticObjectId(payload.attendant_id) if payload.attendant_id else None,
                remarks=payload.remarks,
                receipt_url=payload.receipt_url,
            )
            await sale_log.insert()
            created += 1

            # ── Auto credit deduction for logistic partner vehicles (bulk) ──
            credit_deducted = False
            credit_info = None
            if payload.vehicle_number:
                credit_deducted, credit_info = await _process_logistic_auto_credit(
                    vehicle_number=payload.vehicle_number,
                    amount=amount,
                    pump_id=pump.id,
                    attendant_id=PydanticObjectId(payload.attendant_id) if payload.attendant_id else None,
                    item_name=payload.item_name,
                    quantity=payload.quantity,
                    sale_log_id=sale_log.id,
                    is_bulk=True
                )
                if credit_deducted:
                    sale_log.payment_mode = PaymentModeEnum.credit
                    if credit_info and credit_info.get("type") == "voucher":
                        sale_log.remarks = (sale_log.remarks or "") + f" [Voucher {credit_info['voucher_id'][-6:]} applied]"
                        sale_log.billing_ref = credit_info["voucher_id"]
                    await sale_log.save()
            # ── End credit deduction ─────────────────────────────────────────

            # ── Udhaar credit tracking for bulk ──────────────────────────────
            if (
                c_oid is not None
                and sale_log.payment_mode == PaymentModeEnum.credit
            ):
                await _process_udhaar_credit_transaction(
                    customer_id=c_oid,
                    pump_id=pump.id,
                    amount=amount,
                    quantity=payload.quantity,
                    item_name=payload.item_name,
                    vehicle_number=payload.vehicle_number,
                    slip_ref=payload.credit_slip_ref or payload.billing_ref,
                )
            # ── End Udhaar credit tracking ───────────────────────────────────

        except Exception as e:
            log.warning("Bulk log insert failed", error=str(e))

    return {"status": "success", "count": created}


@router.get("/logs")
async def get_sale_logs(
    pump_id: str = Query(...),
    shift_id: Optional[str] = Query(None),
    customer_name: Optional[str] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    vehicle_number: Optional[str] = Query(None),
    item_name: Optional[str] = Query(None),
    payment_mode: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Filter and paginate sale logs — Entries tab."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    # Start with base filter
    filters = [SaleLog.pump_id == pump.id, SaleLog.is_deleted == False]

    if shift_id:
        try:
            filters.append(SaleLog.shift_id == PydanticObjectId(shift_id))
        except Exception:
            pass

    all_logs = await SaleLog.find(*filters).sort(-SaleLog.timestamp).to_list()

    # Python-side filtering (MongoDB doesn't support ilike easily without regex)
    def matches(l):
        if customer_name and (not l.customer_name or customer_name.lower() not in l.customer_name.lower()):
            return False
        if vehicle_type and (not l.vehicle_type or vehicle_type.lower() not in l.vehicle_type.lower()):
            return False
        if vehicle_number and (not l.vehicle_number or vehicle_number.lower() not in l.vehicle_number.lower()):
            return False
        if item_name and item_name.lower() not in l.item_name.lower():
            return False
        if payment_mode and l.payment_mode.value != payment_mode:
            return False
        if from_date and l.timestamp < from_date:
            return False
        if to_date and l.timestamp > to_date:
            return False
        return True

    filtered = [l for l in all_logs if matches(l)]
    offset = (page - 1) * page_size
    page_logs = filtered[offset:offset + page_size]

    return [
        {
            "id": str(l.id),
            "pump_id": str(l.pump_id),
            "shift_id": str(l.shift_id) if l.shift_id else None,
            "sale_type": l.sale_type.value,
            "timestamp": l.timestamp,
            "nozzle_id": l.nozzle_id,
            "item_name": l.item_name,
            "rate": l.rate,
            "quantity": l.quantity,
            "amount": l.amount,
            "payment_mode": l.payment_mode.value,
            "pos_machine": l.pos_machine,
            "billing_ref": l.billing_ref,
            "customer_name": l.customer_name,
            "credit_slip_ref": l.credit_slip_ref,
            "vehicle_number": l.vehicle_number,
            "vehicle_type": l.vehicle_type,
            "attendant_id": str(l.attendant_id) if l.attendant_id else None,
            "remarks": l.remarks,
            "created_at": l.created_at,
        }
        for l in page_logs
    ]


@router.delete("/logs/{log_id}")
async def delete_sale_log(
    log_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Soft-delete a sale log."""
    try:
        oid = PydanticObjectId(log_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid log_id")
    sale_log = await SaleLog.get(oid)
    if not sale_log:
        raise HTTPException(status_code=404, detail="Sale log not found")

    await _get_pump_or_403(str(sale_log.pump_id), current_user.id)

    sale_log.is_deleted = True
    await sale_log.save()
    return {"status": "deleted", "log_id": log_id}


# ═════════════════════════════════════════════════════════════════
# ITEM RATES — mid-shift update
# ═════════════════════════════════════════════════════════════════

@router.patch("/rates")
async def update_item_rate(
    payload: ItemRateUpdate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Update fuel rate mid-shift."""
    await _get_pump_or_403(payload.pump_id, current_user.id)
    return {
        "status": "ok",
        "pump_id": payload.pump_id,
        "item_name": payload.item_name,
        "new_rate": payload.new_rate,
        "message": "Rate updated. Future logs in this session will use the new rate.",
    }


# ═════════════════════════════════════════════════════════════════
# OVERVIEW DASHBOARD
# ═════════════════════════════════════════════════════════════════

@router.get("/overview")
async def get_sales_overview(
    pump_id: str = Query(...),
    days: int = Query(30, le=365),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Overview dashboard data."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    all_logs = await SaleLog.find(
        SaleLog.pump_id == pump.id,
        SaleLog.is_deleted == False,
    ).to_list()

    logs = [l for l in all_logs if l.timestamp and start_date <= l.timestamp <= end_date]

    net_amount = round(sum(l.amount for l in logs), 2)
    credit_amount = round(sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.credit), 2)
    cash_amount = round(sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.cash), 2)
    pos_amount = round(sum(l.amount for l in logs if l.payment_mode == PaymentModeEnum.pos), 2)
    credit_ratio = round((credit_amount / net_amount * 100) if net_amount else 0, 2)

    monthly_item: dict = defaultdict(lambda: defaultdict(float))
    weekly_item: dict = defaultdict(lambda: defaultdict(float))
    monthly_pay: dict = defaultdict(lambda: defaultdict(float))

    for l in logs:
        month_key = l.timestamp.strftime("%Y-%m")
        week_key = f"W{l.timestamp.isocalendar()[1]}-{l.timestamp.year}"
        monthly_item[l.item_name][month_key] += l.amount
        weekly_item[l.item_name][week_key] += l.amount
        monthly_pay[l.payment_mode.value][month_key] += l.amount

    # Recent closed shifts
    recent_shifts = await Shift.find(
        Shift.pump_id == pump.id,
        Shift.status == ShiftStatus.closed
    ).sort(-Shift.end_time).limit(10).to_list()

    shifts_out = []
    for s in recent_shifts:
        s_logs = await SaleLog.find(SaleLog.shift_id == s.id, SaleLog.is_deleted == False).to_list()
        shifts_out.append({
            "shift_id": str(s.id),
            "shift_type": s.shift_type,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat() if s.end_time else None,
            "net_amount": round(sum(l.amount for l in s_logs), 2),
            "sales_count": len(s_logs),
        })

    return {
        "pump_id": pump_id,
        "period_days": days,
        "net_amount": net_amount,
        "credit_ratio": credit_ratio,
        "cash_amount": cash_amount,
        "credit_amount": credit_amount,
        "pos_amount": pos_amount,
        "monthly_item_sales": {k: dict(v) for k, v in monthly_item.items()},
        "weekly_item_sales": {k: dict(v) for k, v in weekly_item.items()},
        "monthly_payment_breakdown": {k: dict(v) for k, v in monthly_pay.items()},
        "recent_shifts": shifts_out,
    }


# ═════════════════════════════════════════════════════════════════
# ATTENDANTS — for shift personnel selector
# ═════════════════════════════════════════════════════════════════

@router.get("/attendants")
async def get_pump_attendants(
    pump_id: str = Query(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """List all active attendants for a pump — used in shift setup personnel selector."""
    pump = await _get_pump_or_403(pump_id, current_user.id)

    attendants = await Attendant.find(
        Attendant.pump_id == pump.id,
        Attendant.is_active == True
    ).to_list()

    return [
        {
            "id": str(a.id),
            "name": a.name,
            "phone": getattr(a, "phone", None),
            "role": getattr(a, "role", "attendant"),
        }
        for a in attendants
    ]


# ═════════════════════════════════════════════════════════════════
# OCR RECEIPT SCANNER — POST /sales/scan-receipt
# ═════════════════════════════════════════════════════════════════

@router.post("/scan-receipt")
async def scan_receipt(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    OCR-powered credit slip scanner using EasyOCR (zero API cost, runs on server).

    - Accepts a phone camera image (JPEG/PNG/WEBP).
    - Extracts: total_amount, vehicle_no (Indian plate), customer_name_suggested.
    - Saves file to public/uploads/receipts/ (served as static files).
    - NEVER raises a 500 — OCR failures return null fields with an error hint.

    Response: {
        "total_amount": float | null,
        "vehicle_no": str | null,
        "customer_name_suggested": str | null,
        "receipt_url": str | null,
        "raw_text_lines": list[str],
        "error": str | null
    }
    """
    # ── Validate file type ────────────────────────────────────────
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{content_type}'. Please upload an image (JPEG, PNG, WEBP)."
        )

    # ── Read image bytes ──────────────────────────────────────────
    img_bytes = await file.read()
    if len(img_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # ── Save file to public/uploads/receipts/ ────────────────────
    receipts_dir = os.path.join("public", "uploads", "receipts")
    os.makedirs(receipts_dir, exist_ok=True)

    safe_name = re.sub(r"[^\w\.\-]", "_", file.filename or "receipt.jpg")
    unique_filename = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    save_path = os.path.join(receipts_dir, unique_filename)
    receipt_url: Optional[str] = None

    try:
        with open(save_path, "wb") as f:
            f.write(img_bytes)
        receipt_url = f"/uploads/receipts/{unique_filename}"
        log.info("Receipt saved", path=save_path, size=len(img_bytes))
    except Exception as save_err:
        log.error("Failed to save receipt file", error=str(save_err))
        # Non-fatal — proceed with OCR even if save failed

    # ── Run EasyOCR ───────────────────────────────────────────────
    raw_lines: list[str] = []
    parsed: dict = {"total_amount": None, "vehicle_no": None, "customer_name_suggested": None}
    ocr_error: Optional[str] = None

    try:
        import numpy as np
        from PIL import Image
        import io

        reader = _get_ocr_reader()

        # Convert bytes → numpy array for EasyOCR
        pil_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_np = np.array(pil_image)

        # readtext returns list of (bbox, text, confidence) tuples
        results = reader.readtext(img_np, detail=1)

        # Filter by confidence > 0.3 to reduce noise
        raw_lines = [text for (_, text, conf) in results if conf > 0.3]
        log.info("OCR complete", line_count=len(raw_lines), raw_lines=raw_lines[:10])

        if raw_lines:
            parsed = _parse_receipt_text(raw_lines)
        else:
            ocr_error = "no_text_detected"
            log.warning("OCR found no readable text in image")

    except RuntimeError as re_err:
        ocr_error = "ocr_not_installed"
        log.error("OCR reader unavailable", error=str(re_err))
    except Exception as ocr_exc:
        ocr_error = "ocr_failed"
        log.error("OCR processing failed", error=str(ocr_exc))

    return {
        "total_amount": parsed.get("total_amount"),
        "vehicle_no": parsed.get("vehicle_no"),
        "customer_name_suggested": parsed.get("customer_name_suggested"),
        "receipt_url": receipt_url,
        "raw_text_lines": raw_lines,
        "error": ocr_error,
    }


# ═════════════════════════════════════════════════════════════════
# LEGACY ENDPOINTS — kept for backward compatibility
# ═════════════════════════════════════════════════════════════════

@router.get("/register")
async def get_sales_register(
    pump_id: str = Query(...),
    days: int = Query(30),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Legacy endpoint — redirects to overview."""
    return await get_sales_overview(pump_id=pump_id, days=days, current_user=current_user)