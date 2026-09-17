"""
FILE: src/api/v1/accounting.py
Fully migrated to async Beanie (MongoDB ODM).
"""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import List, Optional
from beanie import PydanticObjectId
from beanie.operators import In

from src.core.dependencies import get_current_active_user, require_role
from src.db.models.user import User
from src.db.models.pump import Pump

# Import new accounting models
from src.db.models.accounting import (
    AccountGroup, Account, Party, Voucher, VoucherEntry,
    VoucherType, VoucherOrigin, AccountNature
)

# Import new schemas
from src.db.schemas.accounting import (
    AccountGroupCreate, AccountGroupResponse,
    AccountCreate, AccountResponse,
    PartyCreate, PartyResponse,
    QuickVoucherCreate, JournalVoucherCreate, VoucherResponse, VoucherEntryResponse,
    BalanceSheetResponse, BalanceSheetGroup, BalanceSheetAccount,
)

router = APIRouter(prefix="/accounting", tags=["accounting"])


# ─── Helper ───────────────────────────────────────────────────────────────────

async def _verify_pump(pump_id: str, user: User) -> Pump:
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pump ID format")
    pump = await Pump.find_one(
        Pump.id == oid,
        Pump.owner_id == PydanticObjectId(user.id)
    )
    if not pump:
        raise HTTPException(status_code=403, detail="Pump not found or not authorized")
    return pump


async def _update_account_balance(account_id: PydanticObjectId):
    """Recalculate and cache account balance from all posted voucher entries using aggregation."""
    pipeline = [
        {"$match": {"account_id": account_id}},
        {
            "$lookup": {
                "from": "vouchers",
                "localField": "voucher_id",
                "foreignField": "_id",
                "as": "voucher"
            }
        },
        {"$unwind": "$voucher"},
        {"$match": {"voucher.is_posted": True}},
        {
            "$group": {
                "_id": None,
                "total_debit": {"$sum": "$debit_amount"},
                "total_credit": {"$sum": "$credit_amount"}
            }
        }
    ]
    cursor = VoucherEntry.aggregate(pipeline)
    results = await cursor.to_list(length=1)
    if results:
        res = results[0]
        balance = res.get("total_debit", 0.0) - res.get("total_credit", 0.0)
    else:
        balance = 0.0

    account = await Account.get(account_id)
    if account:
        account.current_balance = balance
        account.updated_at = datetime.utcnow()
        await account.save()


# ═══════════════════════════════════════════════════════════════════════════════
# ACCOUNT GROUPS
# ═══════════════════════════════════════════════════════════════════════════════

def _group_to_dict(g: AccountGroup) -> dict:
    return {
        "id": str(g.id),
        "pump_id": str(g.pump_id),
        "name": g.name,
        "category": g.category,
        "nature": g.nature.value if hasattr(g.nature, "value") else str(g.nature),
        "affects_gross_profit": g.affects_gross_profit,
        "is_active": g.is_active,
        "created_at": g.created_at,
    }


@router.get("/groups")
async def list_groups(
    pump_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)
    groups = await AccountGroup.find(
        AccountGroup.pump_id == PydanticObjectId(pump_id),
        AccountGroup.is_active == True
    ).sort("name").to_list()
    return [_group_to_dict(g) for g in groups]


@router.post("/groups")
async def create_group(
    payload: AccountGroupCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(payload.pump_id, current_user)

    existing = await AccountGroup.find_one(
        AccountGroup.pump_id == PydanticObjectId(payload.pump_id),
        AccountGroup.name == payload.name.strip(),
        AccountGroup.is_active == True
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Group '{payload.name}' already exists for this pump"
        )

    group = AccountGroup(
        pump_id=PydanticObjectId(payload.pump_id),
        name=payload.name,
        category=payload.category,
        nature=payload.nature,
        affects_gross_profit=payload.affects_gross_profit,
    )
    await group.insert()
    return _group_to_dict(group)


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: str,
    pump_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)
    group = await AccountGroup.find_one(
        AccountGroup.id == PydanticObjectId(group_id),
        AccountGroup.pump_id == PydanticObjectId(pump_id)
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    child_count = await Account.find(
        Account.group_id == PydanticObjectId(group_id),
        Account.is_active == True
    ).count()
    if child_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete group with {child_count} active account(s). Remove accounts first."
        )

    group.is_active = False
    await group.save()
    return {"status": "deleted", "group_id": group_id}


