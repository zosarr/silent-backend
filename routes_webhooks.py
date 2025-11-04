# silent-backend-main/silent-backend-main/routes_webhooks.py
import os, hmac, hashlib
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone as tz
from db import SessionLocal
from models import License, LicenseStatus

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev-secret")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/payment")
async def payment_webhook(req: Request, db: Session = Depends(get_db)):
    # Esempio generico con HMAC nell’header X-Signature
    raw = await req.body()
    sig = req.headers.get("X-Signature")
    mac = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not sig or not hmac.compare_digest(sig, mac):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = await req.json()
    # Supponiamo che il PSP invii 'install_id'
    install_id = data.get("install_id")
    if not install_id:
        raise HTTPException(status_code=400, detail="Missing install_id")

    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(status_code=404, detail="license not found")

    lic.status = LicenseStatus.pro
    lic.pro_activated_at = datetime.now(tz.utc)
    db.commit()

    # TODO: opzionale -> notificare i WS collegati a install_id che la licenza è PRO
    return {"ok": True}

from fastapi import Request

@router.post("/payment/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event_type = payload.get("event_type") or payload.get("event")
    resource = payload.get("resource") or {}

    # ricava install_id
    install_id = None
    try:
        pu = (resource.get("purchase_units") or [])[0]
        install_id = pu.get("custom_id")
    except Exception:
        pass
    install_id = install_id or resource.get("custom_id") or resource.get("invoice_id")

    if not install_id:
        raise HTTPException(400, "install_id non presente nel webhook")

    # consideriamo completati questi eventi
    COMPLETED_EVENTS = {"PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"}
    if event_type not in COMPLETED_EVENTS:
        return {"ok": True, "ignored": event_type}

    lic = db.get(License, install_id)
    now = datetime.now(tz.utc)
    if not lic:
        # crea direttamente PRO se non esiste ancora
        lic = License(
            install_id=install_id,
            status=LicenseStatus.pro,
            trial_started_at=now,
            trial_expires_at=now,
            pro_activated_at=now,
            limits_profile="demo_default"
        )
        db.add(lic)
    else:
        lic.status = LicenseStatus.pro
        lic.pro_activated_at = now
    db.commit()
    return {"ok": True, "install_id": install_id, "status": "pro"}

