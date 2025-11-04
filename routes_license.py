from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone as tz
from sqlalchemy.orm import Session

from db import SessionLocal, engine
from models import Base, License, LicenseStatus

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
    limits: dict = Field(default_factory=dict)

def _status_out(lic: License, now: datetime) -> StatusOut:
    return StatusOut(
        status=lic.status.value,
        now=now.isoformat(),
        trial_expires_at=lic.trial_expires_at.isoformat() if lic.status == LicenseStatus.trial else None,
        limits={} if lic.status == LicenseStatus.pro else {"max_text_chars": 1000, "min_send_interval_sec": 5},
    )

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
            limits_profile="demo_default",
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)
    return _status_out(lic, now)

@router.get("/status", response_model=StatusOut)
def status(install_id: str, db: Session = Depends(get_db)):
    now = datetime.now(tz.utc)
    lic = db.get(License, install_id)

    # 👇 AUTO-BOOTSTRAP: se non esiste, crea la trial adesso
    if not lic:
        lic = License(
            install_id=install_id,
            status=LicenseStatus.trial,
            trial_started_at=now,
            trial_expires_at=now + timedelta(hours=24),
            limits_profile="demo_default",
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)

    lic.last_seen_at = now
    db.commit()
    return _status_out(lic, now)

# (opzionale per test) reset trial
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
    
# (trial per test)
#@router.post("/dev/expire")
#def dev_expire(install_id: str, db: Session = Depends(get_db)):
  # if not DEV_RESET_ENABLED:
  #     raise HTTPException(403, "Disabled")
  # lic = db.get(License, install_id)
  #  if not lic:
  #    raise HTTPException(404, "not found")
  # from datetime import datetime, timezone, timedelta
  #lic.trial_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
  # db.commit()
  #return {"ok": True, "trial_expires_at": lic.trial_expires_at.isoformat()}

# dalla console F12 incollare questo:
# prima questo :
#   fetch('https://silent-backend.onrender.com/license/dev/expire?install_id=' + localStorage.getItem('install_id'), { method:'POST' })
#  .then(r => r.json())
#  .then(console.log); 

# secondo questo:
#   fetch('https://silent-backend.onrender.com/license/status?install_id=' + localStorage.getItem('install_id'))
#  .then(r => r.json())
#  .then(console.log);



# ( fine trial per test)
@router.post("/dev/unexpire")
def dev_unexpire(install_id: str, db: Session = Depends(get_db)):
    if not DEV_RESET_ENABLED:
        raise HTTPException(403, "Disabled")
    from datetime import datetime, timezone, timedelta
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(404, "not found")
    lic.trial_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()
    return {"ok": True, "trial_expires_at": lic.trial_expires_at.isoformat()}

# dalla console F12 incollare questo :
#   fetch('https://silent-backend.onrender.com/license/dev/unexpire?install_id='+localStorage.getItem('install_id'), { method:'POST' })
#   .then(r=>r.json()).then(console.log);

from fastapi import Body

@router.post("/activate")
def activate(install_id: str = Body(...), db: Session = Depends(get_db)):
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(404, "install_id not found")
    lic.status = LicenseStatus.pro
    lic.pro_activated_at = datetime.now(tz.utc)
    db.commit()
    return {"ok": True, "status": lic.status.value}

@router.post("/payment/webhook")
async def payment_webhook(payload: dict, db: Session = Depends(get_db)):
    install_id = payload.get("metadata", {}).get("install_id")
    if not install_id:
        raise HTTPException(400, "missing install_id")
    lic = db.get(License, install_id)
    if lic:
        lic.status = LicenseStatus.pro
        lic.pro_activated_at = datetime.now(tz.utc)
        db.commit()
    return {"ok": True}





