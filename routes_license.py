# silent-backend-main/silent-backend-main/routes_license.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone as tz
from sqlalchemy.orm import Session
from db import SessionLocal
from models import Base, License, LicenseStatus
from sqlalchemy import select
from db import engine

Base.metadata.create_all(bind=engine)

router = APIRouter(prefix="/license", tags=["license"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class RegisterIn(BaseModel):
    install_id: str

class StatusOut(BaseModel):
    status: str
    now: str
    trial_expires_at: str | None = None
    limits: dict = {}

@router.post("/register", response_model=StatusOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    now = datetime.now(tz.utc)
    lic = db.get(License, body.install_id)
    if not lic:
        lic = License(
            install_id=body.install_id,
            status=LicenseStatus.trial,
            trial_started_at=now,
            trial_expires_at=now + timedelta(hours=24),
            limits_profile="demo_default"
        )
        db.add(lic)
        db.commit()
    return StatusOut(
        status=lic.status.value,
        now=now.isoformat(),
        trial_expires_at=lic.trial_expires_at.isoformat() if lic.status==LicenseStatus.trial else None,
        limits={} if lic.status==LicenseStatus.pro else {"max_text_chars": 1000, "min_send_interval_sec": 5}
    )

@router.get("/status", response_model=StatusOut)
def status(install_id: str, db: Session = Depends(get_db)):
    now = datetime.now(tz.utc)
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(status_code=404, detail="install_id not found")
    # opzionale: update last_seen
    lic.last_seen_at = now
    db.commit()
    return StatusOut(
        status=lic.status.value,
        now=now.isoformat(),
        trial_expires_at=lic.trial_expires_at.isoformat() if lic.status==LicenseStatus.trial else None,
        limits={} if lic.status==LicenseStatus.pro else {"max_text_chars": 1000, "min_send_interval_sec": 5}
    )
from fastapi import HTTPException
import os

DEV_RESET_ENABLED = os.getenv("DEV_RESET_ENABLED", "1") == "1"

@router.post("/dev/reset")
def dev_reset(install_id: str, db: Session = Depends(get_db)):
    if not DEV_RESET_ENABLED:
        raise HTTPException(403, "Disabled")
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(404, "not found")
    db.delete(lic)
    db.commit()
    return {"ok": True}