# ═══════════════════════════════════════════════════════════════════════════════
# ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/accounts", response_model=List[AccountResponse])
async def list_accounts(
    pump_id: str,
    group_id: Optional[str] = None,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)

    find_query = {
        "pump_id": PydanticObjectId(pump_id),
        "is_active": True
    }
    if group_id:
        find_query["group_id"] = PydanticObjectId(group_id)

    accounts = await Account.find(find_query).sort("name").to_list()

    # Pre-fetch all groups and parties for this pump to avoid N+1 database hits
    groups = await AccountGroup.find(AccountGroup.pump_id == PydanticObjectId(pump_id)).to_list()
    parties = await Party.find(Party.pump_id == PydanticObjectId(pump_id)).to_list()

    group_map = {g.id: g.name for g in groups}
    party_map = {p.id: (p.legal_name or p.alias) for p in parties}

    result = []
    for acc in accounts:
        data = AccountResponse(
            id=str(acc.id),
            pump_id=str(acc.pump_id),
            group_id=str(acc.group_id),
            party_id=str(acc.party_id) if acc.party_id else None,
            name=acc.name,
            alias=acc.alias,
            is_bank_account=acc.is_bank_account,
            bank_details=acc.bank_details,
            tcs_apply=acc.tcs_apply,
            current_balance=acc.current_balance,
            is_active=acc.is_active,
            created_at=acc.created_at,
            group_name=group_map.get(acc.group_id),
            party_name=party_map.get(acc.party_id) if acc.party_id else None,
        )
        result.append(data)
    return result


@router.post("/accounts", response_model=AccountResponse)
async def create_account(
    payload: AccountCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(payload.pump_id, current_user)

    group = await AccountGroup.find_one(
        AccountGroup.id == PydanticObjectId(payload.group_id),
        AccountGroup.pump_id == PydanticObjectId(payload.pump_id),
        AccountGroup.is_active == True
    )
    if not group:
        raise HTTPException(status_code=404, detail="Account group not found")
    
    existing_acc = await Account.find_one(
        Account.pump_id == PydanticObjectId(payload.pump_id),
        Account.group_id == PydanticObjectId(payload.group_id),
        Account.name == payload.name.strip(),
        Account.is_active == True
    )
    if existing_acc:
        raise HTTPException(
            status_code=409,
            detail=f"Account '{payload.name}' already exists in this group"
        )
 
    if payload.is_bank_account:
        bd = payload.bank_details
        if not bd or not bd.bank_name or not bd.account_no or not bd.ifsc:
            raise HTTPException(
                status_code=422,
                detail="Bank accounts require bank_name, account_no, and ifsc in bank_details"
            )

    bank_json = None
    if payload.is_bank_account and payload.bank_details:
        bank_json = payload.bank_details.model_dump()

    account = Account(
        pump_id=PydanticObjectId(payload.pump_id),
        group_id=PydanticObjectId(payload.group_id),
        party_id=PydanticObjectId(payload.party_id) if payload.party_id else None,
        name=payload.name,
        alias=payload.alias,
        is_bank_account=payload.is_bank_account,
        bank_details=bank_json,
        tcs_apply=payload.tcs_apply,
        current_balance=0.0,
    )
    await account.insert()

    return AccountResponse(
        id=str(account.id),
        pump_id=str(account.pump_id),
        group_id=str(account.group_id),
        party_id=str(account.party_id) if account.party_id else None,
        name=account.name,
        alias=account.alias,
        is_bank_account=account.is_bank_account,
        bank_details=account.bank_details,
        tcs_apply=account.tcs_apply,
        current_balance=account.current_balance,
        is_active=account.is_active,
        created_at=account.created_at,
        group_name=group.name,
        party_name=None,
    )


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: str,
    pump_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)
    account = await Account.find_one(
        Account.id == PydanticObjectId(account_id),
        Account.pump_id == PydanticObjectId(pump_id)
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    entry_count = await VoucherEntry.find(
        VoucherEntry.account_id == PydanticObjectId(account_id)
    ).count()
    if entry_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete account with existing voucher entries."
        )

    account.is_active = False
    await account.save()
    return {"status": "deleted", "account_id": account_id}


