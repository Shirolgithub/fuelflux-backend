import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, Query, status
from beanie import PydanticObjectId
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone

from src.core.dependencies import require_role
from src.db.models.user import User
from src.db.models.pump import Pump
from src.db.models.compliance import ComplianceDocumentType, ComplianceDocument, NotifyContact
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/compliance", tags=["compliance"])

UPLOAD_DIR = "public/uploads/compliance"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_FILE_SIZE_MB = 10


# ─── SCHEMAS ──────────────────────────────────────────────────────────────────

class DocTypeCreate(BaseModel):
    pump_id: str
    name: str
    category: str
    is_mandatory: bool = True
    reminder_days: List[int] = Field(default_factory=lambda: [90, 60, 30, 15, 7, 1])


class DocTypeUpdate(BaseModel):
    name: Optional[str] = None
    reminder_days: Optional[List[int]] = None
    is_mandatory: Optional[bool] = None


class ContactsUpdate(BaseModel):
    contacts: List[NotifyContact]


class DocumentVerifyRequest(BaseModel):
    status: str  # "verified" or "rejected"
    rejection_reason: Optional[str] = None


# ─── HELPERS ──────────────────────────────────────────────────────────────────

async def verify_pump_ownership(pump_id: str, user: User) -> Pump:
    try:
        oid = PydanticObjectId(pump_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid pump_id format")
    pump = await Pump.find_one(Pump.id == oid, Pump.owner_id == user.id)
    if not pump:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="You do not own this pump or it does not exist"
        )
    return pump


async def seed_default_types_if_needed(pump_id: PydanticObjectId):
    existing = await ComplianceDocumentType.find(
        ComplianceDocumentType.pump_id == pump_id,
        ComplianceDocumentType.is_active == True
    ).count()
    if existing > 0:
        return

    default_types = [
        {"name": "Fire NOC", "category": "Safety", "is_mandatory": True},
        {"name": "PESO License", "category": "License", "is_mandatory": True},
        {"name": "Explosives License", "category": "License", "is_mandatory": True},
        {"name": "Pollution Under Control (PUC) Certificate", "category": "Environment", "is_mandatory": True},
        {"name": "Calibration Certificate", "category": "Operations", "is_mandatory": False},
    ]
    for dt in default_types:
        doc_type = ComplianceDocumentType(
            pump_id=pump_id,
            name=dt["name"],
            category=dt["category"],
            is_mandatory=dt["is_mandatory"],
            reminder_days=[90, 60, 30, 15, 7, 1],
            is_active=True
        )
        await doc_type.insert()


