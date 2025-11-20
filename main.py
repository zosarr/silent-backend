from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, Request
from typing import Dict, Set
import asyncio
import json
import os
import enum
import logging
import hmac
import hashlib
import base64
import hmac
import hashlib

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from pydantic_settings import BaseSettings
from pydantic import BaseModel

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

app = FastAPI()

# =========================
#  WEBSOCKET CHAT ROOMS
# =========================

rooms: Dict[str, Set[WebSocket]] = {}

PING_INTERVAL = 20
PONG_TIMEOUT = 15


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "ok", "message": "Silent Backend attivo con licensing e BTCPay"}


async def join_room(room: str, websocket: WebSocket):
    peers = rooms.setdefault(room, set())
    peers.add(websocket)


async def leave_room(room: str, websocket: WebSocket):
    peers = rooms.get(room)
    if not peers:
        return
    if websocket in peers:
        peers.remove(websocket)
    if not peers:
        rooms.pop(room, None)


async def broadcast(room: str, message: str, sender: WebSocket | None = None):
    peers = rooms.get(room, set())
    dead = []
    for ws in peers:
        if sender is not None and ws is sender:
            continue
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        await leave_room(room, ws)


def presence_payload(room: str) -> str:
    count = len(rooms.get(room, set()))
    return json.dumps({"type": "presence", "count": count})


async def keepalive_task(room: str, websocket: WebSocket):
    last_pong = asyncio.get_event_loop().time()

    while True:
        await asyncio.sleep(PING_INTERVAL)

        try:
            await websocket.send_text(json.dumps({"type": "ping"}))
        except Exception:
            break

        now = asyncio.get_event_loop().time()
        if now - last_pong > PONG_TIMEOUT:
            break


@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str, token: str = Query(None)):
    await websocket.accept()

    await join_room(room, websocket)
    await broadcast(room, presence_payload(room), sender=None)

    task = asyncio.create_task(keepalive_task(room, websocket))

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await broadcast(room, data, sender=websocket)
                continue

            t = msg.get("type")

            if t == "pong":
                continue

            if t == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if t in {"presence", "pong"}:
                continue

            await broadcast(room, data, sender=websocket)

    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await leave_room(room, websocket)
        await broadcast(room, presence_payload(room), sender=None)


# =========================
# Licenze + Pagamenti BTCPay
# =========================

logger = logging.getLogger("silent-licenses")
logging.basicConfig(level=logging.INFO)


class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./silent.db")

    trial_hours: int = int(os.getenv("TRIAL_HOURS", "24"))
    license_price_eur: Decimal = Decimal(os.getenv("LICENSE_PRICE_EUR", "10"))

    btcpay_server: str = os.getenv("BTCPAY_SERVER", "")
    btcpay_store_id: str = os.getenv("BTCPAY_STORE_ID", "")
    btcpay_api_key: str = os.getenv("BTCPAY_API_KEY", "")
    btcpay_webhook_secret: str = os.getenv("BTCPAY_WEBHOOK_SECRET", "")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class LicenseStatus(str, enum.Enum):
    TRIAL = "trial"
    DEMO = "demo"
    PRO = "pro"


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    install_id = Column(String, unique=True, index=True, nullable=False)

    status = Column(SqlEnum(LicenseStatus), nullable=False, default=LicenseStatus.TRIAL)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime(timezone=True), nullable=True)

    last_invoice_id = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        logger.info("Creata nuova licenza TRIAL per install_id=%s", install_id)
    return lic


def compute_effective_status(lic: License) -> LicenseStatus:
    if lic.status == LicenseStatus.PRO:
        return LicenseStatus.PRO

    now = datetime.now(timezone.utc)
    delta = now - lic.created_at
    if delta > timedelta(hours=settings.trial_hours):
        if lic.status != LicenseStatus.DEMO:
            lic.status = LicenseStatus.DEMO
        return LicenseStatus.DEMO

    return LicenseStatus.TRIAL


class LicenseStatusResponse(BaseModel):
    status: str
    trial_hours_total: int
    trial_hours_left: float
    created_at: datetime | None = None
    activated_at: datetime | None = None


class StartPaymentRequest(BaseModel):
    install_id: str


def verify_btcpay_signature(raw_body: bytes, sig_header: str | None, secret: str) -> bool:
    if not sig_header or not secret:
        return False
    try:
        algo, provided_sig = sig_header.split("=", 1)
    except ValueError:
        return False
    if algo != "sha256":
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, provided_sig)


@app.get("/license/status", response_model=LicenseStatusResponse)
def license_status(install_id: str, db: Session = Depends(get_db)):
    if not install_id:
        raise HTTPException(status_code=400, detail="install_id mancante")

    lic = get_or_create_license(db, install_id)
    effective = compute_effective_status(lic)
    db.commit()

    trial_hours_total = settings.trial_hours
    now = datetime.now(timezone.utc)
    if effective == LicenseStatus.TRIAL:
        expires_at = lic.created_at + timedelta(hours=trial_hours_total)
        trial_hours_left = max(0.0, (expires_at - now).total_seconds() / 3600.0)
    else:
        trial_hours_left = 0.0

    return LicenseStatusResponse(
        status=effective.value,
        trial_hours_total=trial_hours_total,
        trial_hours_left=trial_hours_left,
        created_at=lic.created_at,
        activated_at=lic.activated_at,
    )