# ═══════════════════════════════════════════════════════════════════════════════
# PARTIES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/parties", response_model=List[PartyResponse])
async def list_parties(
    pump_id: str,
    category: Optional[str] = None,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)

    find_query = {
        "pump_id": PydanticObjectId(pump_id),
        "is_active": True
    }
    if category:
        find_query["category"] = category

    parties = await Party.find(find_query).sort("legal_name").to_list()

    # Pre-fetch linked accounts
    party_ids = [p.id for p in parties]
    accounts = await Account.find(
        In(Account.party_id, party_ids),
        Account.is_active == True
    ).to_list()
    account_map = {acc.party_id: acc.id for acc in accounts}

    result = []
    for p in parties:
        data = PartyResponse(
            id=str(p.id),
            pump_id=str(p.pump_id),
            alias=p.alias,
            category=p.category,
            address=p.address,
            legal_name=p.legal_name,
            registration_type=p.registration_type,
            pan=p.pan,
            gst_number=p.gst_number,
            primary_contact=p.primary_contact,
            linked_account_id=str(account_map.get(p.id)) if p.id in account_map else None,
            is_active=p.is_active,
            created_at=p.created_at,
        )
        result.append(data)
    return result


@router.post("/parties", response_model=PartyResponse)
async def create_party(
    payload: PartyCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(payload.pump_id, current_user)

    if payload.legal_name:
        existing_party = await Party.find_one(
            Party.pump_id == PydanticObjectId(payload.pump_id),
            Party.legal_name == payload.legal_name.strip(),
            Party.is_active == True
        )
        if existing_party:
            raise HTTPException(
                status_code=409,
                detail=f"Party with legal name '{payload.legal_name}' already exists"
            )
    if payload.gst_number:
        existing_gst = await Party.find_one(
            Party.pump_id == PydanticObjectId(payload.pump_id),
            Party.gst_number == payload.gst_number.strip(),
            Party.is_active == True
        )
        if existing_gst:
            raise HTTPException(
                status_code=409,
                detail=f"Party with GST number '{payload.gst_number}' already exists"
            )

    # Determine auto-group for party account
    if payload.category == "customer":
        auto_group_name = "Sundry Debtors"
        auto_nature = "asset"
    elif payload.category == "supplier":
        auto_group_name = "Sundry Creditors"
        auto_nature = "liability"
    else:
        auto_group_name = "Miscellaneous"
        auto_nature = "liability"

    # Find or create the default group
    default_group = await AccountGroup.find_one(
        AccountGroup.pump_id == PydanticObjectId(payload.pump_id),
        AccountGroup.name == auto_group_name,
        AccountGroup.is_active == True
    )

    if not default_group:
        default_group = AccountGroup(
            pump_id=PydanticObjectId(payload.pump_id),
            name=auto_group_name,
            category="Auto",
            nature=auto_nature,
            affects_gross_profit=False,
        )
        await default_group.insert()

    # Create party
    party = Party(
        pump_id=PydanticObjectId(payload.pump_id),
        alias=payload.alias,
        category=payload.category.value,
        address=payload.address,
        legal_name=payload.legal_name,
        registration_type=payload.registration_type.value,
        pan=payload.pan,
        gst_number=payload.gst_number,
        primary_contact=payload.primary_contact,
    )
    await party.insert()

    # Auto-create linked account
    display_name = payload.legal_name or payload.alias or f"Party-{party.id}"
    linked_account = Account(
        pump_id=PydanticObjectId(payload.pump_id),
        group_id=default_group.id,
        party_id=party.id,
        name=display_name,
        alias=payload.alias,
        is_bank_account=False,
        tcs_apply=False,
        current_balance=0.0,
    )
    await linked_account.insert()

    return PartyResponse(
        id=str(party.id),
        pump_id=str(party.pump_id),
        alias=party.alias,
        category=party.category,
        address=party.address,
        legal_name=party.legal_name,
        registration_type=party.registration_type,
        pan=party.pan,
        gst_number=party.gst_number,
        primary_contact=party.primary_contact,
        linked_account_id=str(linked_account.id),
        is_active=party.is_active,
        created_at=party.created_at,
    )


@router.delete("/parties/{party_id}")
async def delete_party(
    party_id: str,
    pump_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)
    party = await Party.find_one(
        Party.id == PydanticObjectId(party_id),
        Party.pump_id == PydanticObjectId(pump_id)
    )
    if not party:
        raise HTTPException(status_code=404, detail="Party not found")
    
    linked_account = await Account.find_one(
        Account.party_id == party.id,
        Account.is_active == True
    )
    if linked_account:
        entry_count = await VoucherEntry.find(
            VoucherEntry.account_id == linked_account.id
        ).count()
        if entry_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete party — linked account has {entry_count} voucher entries"
            )
        linked_account.is_active = False
        await linked_account.save()

    party.is_active = False
    await party.save()
    return {"status": "deleted", "party_id": party_id}


