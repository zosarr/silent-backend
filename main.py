from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set
import asyncio

app = FastAPI()
rooms: Dict[str, Set[WebSocket]] = {}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}
@app.get("/")
async def root():
    return {"status": "ok"}

async def join_room(room: str, ws: WebSocket):
    await ws.accept()
    rooms.setdefault(room, set()).add(ws)

async def leave_room(room: str, ws: WebSocket):
    peers = rooms.get(room)
    if peers and ws in peers:
        peers.remove(ws)
        if not peers:
            rooms.pop(room, None)

async def broadcast(room: str, msg: str, sender: WebSocket):
    for peer in list(rooms.get(room, [])):
        if peer is not sender:
            try:
                await peer.send_text(msg)
            except Exception:
                await leave_room(room, peer)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, room: str = Query("default")):
    await join_room(room, ws)
    # benvenuto + keepalive
    await ws.send_text('{"type":"info","msg":"welcome","room":"%s"}' % room)

    async def ka():
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send_text('{"type":"ping"}')
            except Exception:
                break
    task = asyncio.create_task(ka())

    try:
        while True:
            data = await ws.receive_text()
            await broadcast(room, data, sender=ws)
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await leave_room(room, ws)
