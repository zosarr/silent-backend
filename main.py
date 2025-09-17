from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set
import asyncio
import json

app = FastAPI()
rooms: Dict[str, Set[WebSocket]] = {}

PING_INTERVAL = 20  # seconds
PONG_TIMEOUT = 15   # seconds

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "ok"}

async def broadcast(room: str, data: str, sender: WebSocket | None = None):
    utenti connessi = rooms.get(room, set())
    dead = []
    for ws in utenti connessi:
        if ws is sender:
            continue
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        await leave_room(room, ws)

def presence_payload(room: str) -> str:
    count = len(rooms.get(room, set()))
    return json.dumps({"type": "presence", "room": room, "utenti connessi": count})

async def enter_room(room: str, ws: WebSocket):
    utenti connessi = rooms.setdefault(room, set())
    utenti connessi.add(ws)
    # Announce new presence to everyone
    await broadcast(room, presence_payload(room))

async def leave_room(room: str, ws: WebSocket):
    utenti connessi = rooms.get(room)
    if not utenti connessi:
        return
    utenti connessi.discard(ws)
    # Announce updated presence
    try:
        await broadcast(room, presence_payload(room))
    except Exception:
        pass
    if not utenti connessi:
        rooms.pop(room, None)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, room: str = Query("test")):
    await ws.accept()
    await enter_room(room, ws)

    # Per-connection state
    last_pong = asyncio.get_event_loop().time()

    async def pinger():
        nonlocal last_pong
        while True:
            try:
                await ws.send_text(json.dumps({"type": "ping"}))
            except Exception:
                # Connection is dead; cleanup handled below
                break
            # Wait and check for pong
            await asyncio.sleep(PING_INTERVAL)
            if asyncio.get_event_loop().time() - last_pong > PONG_TIMEOUT + PING_INTERVAL:
                # Didn't receive pong in time; close
                try:
                    await ws.close()
                finally:
                    break

    task = asyncio.create_task(pinger())

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                # Non-JSON payloads are just relayed
                await broadcast(room, data, sender=ws)
                continue

            t = msg.get("type")

            # Client responded to keepalive
            if t == "pong":
                last_pong = asyncio.get_event_loop().time()
                continue

            # If client sends ping, reply (no broadcast)
            if t == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                continue

            # Do not broadcast presence/pong messages
            if t in {"presence", "pong"}:
                continue

            # Relay application messages to others in the room
            await broadcast(room, data, sender=ws)

    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await leave_room(room, ws)