# ═══════════════════════════════════════════════════════════════════════════════
# VOUCHERS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/vouchers", response_model=List[VoucherResponse])
async def list_vouchers(
    pump_id: str,
    voucher_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)

    find_query = {"pump_id": PydanticObjectId(pump_id)}
    if voucher_type:
        find_query["voucher_type"] = voucher_type

    vouchers = await Voucher.find(find_query).sort("-voucher_date").skip(offset).limit(limit).to_list()

    voucher_ids = [v.id for v in vouchers]
    entries = await VoucherEntry.find(In(VoucherEntry.voucher_id, voucher_ids)).to_list()

    account_ids = list({e.account_id for e in entries})
    accounts = await Account.find(In(Account.id, account_ids)).to_list()
    account_names = {a.id: a.name for a in accounts}

    entries_by_voucher = {}
    for e in entries:
        entries_by_voucher.setdefault(e.voucher_id, []).append(e)

    result = []
    for v in vouchers:
        v_entries = entries_by_voucher.get(v.id, [])
        v_entries_sorted = sorted(v_entries, key=lambda x: x.entry_order)
        entries_response = [
            VoucherEntryResponse(
                id=str(e.id),
                account_id=str(e.account_id),
                account_name=account_names.get(e.account_id),
                debit_amount=e.debit_amount,
                credit_amount=e.credit_amount,
                entry_order=e.entry_order,
            )
            for e in v_entries_sorted
        ]
        result.append(VoucherResponse(
            id=str(v.id),
            pump_id=str(v.pump_id),
            voucher_type=v.voucher_type,
            origin=v.origin,
            narration=v.narration,
            voucher_date=v.voucher_date,
            is_posted=v.is_posted,
            entries=entries_response,
            created_at=v.created_at,
        ))
    return result