def calculate_status_and_days_left(expiry_date: datetime) -> tuple[str, int]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Ensure expiry_date is timezone-naive for comparison
    exp = expiry_date.replace(tzinfo=None) if expiry_date.tzinfo else expiry_date
    delta = exp - now
    days_left = delta.days

    if days_left < 0:
        return "expired", days_left
    elif days_left <= 30:
        return "expiring_soon", days_left
    else:
        return "active", days_left


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@router.get("/types", summary="List configured document types for a pump")
async def list_doc_types(
    pump_id: Optional[str] = None,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    if not pump_id:
        # Fallback to first owned pump
        pump = await Pump.find_one(Pump.owner_id == current_user.id)
        if not pump:
            return []
        p_id = pump.id
    else:
        await verify_pump_ownership(pump_id, current_user)
        p_id = PydanticObjectId(pump_id)

    await seed_default_types_if_needed(p_id)
    types = await ComplianceDocumentType.find(
        ComplianceDocumentType.pump_id == p_id,
        ComplianceDocumentType.is_active == True
    ).to_list()
    return types


@router.post("/types", summary="Add a custom document type")
async def create_doc_type(
    payload: DocTypeCreate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await verify_pump_ownership(payload.pump_id, current_user)
    
    doc_type = ComplianceDocumentType(
        pump_id=PydanticObjectId(payload.pump_id),
        name=payload.name,
        category=payload.category,
        is_mandatory=payload.is_mandatory,
        reminder_days=payload.reminder_days,
        is_active=True
    )
    await doc_type.insert()
    return doc_type


@router.patch("/types/{id}", summary="Edit custom document type")
async def update_doc_type(
    id: str,
    payload: DocTypeUpdate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document type ID format")

    doc_type = await ComplianceDocumentType.get(oid)
    if not doc_type:
        raise HTTPException(status_code=404, detail="Document type not found")

    await verify_pump_ownership(str(doc_type.pump_id), current_user)

    update_data = payload.model_dump(exclude_none=True)
    for k, v in update_data.items():
        setattr(doc_type, k, v)

    await doc_type.save()
    return doc_type


@router.delete("/types/{id}", summary="Soft delete document type")
async def delete_doc_type(
    id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document type ID format")

    doc_type = await ComplianceDocumentType.get(oid)
    if not doc_type:
        raise HTTPException(status_code=404, detail="Document type not found")

    await verify_pump_ownership(str(doc_type.pump_id), current_user)

    doc_type.is_active = False
    await doc_type.save()
    return {"message": "Document type deleted successfully (soft deleted)"}


@router.get("/documents", summary="List all documents with status and days left")
async def list_documents(
    pump_id: Optional[str] = None,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    if not pump_id:
        pump = await Pump.find_one(Pump.owner_id == current_user.id)
        if not pump:
            return []
        p_id = pump.id
    else:
        await verify_pump_ownership(pump_id, current_user)
        p_id = PydanticObjectId(pump_id)

    docs = await ComplianceDocument.find(ComplianceDocument.pump_id == p_id).to_list()
    
    # Compute days left and verify/update status dynamically on retrieval
    response = []
    for doc in docs:
        if not doc.is_verified:
            doc.status = "rejected" if doc.status == "rejected" else "pending_verification"
            expiry_naive = doc.expiry_date.replace(tzinfo=None) if doc.expiry_date.tzinfo else doc.expiry_date
            days_left = (expiry_naive - datetime.now(timezone.utc).replace(tzinfo=None)).days
        else:
            status_val, days_left = calculate_status_and_days_left(doc.expiry_date)
            # If stored status doesn't match computed, and isn't renewal_pending, update it
            if doc.status not in ["renewal_pending", "rejected", "pending_verification"] and doc.status != status_val:
                doc.status = status_val
                await doc.save()

        doc_dict = doc.model_dump()
        doc_dict["id"] = str(doc.id)
        doc_dict["pump_id"] = str(doc.pump_id)
        doc_dict["doc_type_id"] = str(doc.doc_type_id)
        doc_dict["days_left"] = days_left
        response.append(doc_dict)

    return response


@router.post("/documents/upload", summary="Upload new certificate")
async def upload_document(
    pump_id: str = Form(...),
    doc_type_id: str = Form(...),
    certificate_number: str = Form(...),
    issuing_authority: Optional[str] = Form(None),
    issue_date: str = Form(...),
    expiry_date: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    await verify_pump_ownership(pump_id, current_user)

    # Validate document type
    try:
        dt_oid = PydanticObjectId(doc_type_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid doc_type_id format")

    doc_type = await ComplianceDocumentType.get(dt_oid)
    if not doc_type:
        raise HTTPException(status_code=404, detail="Document type not found")

    # Validate file extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # Validate file size
    contents = await file.read()
    file_size = len(contents)
    size_mb = file_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size: {MAX_FILE_SIZE_MB}MB"
        )

    # Parse dates
    try:
        issue_dt = datetime.fromisoformat(issue_date.replace("Z", "+00:00")).replace(tzinfo=None)
        expiry_dt = datetime.fromisoformat(expiry_date.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO format (YYYY-MM-DD)")

    # Save file
    filename = f"comp_{pump_id}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(contents)

    file_url = f"/uploads/compliance/{filename}"
    
    doc = ComplianceDocument(
        pump_id=PydanticObjectId(pump_id),
        doc_type_id=dt_oid,
        doc_type_name=doc_type.name,
        certificate_number=certificate_number,
        issuing_authority=issuing_authority,
        issue_date=issue_dt,
        expiry_date=expiry_dt,
        file_url=file_url,
        file_name=file.filename or filename,
        file_size=file_size,
        status="pending_verification",
        notify_contacts=[],
        is_verified=False
    )
    await doc.insert()
    
    doc_dict = doc.model_dump()
    doc_dict["id"] = str(doc.id)
    doc_dict["pump_id"] = str(doc.pump_id)
    doc_dict["doc_type_id"] = str(doc.doc_type_id)
    return doc_dict


@router.put("/documents/{id}", summary="Replace/renew existing document")
async def renew_document(
    id: str,
    certificate_number: str = Form(...),
    issuing_authority: Optional[str] = Form(None),
    issue_date: str = Form(...),
    expiry_date: str = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")

    doc = await ComplianceDocument.get(oid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await verify_pump_ownership(str(doc.pump_id), current_user)

    # Parse dates
    try:
        issue_dt = datetime.fromisoformat(issue_date.replace("Z", "+00:00")).replace(tzinfo=None)
        expiry_dt = datetime.fromisoformat(expiry_date.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO format (YYYY-MM-DD)")

    if file:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            )

        contents = await file.read()
        file_size = len(contents)
        size_mb = file_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Max size: {MAX_FILE_SIZE_MB}MB"
            )

        # Delete old file
        old_path = doc.file_url.lstrip("/")
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

        # Save new file
        filename = f"comp_{str(doc.pump_id)}_{uuid.uuid4().hex[:8]}{ext}"
        file_path = os.path.join(UPLOAD_DIR, filename)

        with open(file_path, "wb") as f:
            f.write(contents)

        doc.file_url = f"/uploads/compliance/{filename}"
        doc.file_name = file.filename or filename
        doc.file_size = file_size
    # Update other fields
    doc.certificate_number = certificate_number
    doc.issuing_authority = issuing_authority
    doc.issue_date = issue_dt
    doc.expiry_date = expiry_dt
    doc.renewal_date = datetime.utcnow()
    doc.status = "pending_verification"
    doc.is_verified = False
    doc.rejection_reason = None
    doc.updated_at = datetime.utcnow()

    await doc.save()
    
    doc_dict = doc.model_dump()
    doc_dict["id"] = str(doc.id)
    doc_dict["pump_id"] = str(doc.pump_id)
    doc_dict["doc_type_id"] = str(doc.doc_type_id)
    return doc_dict


@router.patch("/documents/{id}/contacts", summary="Update notification contacts")
async def update_contacts(
    id: str,
    payload: ContactsUpdate,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")

    doc = await ComplianceDocument.get(oid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await verify_pump_ownership(str(doc.pump_id), current_user)

    doc.notify_contacts = payload.contacts
    doc.updated_at = datetime.utcnow()
    await doc.save()

    return {"message": "Notification contacts updated successfully", "contacts": doc.notify_contacts}


@router.delete("/documents/{id}", summary="Delete document")
async def delete_document(
    id: str,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")

    doc = await ComplianceDocument.get(oid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await verify_pump_ownership(str(doc.pump_id), current_user)

    # Delete physical file
    file_path = doc.file_url.lstrip("/")
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    await doc.delete()
    return {"message": "Document deleted successfully"}


@router.get("/dashboard", summary="Compliance Dashboard summary counts & expiring soon documents")
async def compliance_dashboard(
    pump_id: Optional[str] = None,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    if not pump_id:
        pump = await Pump.find_one(Pump.owner_id == current_user.id)
        if not pump:
            return {
                "total": 0, "active": 0, "expiring_soon": 0, "expired": 0, "renewal_pending": 0,
                "expiring_next_30_days": []
            }
        p_id = pump.id
    else:
        await verify_pump_ownership(pump_id, current_user)
        p_id = PydanticObjectId(pump_id)
    docs = await ComplianceDocument.find(ComplianceDocument.pump_id == p_id).to_list()
    
    total = len(docs)
    active = 0
    expiring_soon = 0
    expired = 0
    renewal_pending = 0
    pending_verification = 0
    rejected = 0
    expiring_next_30_days = []

    for doc in docs:
        if not doc.is_verified:
            if doc.status == "rejected":
                rejected += 1
            else:
                pending_verification += 1
            continue

        status_val, days_left = calculate_status_and_days_left(doc.expiry_date)
        
        # Override with stored status if renewal_pending is set
        final_status = doc.status if doc.status == "renewal_pending" else status_val

        if final_status == "active":
            active += 1
        elif final_status == "expiring_soon":
            expiring_soon += 1
        elif final_status == "expired":
            expired += 1
        elif final_status == "renewal_pending":
            renewal_pending += 1

        if days_left <= 30:
            expiring_next_30_days.append({
                "id": str(doc.id),
                "doc_type_name": doc.doc_type_name,
                "expiry_date": doc.expiry_date,
                "days_left": days_left,
                "status": final_status
            })

    # Sort expiring soon by days left (closest to expiry first)
    expiring_next_30_days.sort(key=lambda x: x["days_left"])

    return {
        "total": total,
        "active": active,
        "expiring_soon": expiring_soon,
        "expired": expired,
        "renewal_pending": renewal_pending,
        "pending_verification": pending_verification,
        "rejected": rejected,
        "expiring_next_30_days": expiring_next_30_days
    }


@router.patch("/documents/{id}/verify", summary="Verify/Approve compliance document")
async def verify_compliance_document(
    id: str,
    payload: DocumentVerifyRequest,
    current_user: User = Depends(require_role(["pump_owner"]))
):
    try:
        oid = PydanticObjectId(id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")

    doc = await ComplianceDocument.get(oid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await verify_pump_ownership(str(doc.pump_id), current_user)

    if payload.status == "verified":
        doc.is_verified = True
        status_val, _ = calculate_status_and_days_left(doc.expiry_date)
        doc.status = status_val
        doc.rejection_reason = None
    elif payload.status == "rejected":
        doc.is_verified = False
        doc.status = "rejected"
        doc.rejection_reason = payload.rejection_reason
    else:
        raise HTTPException(status_code=400, detail="Invalid verification status. Must be 'verified' or 'rejected'")

    doc.verified_by = current_user.email
    doc.verified_at = datetime.utcnow()
    doc.updated_at = datetime.utcnow()
    await doc.save()

    return {
        "message": f"Document verification status updated to {payload.status}",
        "id": str(doc.id),
        "status": doc.status,
        "is_verified": doc.is_verified
    }
