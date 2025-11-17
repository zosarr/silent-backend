# silent-backend-main/silent-backend-main/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, Depends
from typing import Dict, Set
import asyncio
import json

import os
import enum
import logging
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from pydantic import BaseSettings, BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum
from sqlalchemy.orm import sessionmaker, declarative_base, Session

app = FastAPI()


from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime, timezone as tz

from .routes_license import router as license_router
from .routes_webhooks import router as webhooks_router
from .db import SessionLocal
from .models import License, LicenseStatus



# CORS - consenti al PWA di connettersi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monta le route per le licenze e i webhook
app.include_router(license_router)
app.include_router(webhooks_router)

# Stanze per i WebSocket
rooms: Dict[str, Set[WebSocket]] = {}
by_install: Dict[str, Set[WebSocket]] = {}


def register_ws(install_id: str, ws: WebSocket):
    by_install.setdefault(install_id, set()).add(ws)


def unregister_ws(install_id: str, ws: WebSocket):
    s = by_install.get(install_id)
    if s and ws in s:
        s.remove(ws)


async def broadcast(room: str, message: str, sender: WebSocket):
    if room not in rooms:
        return
    dead = set()
    for conn in rooms[room]:
        if conn is sender:
            continue
        try:
            await conn.send_text(message)
        except Exception:
            dead.add(conn)
    rooms[room] -= dead


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    room: str = Query(...),
    install_id: str = Query(...)
):
    await websocket.accept()
    websocket.state.install_id = install_id

    # Recupera la licenza
    db = SessionLocal()
    lic = db.get(License, install_id)
    db.close()

    # Imposta stato di default se non trovata
    license_status = lic.status.value if lic else "trial"
    websocket.state.license_status = license_status
    websocket.state.trial_expires_at = (
        lic.trial_expires_at if lic else datetime.now(tz.utc)
    )

    # Registra connessione
    register_ws(install_id, websocket)
    rooms.setdefault(room, set()).add(websocket)

    print(f"🟢 WS aperto: room={room}, install_id={install_id}, status={license_status}")

    try:
        while True:
            data = await websocket.receive_text()

            # --- Enforcement base Trial/Demo ---
            now = datetime.now(tz.utc)
            if websocket.state.license_status != "pro":
                # Trial scaduta → blocco
                if websocket.state.trial_expires_at <= now:
                    await websocket.send_text(
                        '{"type":"license_expired","msg":"Trial expired"}'
                    )
                    continue  # ignora i messaggi utente

            # --- Broadcast normale ---
            await broadcast(room, data, websocket)

    except WebSocketDisconnect:
        rooms[room].remove(websocket)
        unregister_ws(install_id, websocket)
        print(f"🔴 WS chiuso: room={room}, install_id={install_id}")
    except Exception as e:
        print("⚠️ WS error:", e)
        try:
            await websocket.close()
        except Exception:
            pass
        rooms[room].discard(websocket)
        unregister_ws(install_id, websocket)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Silent Backend attivo con licensing"}

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

# --- SQLAlchemy setup ---
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
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
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
    return hmac.compare_digest(provided_sig, digest)


@app.get("/license/status", response_model=LicenseStatusResponse)
def license_status(install_id: str, db: Session = Depends(get_db)):
    if not install_id:
        raise HTTPException(status_code=400, detail="install_id mancante")

    lic = get_or_create_license(db, install_id)
    effective = compute_effective_status(lic)
    db.commit()  # salva eventuale TRIAL -> DEMO

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