@router.get("/vouchers/{voucher_id}/entries", response_model=VoucherResponse)
async def get_voucher_entries(
    voucher_id: str,
    pump_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await _verify_pump(pump_id, current_user)

    v = await Voucher.find_one(
        Voucher.id == PydanticObjectId(voucher_id),
        Voucher.pump_id == PydanticObjectId(pump_id)
    )
    if not v:
        raise HTTPException(status_code=404, detail="Voucher not found")

    entries = await VoucherEntry.find(VoucherEntry.voucher_id == v.id).to_list()
    account_ids = [e.account_id for e in entries]
    accounts = await Account.find(In(Account.id, account_ids)).to_list()
    account_names = {a.id: a.name for a in accounts}

    entries_response = [
        VoucherEntryResponse(
            id=str(e.id),
            account_id=str(e.account_id),
            account_name=account_names.get(e.account_id),
            debit_amount=e.debit_amount,
            credit_amount=e.credit_amount,
            entry_order=e.entry_order,
        )
        for e in sorted(entries, key=lambda x: x.entry_order)
    ]
    return VoucherResponse(
        id=str(v.id),
        pump_id=str(v.pump_id),
        voucher_type=v.voucher_type,
        origin=v.origin,
        narration=v.narration,
        voucher_date=v.voucher_date,
        is_posted=v.is_posted,
        entries=entries_response,
        created_at=v.created_at,
    )


@router.post("/vouchers/quick", response_model=VoucherResponse)
async def create_quick_voucher(
    payload: QuickVoucherCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Payment / Receipt / Contra — simple 2-account transfer."""
    await _verify_pump(payload.pump_id, current_user)

    from_acc = await Account.find_one(
        Account.id == PydanticObjectId(payload.from_account_id),
        Account.pump_id == PydanticObjectId(payload.pump_id),
        Account.is_active == True
    )
    to_acc = await Account.find_one(
        Account.id == PydanticObjectId(payload.to_account_id),
        Account.pump_id == PydanticObjectId(payload.pump_id),
        Account.is_active == True
    )

    if not from_acc:
        raise HTTPException(status_code=404, detail="Source account not found")
    if not to_acc:
        raise HTTPException(status_code=404, detail="Destination account not found")
    if payload.from_account_id == payload.to_account_id:
        raise HTTPException(
            status_code=400,
            detail="Source and destination account cannot be the same"
        )

    voucher = Voucher(
        pump_id=PydanticObjectId(payload.pump_id),
        voucher_type=payload.voucher_type.value,
        origin=VoucherOrigin.manual,
        narration=payload.narration,
        voucher_date=payload.voucher_date or datetime.utcnow(),
        is_posted=True,
        posted_at=datetime.utcnow(),
    )
    await voucher.insert()

    entries = [
        VoucherEntry(voucher_id=voucher.id, account_id=from_acc.id,
                     debit_amount=payload.amount, credit_amount=0.0, entry_order=0),
        VoucherEntry(voucher_id=voucher.id, account_id=to_acc.id,
                     debit_amount=0.0, credit_amount=payload.amount, entry_order=1),
    ]
    for entry in entries:
        await entry.insert()

    await _update_account_balance(from_acc.id)
    await _update_account_balance(to_acc.id)

    return VoucherResponse(
        id=str(voucher.id),
        pump_id=str(voucher.pump_id),
        voucher_type=voucher.voucher_type,
        origin=voucher.origin,
        narration=voucher.narration,
        voucher_date=voucher.voucher_date,
        is_posted=voucher.is_posted,
        entries=[
            VoucherEntryResponse(id=str(entries[0].id), account_id=str(entries[0].account_id),
                                 account_name=from_acc.name, debit_amount=entries[0].debit_amount,
                                 credit_amount=0.0, entry_order=0),
            VoucherEntryResponse(id=str(entries[1].id), account_id=str(entries[1].account_id),
                                 account_name=to_acc.name, debit_amount=0.0,
                                 credit_amount=entries[1].credit_amount, entry_order=1),
        ],
        created_at=voucher.created_at,
    )


@router.post("/vouchers/journal", response_model=VoucherResponse)
async def create_journal_voucher(
    payload: JournalVoucherCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """Journal — multi-line, user supplies debit/credit. SUM(debit)==SUM(credit) enforced."""
    await _verify_pump(payload.pump_id, current_user)

    if len(payload.entries) < 2:
        raise HTTPException(status_code=400, detail="Journal voucher needs at least 2 entries")

    total_debit  = sum(e.debit_amount for e in payload.entries)
    total_credit = sum(e.credit_amount for e in payload.entries)

    if round(total_debit, 2) != round(total_credit, 2):
        raise HTTPException(
            status_code=400,
            detail=f"Journal not balanced: debits={total_debit}, credits={total_credit}"
        )

    account_ids = [PydanticObjectId(e.account_id) for e in payload.entries]
    found_accounts = await Account.find(
        In(Account.id, account_ids),
        Account.pump_id == PydanticObjectId(payload.pump_id),
        Account.is_active == True
    ).to_list()
    found_ids = {a.id: a for a in found_accounts}

    missing = [str(aid) for aid in account_ids if aid not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"Accounts not found: {missing}")

    voucher = Voucher(
        pump_id=PydanticObjectId(payload.pump_id),
        voucher_type=VoucherType.journal,
        origin=VoucherOrigin.manual,
        narration=payload.narration,
        voucher_date=payload.voucher_date or datetime.utcnow(),
        is_posted=True,
        posted_at=datetime.utcnow(),
    )
    await voucher.insert()

    entry_objs = []
    for i, e in enumerate(payload.entries):
        entry_obj = VoucherEntry(
            voucher_id=voucher.id,
            account_id=PydanticObjectId(e.account_id),
            debit_amount=e.debit_amount,
            credit_amount=e.credit_amount,
            entry_order=e.entry_order if e.entry_order else i,
        )
        await entry_obj.insert()
        entry_objs.append(entry_obj)

    for aid in set(account_ids):
        await _update_account_balance(aid)

    return VoucherResponse(
        id=str(voucher.id),
        pump_id=str(voucher.pump_id),
        voucher_type=voucher.voucher_type,
        origin=voucher.origin,
        narration=voucher.narration,
        voucher_date=voucher.voucher_date,
        is_posted=voucher.is_posted,
        entries=[
            VoucherEntryResponse(
                id=str(eo.id),
                account_id=str(eo.account_id),
                account_name=found_ids[eo.account_id].name,
                debit_amount=eo.debit_amount,
                credit_amount=eo.credit_amount,
                entry_order=eo.entry_order,
            )
            for eo in entry_objs
        ],
        created_at=voucher.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BALANCE SHEET (Live)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/balance-sheet", response_model=BalanceSheetResponse)
async def get_balance_sheet(
    pump_id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    """
    Live balance sheet — reads current_balance cached on each Account.
    Organized by nature: income / expenditure / asset / liability.
    Calculates gross_profit (only affects_gross_profit groups) and total_equity.
    """
    await _verify_pump(pump_id, current_user)

    groups = await AccountGroup.find(
        AccountGroup.pump_id == PydanticObjectId(pump_id),
        AccountGroup.is_active == True
    ).to_list()

    accounts = await Account.find(
        Account.pump_id == PydanticObjectId(pump_id),
        Account.is_active == True
    ).to_list()

    accounts_by_group = {}
    for a in accounts:
        accounts_by_group.setdefault(a.group_id, []).append(a)

    sections = {
        "income": [],
        "expenditure": [],
        "asset": [],
        "liability": [],
    }

    for group in groups:
        group_accounts = accounts_by_group.get(group.id, [])
        group_total = sum(a.current_balance for a in group_accounts)

        bs_accounts = [
            BalanceSheetAccount(
                account_id=str(a.id),
                account_name=a.name,
                alias=a.alias,
                current_balance=a.current_balance,
            )
            for a in group_accounts
        ]

        bs_group = BalanceSheetGroup(
            group_id=str(group.id),
            group_name=group.name,
            nature=group.nature.value if isinstance(group.nature, AccountNature) else str(group.nature),
            affects_gross_profit=group.affects_gross_profit,
            total=group_total,
            accounts=bs_accounts,
        )

        nature_key = group.nature.value if isinstance(group.nature, AccountNature) else str(group.nature)
        if nature_key in sections:
            sections[nature_key].append(bs_group)

    # Gross Profit = Direct Income - Direct Expenditure (affects_gross_profit groups only)
    direct_income = sum(
        g.total for g in sections["income"] if g.affects_gross_profit
    )
    direct_expenditure = sum(
        g.total for g in sections["expenditure"] if g.affects_gross_profit
    )
    gross_profit = direct_income - direct_expenditure

    # Total Equity = Assets - Liabilities
    total_assets = sum(g.total for g in sections["asset"])
    total_liabilities = sum(g.total for g in sections["liability"])
    total_equity = total_assets - total_liabilities

    return BalanceSheetResponse(
        pump_id=pump_id,
        generated_at=datetime.utcnow(),
        income=sections["income"],
        expenditure=sections["expenditure"],
        assets=sections["asset"],
        liabilities=sections["liability"],
        gross_profit=round(gross_profit, 2),
        total_equity=round(total_equity, 2),
    )