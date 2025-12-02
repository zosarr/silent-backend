from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set

from database import Base, engine
from config import settings

from routes_license import router as license_router
from routes_payment import router as payment_router


app = FastAPI()

Base.metadata.create_all(bind=engine)

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

app.include_router(license_router)
app.include_router(payment_router)


@app.get("/")
def root():
    return {"status": "ok", "service": "silent-backend"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


rooms: Dict[str, Set[WebSocket]] = {}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):

    room = ws.query_params.get("room")
    install_id = ws.query_params.get("install_id")

    if not room or not install_id:
        await ws.close(code=400)
        return

    origin = ws.headers.get("origin")

    allowed = {
        "https://silentpwa.com",
        "https://www.silentpwa.com",
        "https://silent-pwa.netlify.app",
    }

    if origin not in allowed:
        await ws.close(code=403)
        return

    await ws.accept()

    rooms.setdefault(room, set()).add(ws)

    try:
        while True:
            data = await ws.receive_text()

            for conn in list(rooms[room]):
                if conn != ws:
                    await conn.send_text(data)

    except WebSocketDisconnect:
        rooms[room].remove(ws)
        if not rooms[room]:
            del rooms[room]