@app.post("/license/pay/btcpay/start")
async def start_btcpay_payment(
    payload: StartPaymentRequest,
    db: Session = Depends(get_db),
):
    install_id = payload.install_id.strip()
    if not install_id:
        raise HTTPException(status_code=400, detail="install_id mancante")

    if not (settings.btcpay_server and settings.btcpay_store_id and settings.btcpay_api_key):
        logger.error("BTCPay non configurato correttamente")
        raise HTTPException(status_code=500, detail="BTCPay non configurato")

    lic = get_or_create_license(db, install_id)

    invoice_body = {
        "amount": str(settings.license_price_eur),
        "currency": "EUR",
        "metadata": {
            "install_id": install_id,
        },
    }

    url = f"{settings.btcpay_server.rstrip('/')}/api/v1/stores/{settings.btcpay_store_id}/invoices"
    headers = {
        "Authorization": f"Token {settings.btcpay_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=invoice_body, headers=headers)
    except Exception as e:
        logger.exception("Errore chiamata BTCPay: %s", e)
        raise HTTPException(status_code=502, detail="Errore di comunicazione con BTCPay")

    if resp.status_code >= 400:
        logger.error(
            "BTCPay create invoice fallita: status=%s body=%s",
            resp.status_code,
            resp.text,
        )
        raise HTTPException(status_code=502, detail="Errore creazione invoice BTCPay")

    data = resp.json()
    invoice_id = data.get("id")
    checkout_url = data.get("checkoutLink") or data.get("checkoutUrl")

    if not checkout_url:
        logger.error("Risposta BTCPay senza checkout url: %s", data)
        raise HTTPException(status_code=502, detail="Risposta BTCPay non valida")

    lic.last_invoice_id = invoice_id
    db.commit()

    logger.info("Creata invoice BTCPay invoice_id=%s per install_id=%s", invoice_id, install_id)

    return {"status": "ok", "checkout_url": checkout_url}




@app.post("/license/payment/btcpay")
async def btcpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig_header = (
        request.headers.get("BTCPAY-SIG")
        or request.headers.get("Btcpay-Sig")
        or request.headers.get("btcpay-sig")
    )

    if not verify_btcpay_signature(raw, sig_header, settings.btcpay_webhook_secret):
        logger.warning("Webhook BTCPay con firma non valida")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(raw.decode("utf-8"))

    event_type = payload.get("type")
    if event_type != "InvoiceSettled":
        logger.info("Webhook BTCPay ignorato: type=%s", event_type)
        return {"status": "ignored"}

    metadata = payload.get("metadata") or {}
    install_id = metadata.get("install_id")

    if not install_id:
        invoice_id = payload.get("invoiceId")
        if invoice_id:
            lic = db.query(License).filter(License.last_invoice_id == invoice_id).first()
        else:
            lic = None
    else:
        lic = db.query(License).filter(License.install_id == install_id).first()

    if not lic:
        logger.error("Licenza non trovata per webhook BTCPay install_id=%s payload=%s", install_id, payload)
        return {"status": "license_not_found"}

    if lic.status != LicenseStatus.PRO:
        lic.status = LicenseStatus.PRO
        lic.activated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Licenza PRO attivata per install_id=%s", lic.install_id)
    else:
        logger.info("Webhook BTCPay duplicato per install_id=%s, licenza già PRO", lic.install_id)

    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

class CoinbaseStartRequest(BaseModel):
    install_id: str


def verify_coinbase_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    try:
        computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature)
    except Exception:
        return False


@app.post("/license/pay/coinbase/start")
async def start_coinbase_payment(payload: CoinbaseStartRequest, db: Session = Depends(get_db)):

    install_id = payload.install_id.strip()
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    api_key = os.getenv("COINBASE_API_KEY")
    api_url = os.getenv("COINBASE_API_URL", "https://api.commerce.coinbase.com")

    if not api_key:
        raise HTTPException(500, "Coinbase Commerce non configurato")

    headers = {
        "Content-Type": "application/json",
        "X-CC-Api-Key": api_key,
        "X-CC-Version": "2018-03-22",
    }

    body = {
        "name": "Silent PRO License",
        "description": "Licenza Silent PRO",
        "local_price": {
            "amount": str(settings.license_price_eur),
            "currency": "EUR"
        },
        "pricing_type": "fixed_price",
        "metadata": {
            "install_id": install_id
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{api_url}/charges", json=body, headers=headers)
    except Exception as e:
        logger.error("Coinbase communication error: %s", e)
        raise HTTPException(502, "Errore comunicazione Coinbase")

    if resp.status_code >= 400:
        logger.error("Coinbase API error: %s", resp.text)
        raise HTTPException(502, "Errore creazione payment Coinbase")

    data = resp.json()
    charge = data["data"]

    checkout_url = charge["hosted_url"]
    charge_id = charge["id"]

    lic = get_or_create_license(db, install_id)
    lic.last_invoice_id = charge_id
    db.commit()

    return {"status": "ok", "checkout_url": checkout_url}
    @app.post("/license/payment/coinbase")
async def coinbase_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("X-Cc-Webhook-Signature")
    secret = os.getenv("COINBASE_WEBHOOK_SECRET")

    if not verify_coinbase_signature(raw, sig, secret):
        logger.warning("Coinbase webhook firma non valida")
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(raw.decode("utf-8"))
    event_type = payload.get("event", {}).get("type")
    charge = payload.get("event", {}).get("data", {})
    metadata = charge.get("metadata", {})

    if event_type not in ["charge:confirmed", "charge:resolved"]:
        return {"status": "ignored"}

    install_id = metadata.get("install_id")
    charge_id = charge.get("id")

    if install_id:
        lic = db.query(License).filter(License.install_id == install_id).first()
    else:
        lic = db.query(License).filter(License.last_invoice_id == charge_id).first()

    if not lic:
        logger.error("Licenza non trovata per webhook Coinbase")
        return {"status": "license_not_found"}

    lic.status = LicenseStatus.PRO
    lic.activated_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok"}


