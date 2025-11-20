from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, Request
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
from pydantic_settings import BaseSettings
from pydantic import BaseModel

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

app = FastAPI()

# ============================================================
#  WEBSOCKET ROOMS
# ============================================================

rooms: Dict[str, Set[WebSocket]] = {}

PING_INTERVAL = 20
PONG_TIMEOUT = 15

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"status": "ok", "message": "Silent Backend attivo con Coinbase Commerce"}

async def join_room(room: str, websocket: WebSocket):
    rooms.setdefault(room, set()).add(websocket)

async def leave_room(room: str, websocket: WebSocket):
    peers = rooms.get(room)
    if peers and websocket in peers:
        peers.remove(websocket)
        if not peers:
            rooms.pop(room, None)

async def broadcast(room: str, message: str, sender: WebSocket | None = None):
    peers = rooms.get(room, set())
    dead = []
    for ws in peers:
        if sender and ws is sender:
            continue
        try:
            await ws.send_text(message)
        except:
            dead.append(ws)
    for ws in dead:
        await leave_room(room, ws)

def presence_payload(room: str):
    return json.dumps({"type": "presence", "count": len(rooms.get(room, set()))})

async def keepalive_task(room: str, websocket: WebSocket):
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            await websocket.send_text(json.dumps({"type": "ping"}))
        except:
            break

@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str):
    await websocket.accept()
    await join_room(room, websocket)
    await broadcast(room, presence_payload(room))

    task = asyncio.create_task(keepalive_task(room, websocket))

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    continue
            except:
                pass
            await broadcast(room, data, sender=websocket)
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await leave_room(room, websocket)
        await broadcast(room, presence_payload(room))


# ============================================================
#   LICENSING + COINBASE COMMERCE
# ============================================================

logger = logging.getLogger("silent-licenses")
logging.basicConfig(level=logging.INFO)

class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./silent.db")

    trial_hours: int = int(os.getenv("TRIAL_HOURS", "24"))
    license_price_eur: Decimal = Decimal(os.getenv("LICENSE_PRICE_EUR", "10"))

    # SOLO Coinbase Commerce
    coinbase_api_key: str = os.getenv("COINBASE_API_KEY", "")
    coinbase_webhook_secret: str = os.getenv("COINBASE_WEBHOOK_SECRET", "")
    coinbase_api_url: str = os.getenv("COINBASE_API_URL", "https://api.commerce.coinbase.com")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class LicenseStatus(str, enum.Enum):
    TRIAL = "trial"
    DEMO = "demo"
    PRO = "pro"

class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    install_id = Column(String, unique=True, index=True, nullable=False)
    status = Column(SqlEnum(LicenseStatus), default=LicenseStatus.TRIAL)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime(timezone=True), nullable=True)
    last_invoice_id = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_or_create_license(db: Session, install_id: str):
    lic = db.query(License).filter(License.install_id == install_id).first()
    if not lic:
        lic = License(
            install_id=install_id,
            status=LicenseStatus.TRIAL,
            created_at=datetime.now(timezone.utc)
        )
        db.add(lic)
        db.commit()
    return lic

def compute_effective_status(lic: License):
    if lic.status == LicenseStatus.PRO:
        return LicenseStatus.PRO
    now = datetime.now(timezone.utc)
    if now - lic.created_at > timedelta(hours=settings.trial_hours):
        lic.status = LicenseStatus.DEMO
        return LicenseStatus.DEMO
    return LicenseStatus.TRIAL


class LicenseStatusResponse(BaseModel):
    status: str
    trial_hours_total: int
    trial_hours_left: float
    created_at: datetime | None
    activated_at: datetime | None

@app.get("/license/status", response_model=LicenseStatusResponse)
def license_status(install_id: str, db: Session = Depends(get_db)):
    lic = get_or_create_license(db, install_id)
    effective = compute_effective_status(lic)
    db.commit()

    trial_total = settings.trial_hours
    if effective == LicenseStatus.TRIAL:
        expires = lic.created_at + timedelta(hours=trial_total)
        left = max(0.0, (expires - datetime.now(timezone.utc)).total_seconds() / 3600)
    else:
        left = 0.0

    return LicenseStatusResponse(
        status=effective.value,
        trial_hours_total=trial_total,
        trial_hours_left=left,
        created_at=lic.created_at,
        activated_at=lic.activated_at,
    )


# ============================================================
#     COINBASE COMMERCE START PAYMENT
# ============================================================

class CoinbaseStartRequest(BaseModel):
    install_id: str

@app.post("/license/pay/coinbase/start")
async def start_coinbase_payment(payload: CoinbaseStartRequest, db: Session = Depends(get_db)):

    install_id = payload.install_id.strip()
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    if not settings.coinbase_api_key:
        raise HTTPException(500, "Coinbase Commerce non configurato")

    headers = {
        "Content-Type": "application/json",
        "X-CC-Api-Key": settings.coinbase_api_key,
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
        "metadata": {"install_id": install_id}
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{settings.coinbase_api_url}/charges", json=body, headers=headers)
    except Exception as e:
        logger.error("Errore comunicazione Coinbase: %s", e)
        raise HTTPException(502, "Errore comunicazione Coinbase")

    if resp.status_code >= 400:
        logger.error("Errore Coinbase: %s", resp.text)
        raise HTTPException(502, "Errore creazione payment Coinbase")

    data = resp.json()["data"]
    checkout_url = data["hosted_url"]
    charge_id = data["id"]

    lic = get_or_create_license(db, install_id)
    lic.last_invoice_id = charge_id
    db.commit()

    return {"status": "ok", "checkout_url": checkout_url}


# ============================================================
#     COINBASE WEBHOOK
# ============================================================

def verify_coinbase_signature(raw: bytes, signature: str, secret: str):
    if not signature or not secret:
        return False
    computed = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)

@app.post("/license/payment/coinbase")
async def coinbase_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("X-Cc-Webhook-Signature")
    secret = settings.coinbase_webhook_secret

    if not verify_coinbase_signature(raw, signature, secret):
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(raw)
    event = payload.get("event", {})
    event_type = event.get("type")

    if event_type not in ["charge:confirmed", "charge:resolved"]:
        return {"status": "ignored"}

    charge = event.get("data", {})
    metadata = charge.get("metadata", {})
    install_id = metadata.get("install_id")
    charge_id = charge.get("id")

    if install_id:
        lic = db.query(License).filter(License.install_id == install_id).first()
    else:
        lic = db.query(License).filter(License.last_invoice_id == charge_id).first()

    if not lic:
        return {"status": "license_not_found"}

    lic.status = LicenseStatus.PRO
    lic.activated_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok"}
