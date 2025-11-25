from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum
from sqlalchemy.orm import sessionmaker, declarative_base
import enum
import logging

# === CONFIG ===
from config import settings

# === ROUTES ===
from routes_payment import router as payment_router
from routes_license import router as license_router

# =============================
#  CREA L’APP
# =============================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Puoi restringere in futuro
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================
#  WEBSOCKET CHAT
# =============================

rooms: Dict[str, Set[WebSocket]] = {}

@app.get("/")
def root():
    return {"status": "ok", "msg": "Silent Backend Attivo (BTC mode)"}

@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str):
    await websocket.accept()

    rooms.setdefault(room, set()).add(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            for ws in list(rooms[room]):
                if ws is not websocket:
                    await ws.send_text(data)
    except WebSocketDisconnect:
        pass
    finally:
        rooms[room].remove(websocket)
        if not rooms[room]:
            rooms.pop(room, None)

# =============================
#  DATABASE
# =============================

logger = logging.getLogger("silent-licenses")
logging.basicConfig(level=logging.INFO)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
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

Base.metadata.create_all(bind=engine)

# DB dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# =============================
#  LICENCE API
# =============================

def get_or_create_license(db: Session, install_id: str):
    lic = db.query(License).filter_by(install_id=install_id).first()
    if not lic:
        lic = License(install_id=install_id)
        db.add(lic)
        db.commit()
    return lic

def compute_effective_status(lic: License):
    now = datetime.now(timezone.utc)

    if lic.status == LicenseStatus.PRO:
        return LicenseStatus.PRO

    # scaduto il trial?
    if now - lic.created_at > timedelta(hours=settings.trial_hours):
        return LicenseStatus.DEMO

    return LicenseStatus.TRIAL

@app.get("/license/status")
def license_status(install_id: str, db: Session = Depends(get_db)):
    lic = get_or_create_license(db, install_id)
    eff = compute_effective_status(lic)

    if eff == LicenseStatus.TRIAL:
        expires = lic.created_at + timedelta(hours=settings.trial_hours)
        left = (expires - datetime.now(timezone.utc)).total_seconds() / 3600
    else:
        left = 0

    return {
        "status": eff.value,
        "trial_hours_total": settings.trial_hours,
        "trial_hours_left": max(0, left),
        "created_at": lic.created_at,
        "activated_at": lic.activated_at
    }

@app.post("/license/register")
def license_register(data: dict, db: Session = Depends(get_db)):
    install_id = data.get("install_id")
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    get_or_create_license(db, install_id)
    return {"status": "ok"}

@app.post("/license/activate")
def activate_license(data: dict, db: Session = Depends(get_db)):
    install_id = data.get("install_id")
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    lic = get_or_create_license(db, install_id)
    lic.status = LicenseStatus.PRO
    lic.activated_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok", "message": "License upgraded to PRO"}

# =============================
#  BTC PAYMENT ROUTES
# =============================

app.include_router(payment_router)

# =============================
#  LICENZA (REGOLE FRONTEND)
# =============================

app.include_router(license_router)
