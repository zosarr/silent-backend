from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import logging

from database import Base, engine
from config import settings

# Routers
from routes_license import router as license_router
from routes_payment import router as payment_router

# =====================================================
#  INIT APP
# =====================================================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create DB tables
Base.metadata.create_all(bind=engine)

# Include routes
app.include_router(license_router)
app.include_router(payment_router)

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
            for conn in rooms[room]:
                if conn != ws:
                    await conn.send_text(data)
    except WebSocketDisconnect:
        rooms[room].remove(ws)
        if not rooms[room]:
            del rooms[room]

# Health check
@app.get("/")
def root():
    return {"status": "ok"}
