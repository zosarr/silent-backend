# silent-backend-main/silent-backend-main/main.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
from datetime import datetime, timezone as tz

from routes_license import router as license_router
from routes_webhooks import router as webhooks_router
from db import SessionLocal
from models import License, LicenseStatus
app = FastAPI(title="Silent Messaging Backend")

# CORS - consenti al PWA di connettersi
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://silent-pwa.netlify.app",  # dominio PWA su Render
        "http://localhost:5173",            # se fai test in locale
    ],
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
    # main.py (aggiunte in coda ai tuoi import)
from fastapi import Depends
from sqlalchemy import text
from db import SessionLocal, engine

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    # ping DB: se fallisce, Render segna unhealthy
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

