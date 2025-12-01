from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set

from database import Base, engine
from config import settings

# Routers
from routes_license import router as license_router
from routes_payment import router as payment_router


# ============================
# INIT APP
# ============================
app = FastAPI()

# Create tables
Base.metadata.create_all(bind=engine)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://silentpwa.com",
        "https://www.silentpwa.com",
        "https://silent-pwa.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(license_router)
app.include_router(payment_router)


# ============================
# HEALTH
# ============================
@app.get("/")
def root():
    return {"status": "ok", "service": "silent-backend"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ============================
# WEBSOCKETS
# ============================
rooms: Dict[str, Set[WebSocket]] = {}
@app.websocket("/ws/{room}")
async def ws_endpoint(ws: WebSocket, room: str):

    origin = ws.headers.get("origin")

    allowed = {
        "https://silentpwa.com",
        "https://www.silentpwa.com",
        "https://silent-pwa.netlify.app"
    }

    # Se origin non è valido → chiudi
    if origin not in allowed:
        await ws.close(code=403)
        return

    # Accetta WebSocket
    await ws.accept()

    if room not in rooms:
        rooms[room] = set()

    rooms[room].add(ws)

    try:
        while True:
            data = await ws.receive_text()

            # Broadcast a tutti tranne il mittente
            for conn in list(rooms[room]):
                if conn != ws:
                    await conn.send_text(data)

    except WebSocketDisconnect:
        rooms[room].remove(ws)
        if not rooms[room]:
            del rooms[room]

