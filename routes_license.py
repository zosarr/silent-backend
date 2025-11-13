# routes_license.py — Silent licensing + PayPal
from fastapi import APIRouter, HTTPException, Depends, Request, Body
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone as tz
from sqlalchemy.orm import Session
import os, logging, httpx
import os, httpx, logging, hmac, hashlib, json
from fastapi import Request

from db import SessionLocal, engine
from models import Base, License, LicenseStatus
import os
from decimal import Decimal, InvalidOperation
log = logging.getLogger("btcpay")

BTCPAY_SERVER = os.getenv("BTCPAY_SERVER", "")
BTCPAY_STORE_ID = os.getenv("BTCPAY_STORE_ID", "")
BTCPAY_API_KEY = os.getenv("BTCPAY_API_KEY", "")
BTCPAY_WEBHOOK_SECRET = os.getenv("BTCPAY_WEBHOOK_SECRET", "")
LICENSE_PRICE_EUR = float(os.getenv("LICENSE_PRICE_EUR", "4.99"))


LICENSE_CURRENCY = os.getenv("LICENSE_CURRENCY", "EUR").upper()
LICENSE_PRICE    = os.getenv("LICENSE_PRICE", "4.99")  # stringa


def _price_str(v: str) -> str:
    try:
        # normalizza a 2 decimali per PayPal
        return f"{Decimal(v):.2f}"
    except InvalidOperation:
        return "4.99"

# --- DB init
Base.metadata.create_all(bind=engine)

# --- Router
router = APIRouter(prefix="/license", tags=["license"])

# --- Logger
log = logging.getLogger("paypal")

# =========================
# PayPal configuration
# =========================
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()  # 'sandbox' | 'live'
PAYPAL_BASE = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")

# Debug env (facoltativo: utile in test)
@router.get("/pay/_debug", include_in_schema=False)
def pay_debug():
    return {
        "mode": PAYPAL_MODE,
        "has_client_id": bool(PAYPAL_CLIENT_ID),
        "has_client_secret": bool(PAYPAL_CLIENT_SECRET),
        "base": PAYPAL_BASE,
    }

async def paypal_access_token() -> str:
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(500, "PayPal non configurato: mancano CLIENT_ID/CLIENT_SECRET")
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{PAYPAL_BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
            headers={"Accept": "application/json", "Accept-Language": "it-IT"},
        )
    if r.status_code != 200:
        log.error("PayPal token failed %s: %s", r.status_code, r.text)
        raise HTTPException(502, f"PayPal token failed {r.status_code}: {r.text}")
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
        "amount": {
            "currency_code": LICENSE_CURRENCY,
            "value": _price_str(LICENSE_PRICE),
        },
        "custom_id": install_id
    }],
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
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    if r.status_code not in (200, 201):
        log.error("PayPal create order failed %s: %s", r.status_code, r.text)
        raise HTTPException(502, f"PayPal create order failed {r.status_code}: {r.text}")
    data = r.json()
    approve_url = next((l["href"] for l in data.get("links", []) if l.get("rel") == "approve"), None)
    if not approve_url:
        log.error("approve_url non trovato nella risposta: %s", data)
        raise HTTPException(502, "approve_url non trovato")
    return {"approve_url": approve_url, "order_id": data.get("id")}

# =========================
# Dipendenze DB
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# =========================
# Schemi Pydantic
# =========================
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

# =========================
# Endpoints licenze
# =========================
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
    # Auto-bootstrap trial se non esiste
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

# =========================
# Dev tools (facoltativi)
# =========================
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

@router.post("/dev/unexpire")
def dev_unexpire(install_id: str, db: Session = Depends(get_db)):
    if not DEV_RESET_ENABLED:
        raise HTTPException(403, "Disabled")
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(404, "not found")
    lic.trial_expires_at = datetime.now(tz.utc) + timedelta(hours=24)
    db.commit()
    return {"ok": True, "trial_expires_at": lic.trial_expires_at.isoformat()}

@router.post("/dev/expire")
def dev_expire(install_id: str, db: Session = Depends(get_db)):
    if not DEV_RESET_ENABLED:
        raise HTTPException(403, "Disabled")
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(404, "not found")
    # forza la scadenza portando la trial nel passato
    lic.trial_expires_at = datetime.now(tz.utc) - timedelta(hours=1)
    db.commit()
    return {"ok": True, "trial_expires_at": lic.trial_expires_at.isoformat()}


# =========================
# Attivazione PRO
# =========================
from fastapi import Body

@router.post("/activate")
def activate(install_id: str = Body(..., embed=True), db: Session = Depends(get_db)):
    if not install_id:
        raise HTTPException(400, "missing install_id")
    lic = db.get(License, install_id)
    if not lic:
        raise HTTPException(404, "install_id not found")
    lic.status = LicenseStatus.pro
    lic.pro_activated_at = datetime.now(tz.utc)
    db.commit()
    return {"ok": True, "status": lic.status.value}



