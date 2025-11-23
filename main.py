from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
from config import settings
import httpx
from pydantic_settings import BaseSettings
from pydantic import BaseModel

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from routes_btc import router as btc_router
app.include_router(btc_router)

# =============================
#  CREA L’APP PRIMA DI TUTTO
# =============================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import dopo la creazione dell’app
from routes_license import router as license_router
from routes_webhooks import router as webhooks_router

# Includi router
app.include_router(license_router)
app.include_router(webhooks_router)

# ============================================================
#  WEBSOCKET
# ============================================================

rooms: Dict[str, Set[WebSocket]] = {}
PING_INTERVAL = 20

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

async def broadcast(room: str, msg: str, sender: WebSocket | None = None):
    for ws in rooms.get(room, set()):
        if ws is not sender:
            try:
                await ws.send_text(msg)
            except:
                pass

@app.websocket("/ws/{room}")
async def websocket_endpoint(ws: WebSocket, room: str):
    await ws.accept()
    await join_room(room, ws)
    try:
        while True:
            data = await ws.receive_text()
            await broadcast(room, data, sender=ws)
    except WebSocketDisconnect:
        pass
    finally:
        await leave_room(room, ws)

# ============================================================
#  DATABASE & SETTINGS
# ============================================================

logger = logging.getLogger("silent-licenses")
logging.basicConfig(level=logging.INFO)

class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./silent.db")

    trial_hours: int = int(os.getenv("TRIAL_HOURS", "24"))
    license_price_eur: Decimal = Decimal(os.getenv("LICENSE_PRICE_EUR", "10"))

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
    install_id = Column(String, unique=True, index=True)
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
        lic = License(install_id=install_id)
        db.add(lic)
        db.commit()
    return lic

def compute_effective_status(lic: License):
    now = datetime.now(timezone.utc)
    if lic.status == LicenseStatus.PRO:
        return LicenseStatus.PRO
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
    s = compute_effective_status(lic)
    db.commit()

    if s == LicenseStatus.TRIAL:
        expires = lic.created_at + timedelta(hours=settings.trial_hours)
        left = max(0, (expires - datetime.now(timezone.utc)).total_seconds() / 3600)
    else:
        left = 0

    return LicenseStatusResponse(
        status=s.value,
        trial_hours_total=settings.trial_hours,
        trial_hours_left=left,
        created_at=lic.created_at,
        activated_at=lic.activated_at,
    )

# ============================================================
#  COINBASE COMMERCE PAYMENT START
# ============================================================

class CoinbaseStartRequest(BaseModel):
    install_id: str

@app.post("/license/pay/coinbase/start")
async def start_coinbase_payment(payload: CoinbaseStartRequest, db: Session = Depends(get_db)):

    install_id = payload.install_id.strip()
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    headers = {
        "Content-Type": "application/json",
        "X-CC-Api-Key": settings.coinbase_api_key,
        "X-CC-Version": "2018-03-22",
    }

    body = {
        "name": "Silent PRO License",
        "local_price": {
            "amount": str(settings.license_price_eur),
            "currency": "EUR",
        },
        "pricing_type": "fixed_price",
        "metadata": {"install_id": install_id}
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{settings.coinbase_api_url}/charges", json=body, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(502, "Errore creazione pagamento Coinbase")

    data = resp.json()["data"]
    lic = get_or_create_license(db, install_id)
    lic.last_invoice_id = data["id"]
    db.commit()

    return {"status": "ok", "checkout_url": data["hosted_url"]}

# ============================================================
#  COINBASE WEBHOOK
# ============================================================

def verify_coinbase_signature(raw: bytes, signature: str, secret: str):
    if not signature:
        return False
    mac = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature)

@app.post("/license/payment/coinbase")
async def coinbase_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("X-Cc-Webhook-Signature")

    if not verify_coinbase_signature(raw, sig, settings.coinbase_webhook_secret):
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(raw)
    event = payload.get("event", {})
    t = event.get("type")

    if t not in ["charge:confirmed", "charge:resolved"]:
        return {"status": "ignored"}

    data = event.get("data", {})
    install_id = data.get("metadata", {}).get("install_id")
    charge_id = data.get("id")

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
