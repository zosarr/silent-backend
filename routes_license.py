# routes_license.py (versione compatibile con Coinbase & main.py)
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from .db import SessionLocal, engine
from .models import Base, License, LicenseStatus
from .config import settings  # SE hai Settings altrove, aggiorna il path

Base.metadata.create_all(bind=engine)

router = APIRouter(prefix="/license", tags=["license"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class RegisterRequest(BaseModel):
    install_id: str


class LicenseStatusResponse(BaseModel):
    status: str
    trial_hours_total: int
    trial_hours_left: float
    created_at: datetime | None = None
    activated_at: datetime | None = None


# ---------------------------------------------------------
# 📌 TRIAL + STATUS SYSTEM (compatile con main.py)
# ---------------------------------------------------------

def compute_effective_status(lic: License) -> LicenseStatus:
    if lic.status == LicenseStatus.PRO:
        return LicenseStatus.PRO

    now = datetime.now(timezone.utc)

    elapsed = now - lic.created_at
    if elapsed > timedelta(hours=settings.trial_hours):
        # scaduta → DEMO
        if lic.status != LicenseStatus.DEMO:
            lic.status = LicenseStatus.DEMO
        return LicenseStatus.DEMO

    return LicenseStatus.TRIAL


def get_or_create_license(db: Session, install_id: str) -> License:
    lic = db.query(License).filter(License.install_id == install_id).first()
    if not lic:
        lic = License(
            install_id=install_id,
            status=LicenseStatus.TRIAL,
            created_at=datetime.now(timezone.utc),
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)
    return lic


# ---------------------------------------------------------
# 📌 ENDPOINT: REGISTER
# ---------------------------------------------------------

@router.post("/register", response_model=LicenseStatusResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    install_id = body.install_id.strip()
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    lic = get_or_create_license(db, install_id)
    effective_status = compute_effective_status(lic)
    db.commit()

    now = datetime.now(timezone.utc)
    if effective_status == LicenseStatus.TRIAL:
        expires_at = lic.created_at + timedelta(hours=settings.trial_hours)
        trial_left = max(0.0, (expires_at - now).total_seconds() / 3600)
    else:
        trial_left = 0.0

    return LicenseStatusResponse(
        status=effective_status.value,
        trial_hours_total=settings.trial_hours,
        trial_hours_left=trial_left,
        created_at=lic.created_at,
        activated_at=lic.activated_at,
    )


# ---------------------------------------------------------
# 📌 ENDPOINT: STATUS
# ---------------------------------------------------------

@router.get("/status", response_model=LicenseStatusResponse)
def status(install_id: str, db: Session = Depends(get_db)):
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    lic = db.query(License).filter(License.install_id == install_id).first()
    if not lic:
        raise HTTPException(404, "install_id non trovato")

    effective_status = compute_effective_status(lic)
    db.commit()

    now = datetime.now(timezone.utc)
    if effective_status == LicenseStatus.TRIAL:
        expires_at = lic.created_at + timedelta(hours=settings.trial_hours)
        trial_left = max(0.0, (expires_at - now).total_seconds() / 3600)
    else:
        trial_left = 0.0

    return LicenseStatusResponse(
        status=effective_status.value,
        trial_hours_total=settings.trial_hours,
        trial_hours_left=trial_left,
        created_at=lic.created_at,
        activated_at=lic.activated_at,
    )