# =========================
# Webhook PayPal (auto PRO)
# =========================
@router.post("/payment/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Riceve eventi PayPal e promuove a PRO quando il pagamento è completato.
    Ricava install_id da purchase_units[0].custom_id (o fallback).
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event_type = payload.get("event_type") or payload.get("event")  # es. PAYMENT.CAPTURE.COMPLETED
    resource = payload.get("resource") or {}

    install_id = None
    try:
        pu = (resource.get("purchase_units") or [])[0]
        install_id = pu.get("custom_id")
    except Exception:
        pass
    install_id = install_id or resource.get("custom_id") or resource.get("invoice_id")

    if not install_id:
        raise HTTPException(400, "install_id non presente nel webhook")

    COMPLETED_EVENTS = {"PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"}
    if event_type not in COMPLETED_EVENTS:
        return {"ok": True, "ignored": event_type}

    lic = db.get(License, install_id)
    now = datetime.now(tz.utc)
    if not lic:
        lic = License(
            install_id=install_id,
            status=LicenseStatus.pro,
            trial_started_at=now,
            trial_expires_at=now,
            pro_activated_at=now,
            limits_profile="demo_default",
        )
        db.add(lic)
    else:
        lic.status = LicenseStatus.pro
        lic.pro_activated_at = now
    db.commit()
    return {"ok": True, "install_id": install_id, "status": "pro"}


def _price_str(v: float) -> str:
    return f"{v:.2f}"  # es. 4.99

@router.get("/pay/btcpay/start")
async def btcpay_start(install_id: str):
    """
    Crea un'invoice BTCPay e ritorna la URL di checkout.
    """
    if not install_id:
        raise HTTPException(400, "missing install_id")

    if not (BTCPAY_SERVER and BTCPAY_STORE_ID and BTCPAY_API_KEY):
        raise HTTPException(500, "BTCPay non configurato (env mancanti)")

    headers = {
        "Authorization": f"token {BTCPAY_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    body = {
        "amount": _price_str(LICENSE_PRICE_EUR),
        "currency": "EUR",
        "metadata": {
            "install_id": install_id
        },
        "checkout": {
            "speedPolicy": "HighSpeed",
            "redirectAutomatically": True,
            # dove mandare l'utente dopo il pagamento
            "redirectURL": "https://silentpwa.com/?paid=1"
        }
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BTCPAY_SERVER}/api/v1/stores/{BTCPAY_STORE_ID}/invoices",
            headers=headers,
            json=body
        )

    if r.status_code not in (200, 201):
        log.error("BTCPay invoice failed %s: %s", r.status_code, r.text)
        raise HTTPException(502, f"BTCPay error {r.status_code}: {r.text}")

    data = r.json()
    checkout_url = data.get("checkoutLink")
    if not checkout_url:
        raise HTTPException(502, "checkoutLink mancante nella risposta BTCPay")

    return {"checkout_url": checkout_url}

@router.post("/payment/btcpay")
async def btcpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook chiamato da BTCPay.
    Quando type == 'InvoiceSettled' e metadata.install_id è presente,
    la licenza viene promossa a PRO.
    """
    if not BTCPAY_WEBHOOK_SECRET:
        raise HTTPException(500, "BTCPay webhook secret non configurato")

    raw = await request.body()
    sig_header = request.headers.get("BTCPAY-SIG", "")

    expected = "sha256=" + hmac.new(
        BTCPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(sig_header, expected):
        log.warning("BTCPay webhook: firma non valida")
        raise HTTPException(400, "invalid signature")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "invalid json")

    event_type = payload.get("type")
    metadata = payload.get("metadata") or {}
    install_id = metadata.get("install_id")

    if not install_id:
        log.info("Webhook senza install_id, ignorato")
        return {"ok": True, "ignored": "no install_id"}

    log.info("BTCPay webhook %s per install_id=%s", event_type, install_id)

    # Solo quando l'invoice è realmente saldata
    if event_type != "InvoiceSettled":
        return {"ok": True, "ignored": event_type}

    now = datetime.now(tz.utc)
    lic = db.get(License, install_id)

    if not lic:
        # crea direttamente PRO se non esiste (opzionale)
        lic = License(
            install_id=install_id,
            status=LicenseStatus.pro,
            trial_started_at=now,
            trial_expires_at=now,
            pro_activated_at=now,
            limits_profile="demo_default",
        )
        db.add(lic)
    else:
        lic.status = LicenseStatus.pro
        lic.pro_activated_at = now

    db.commit()
    return {"ok": True, "install_id": install_id, "status": lic.status.value}





