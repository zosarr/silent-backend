import os, httpx
from fastapi import Request
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()   # 'sandbox' o 'live'
PAYPAL_BASE = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")


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
async def paypal_access_token() -> str:
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(500, "PayPal non configurato")
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
            headers={"Accept": "application/json", "Accept-Language": "it-IT"},
        )
        if r.status_code != 200:
            raise HTTPException(502, f"PayPal token failed: {r.text}")
        return r.json()["access_token"]


@router.get("/pay/start")
async def pay_start(install_id: str):
    """
    Crea un ordine PayPal e restituisce il link di approvazione da aprire nel browser.
    """
    if not install_id:
        raise HTTPException(400, "missing install_id")

    token = await paypal_access_token()
    order_body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            # IMPORTO: cambia come vuoi
            "amount": {"currency_code": "EUR", "value": "4.99"},
            # LEGA l'ordine a questo install_id
            "custom_id": install_id
        }],
        # Opzionale: URL per redirect (non indispensabili se usi solo webhook)
        "application_context": {
            "brand_name": "Silent",
            "landing_page": "LOGIN",
            "user_action": "PAY_NOW"
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v2/checkout/orders",
            json=order_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )
    if r.status_code not in (201, 200):
        raise HTTPException(502, f"PayPal create order failed: {r.text}")

    data = r.json()
    approve_url = next((l["href"] for l in data.get("links", []) if l.get("rel") == "approve"), None)
    if not approve_url:
        raise HTTPException(502, "approve_url non trovato")
    return {"approve_url": approve_url, "order_id": data.get("id")}


@router.post("/payment/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Riceve eventi PayPal. Quando il pagamento è completato,
    promuove la licenza a PRO usando purchase_units[0].custom_id = install_id.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # Eventi tipici:
    # - CHECKOUT.ORDER.APPROVED (ordine approvato)
    # - PAYMENT.CAPTURE.COMPLETED (pagamento incassato)
    event_type = payload.get("event_type") or payload.get("event")  # fallback
    resource = payload.get("resource") or {}

    # Prova a risalire all'install_id dai purchase_units
    install_id = None
    try:
        pu = (resource.get("purchase_units") or [])[0]
        install_id = pu.get("custom_id")
    except Exception:
        pass

    # Alcuni webhook hanno custom_id a livelli diversi — altri fallback:
    install_id = install_id or resource.get("custom_id") or resource.get("invoice_id")

    if not install_id:
        # Se proprio non c'è, logga e stoppa: per sicurezza non attiviamo PRO.
        raise HTTPException(400, "install_id non presente nel webhook")

    # Consideriamo completati questi eventi:
    COMPLETED_EVENTS = {"PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"}

    if event_type not in COMPLETED_EVENTS:
        # Ignora eventi non rilevanti
        return {"ok": True, "ignored": event_type}

    lic = db.get(License, install_id)
    if not lic:
        # Se l'install_id non esiste ancora, crea trial e promuovi (opzionale)
        now = datetime.now(tz.utc)
        lic = License(
            install_id=install_id,
            status=LicenseStatus.pro,
            trial_started_at=now,
            trial_expires_at=now,  # trial irrilevante, già PRO
            pro_activated_at=now,
            limits_profile="demo_default"
        )
        db.add(lic)
        db.commit()
        return {"ok": True, "installed": "created_pro"}

    # Promuovi a PRO se non lo è già
    if lic.status != LicenseStatus.pro:
        lic.status = LicenseStatus.pro
        lic.pro_activated_at = datetime.now(tz.utc)
        db.commit()

    return {"ok": True, "install_id": install_id, "status": lic.status.value}



