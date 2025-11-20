# routes_webhooks.py - Versione compatibile con Coinbase Commerce
import os
import hmac
import hashlib
import json
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from .db import SessionLocal
from .models import License, LicenseStatus

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Secret Coinbase Commerce
WEBHOOK_SECRET = os.getenv("COINBASE_WEBHOOK_SECRET")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_coinbase_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verifica firma Coinbase Commerce (HMAC-SHA256)."""
    if not signature or not secret:
        return False

    try:
        computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature)
    except Exception:
        return False


@router.post("/coinbase")
async def coinbase_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook Coinbase Commerce
    Eventi accettati:
      - charge:confirmed
      - charge:resolved
    """
    raw = await request.body()
    signature = request.headers.get("X-Cc-Webhook-Signature")

    if not verify_coinbase_signature(raw, signature, WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(raw.decode("utf-8"))

    event = payload.get("event", {})
    event_type = event.get("type", "")
    charge_data = event.get("data", {})
    metadata = charge_data.get("metadata", {})

    # Coinbase invia anche "id" utile in fallback
    charge_id = charge_data.get("id")
    install_id = metadata.get("install_id")

    # Eventi validi
    if event_type not in ("charge:confirmed", "charge:resolved"):
        return {"status": "ignored"}

    # Troviamo la licenza:
    if install_id:
        lic = db.query(License).filter(License.install_id == install_id).first()
    else:
        lic = db.query(License).filter(License.last_invoice_id == charge_id).first()

    if not lic:
        raise HTTPException(404, "License not found")

    # Attiviamo licenza PRO
    lic.status = LicenseStatus.PRO
    lic.activated_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok"}
