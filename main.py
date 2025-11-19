# silent-backend-main/silent-backend-main/main.py
from fastapi import Depends, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, Depends
from typing import Dict, Set
from pydantic import BaseSettings, BaseModel
import asyncio
import json

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

# =========================
# Licenze + Pagamenti BTCPay
# =========================

import hmac
import hashlib
import httpx
from decimal import Decimal
from fastapi import Request, HTTPException

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


# ============================================================
#                  ENDPOINT: CREA INVOICE BTCPAY
# ============================================================
@app.post("/license/pay/btcpay/start")
async def btcpay_start(data: dict):
    install_id = data.get("install_id")
    if not install_id:
        raise HTTPException(status_code=400, detail="missing install_id")

    amount = settings.license_price_eur

    url = f"{settings.btcpay_server}/api/v1/stores/{settings.btcpay_store_id}/invoices"

    headers = {
        "Authorization": f"token {settings.btcpay_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "amount": float(amount),
        "currency": "EUR",
        "metadata": {"install_id": install_id},
        "checkout": {"redirectURL": "https://silentpwa.com"},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        logger.error(f"Errore creazione invoice BTCPay: {resp.text}")
        raise HTTPException(status_code=500, detail="BTCPay error")

    invoice = resp.json()
    checkout_url = invoice.get("checkoutLink")

    return {"status": "ok", "checkout_url": checkout_url}


# ============================================================
#             ENDPOINT: WEBHOOK BTCPAY (pagamento)
# ============================================================
@app.post("/license/payment/btcpay")
async def btcpay_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("BTCPay-Sig")

    if not signature:
        raise HTTPException(status_code=400, detail="missing signature")

    secret = settings.btcpay_webhook_secret.encode()
    computed = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, signature):
        raise HTTPException(status_code=400, detail="invalid signature")

    payload = await request.json()
    event = payload.get("type")
    invoice = payload.get("invoice", {})
    metadata = invoice.get("metadata", {})
    install_id = metadata.get("install_id")

    if event == "InvoiceSettled" and install_id:
        db = SessionLocal()
        lic = db.query(License).filter(License.install_id == install_id).first()

        if lic:
            lic.status = "pro"
            lic.activated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Licenza attivata per install_id={install_id}")
        db.close()

    return {"status": "ok"}
