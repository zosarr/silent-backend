from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import asyncio
import json
import os
import enum
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum
from sqlalchemy.orm import sessionmaker, declarative_base, Session

  



# ============================================================
#   FASTAPI SETUP
# ============================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# NUOVO IMPORT
from routes_payment import router as payment_router
from routes_license import router as license_router
# Includi router
app.include_router(license_router)
app.include_router(payment_router)  
# ============================================================
#   ROUTER LICENZE
# ============================================================

from routes_license import router as license_router
app.include_router(license_router)

# (I router del pagamento BTC li aggiungeremo dopo)
# from routes_payment import router as payment_router
# app.include_router(payment_router)

# ============================================================
#  WEBSOCKET CHAT
# ============================================================

rooms: Dict[str, Set[WebSocket]] = {}
PING_INTERVAL = 20

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"status": "ok", "message": "Silent Backend attivo"}

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

logger = logging.getLogger("silent")
logging.basicConfig(level=logging.INFO)

class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./silent.db")
    trial_hours: int = int(os.getenv("TRIAL_HOURS", "24"))
    license_price_eur: Decimal = Decimal("2.99")  # prezzo aggiornato

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

# ============================================================
#   MODELLI DATABASE (License + Order)
# ============================================================

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

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    install_id = Column(String, index=True)
    amount_btc = Column(String)      
    address = Column(String)         
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True))
    paid = Column(Integer, default=0)
    txid = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)

# ============================================================
#   FUNZIONI LICENZE
# ============================================================

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
