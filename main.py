from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set

from database import Base, engine
from config import settings

# Routers
from routes_license import router as license_router
from routes_payment import router as payment_router

# =====================================================
#  INIT APP
# =====================================================
app = FastAPI()

# Create DB tables on startup
Base.metadata.create_all(bind=engine)

# Include routes
app.include_router(license_router)
app.include_router(payment_router)

# =====================================================
#  HEALTH CHECK
# =====================================================

@app.get("/")
def root():
    return {"status": "ok", "service": "silent-backend"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# =====================================================
#  WEBSOCKETS
# =====================================================

rooms: Dict[str, Set[WebSocket]] = {}

@app.websocket("/ws/{room}")
async def ws_endpoint(ws: WebSocket, room: str):
    await ws.accept()

    rooms.setdefault(room, set()).add(ws)

    try:
        while True:
            data = await ws.receive_text()

            # broadcast to all except sender
            for conn in rooms[room]:
                if conn != ws:
                    await conn.send_text(data)

    except WebSocketDisconnect:
        rooms[room].remove(ws)
        if not rooms[room]:
            del rooms[room]
