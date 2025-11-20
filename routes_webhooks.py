# routes_webhooks.py - VERSIONE CORRETTA PER COINBASE + main.py

import json
import hmac
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone as tz
import hmac, hashlib, json

from db import SessionLocal
from models import License, LicenseStatus
from config import settings



router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------
#   VERIFICA FIRMA COINBASE (HMAC SHA256)
# --------------------------------------------------------------
def verify_coinbase_signature(raw: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False

    computed = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


# --------------------------------------------------------------
#   WEBHOOK PAYMENT COINBASE
# --------------------------------------------------------------
@router.post("/payment/coinbase")
async def coinbase_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()

    signature = request.headers.get("X-Cc-Webhook-Signature")
    secret = settings.coinbase_webhook_secret

    if not verify_coinbase_signature(raw, signature, secret):
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(raw)
    event = payload.get("event", {})
    event_type = event.get("type")

    # Ignora eventi non importanti
    if event_type not in ["charge:confirmed", "charge:resolved"]:
        return {"status": "ignored"}

    charge_data = event.get("data", {})
    metadata = charge_data.get("metadata", {})

    install_id = metadata.get("install_id")
    charge_id = charge_data.get("id")

    # Cerca licenza tramite install_id
    lic = None
    if install_id:
        lic = db.query(License).filter(License.install_id == install_id).first()
    else:
        # fallback: cerca via invoice id
        lic = db.query(License).filter(License.last_invoice_id == charge_id).first()

    if not lic:
        return {"status": "license_not_found"}

    # Attiva PRO
    lic.status = LicenseStatus.PRO
    lic.activated_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok"}

