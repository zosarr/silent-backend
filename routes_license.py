from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from database import SessionLocal, License


router = APIRouter(prefix="/license", tags=["license"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


TRIAL_DURATION_HOURS = 24


class LicenseStatusResponse:
    def __init__(self, status, trial_hours_total, trial_hours_left, created_at, activated_at):
        self.status = status
        self.trial_hours_total = trial_hours_total
        self.trial_hours_left = trial_hours_left
        self.created_at = created_at
        self.activated_at = activated_at


def compute_status(lic: License):
    if lic.mode == "pro":
        return "pro"

    now = datetime.utcnow()
    elapsed = now - lic.trial_started

    if elapsed.total_seconds() > TRIAL_DURATION_HOURS * 3600:
        lic.mode = "demo"
        return "demo"

    return "trial"


def get_or_create_license(db: Session, install_id: str):
    lic = db.query(License).filter(License.install_id == install_id).first()
    if not lic:
        now = datetime.utcnow()
        lic = License(
            install_id=install_id,
            mode="trial",
            trial_started=now,
            pro_expires=None
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)
    return lic


@router.post("/register")
def register(install_id: str, db: Session = Depends(get_db)):
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    lic = get_or_create_license(db, install_id)
    mode = compute_status(lic)
    db.commit()

    # trial
    if mode == "trial":
        now = datetime.utcnow()
        ends = lic.trial_started + timedelta(hours=TRIAL_DURATION_HOURS)
        left = max(0, (ends - now).total_seconds() / 3600)
    else:
        left = 0

    return {
        "status": mode,
        "trial_hours_total": TRIAL_DURATION_HOURS,
        "trial_hours_left": left,
        "created_at": lic.trial_started,
        "activated_at": lic.pro_expires
    }


@router.get("/status")
def status(install_id: str, db: Session = Depends(get_db)):
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    lic = get_or_create_license(db, install_id)
    mode = compute_status(lic)
    db.commit()

    if mode == "trial":
        now = datetime.utcnow()
        ends = lic.trial_started + timedelta(hours=TRIAL_DURATION_HOURS)
        left = max(0, (ends - now).total_seconds() / 3600)
    else:
        left = 0

    return {
        "status": mode,
        "trial_hours_total": TRIAL_DURATION_HOURS,
        "trial_hours_left": left,
        "created_at": lic.trial_started,
        "activated_at": lic.pro_expires
    }
@router.options("/status")
async def options_status():
    return {}

@router.options("/register")
async def options_register():
    return {}

# ==========================
#   DEV DEBUG ENDPOINTS
# ==========================
from fastapi import Depends
from datetime import timedelta, datetime
from models import License
from sqlalchemy.orm import Session
from database import get_db

DEV_SECRET = "debug-2024"   # puoi scegliere una password

def check_dev(secret: str):
    if secret != DEV_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/license/dev/reset")
def dev_reset(install_id: str, secret: str, db: Session = Depends(get_db)):
    check_dev(secret)

    lic = db.query(License).filter_by(install_id=install_id).first()
    if not lic:
        lic = License(install_id=install_id)
        db.add(lic)

    lic.status = "trial"
    lic.activated_at = None
    lic.created_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "status": "trial"}


@router.post("/license/dev/expire")
def dev_expire(install_id: str, secret: str, db: Session = Depends(get_db)):
    check_dev(secret)

    lic = db.query(License).filter_by(install_id=install_id).first()
    if not lic:
        return {"error": "not found"}

    lic.status = "demo"
    db.commit()
    return {"ok": True, "status": "demo"}


@router.post("/license/dev/unexpire")
def dev_unexpire(install_id: str, secret: str, db: Session = Depends(get_db)):
    check_dev(secret)

    lic = db.query(License).filter_by(install_id=install_id).first()
    if not lic:
        return {"error": "not found"}

    lic.status = "trial"
    lic.created_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "status": "trial"}


@router.post("/license/dev/pro")
def dev_set_pro(install_id: str, secret: str, db: Session = Depends(get_db)):
    check_dev(secret)

    lic = db.query(License).filter_by(install_id=install_id).first()
    if not lic:
        return {"error": "not found"}

    lic.status = "pro"
    lic.activated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "status": "pro"}



