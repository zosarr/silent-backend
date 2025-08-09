from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set
import asyncio

app = FastAPI()
rooms: Dict[str, Set[WebSocket]] = {}

@app.get("/")
async def root():
    return {"status": "ok"}

async def join_room(room: str, ws: WebSocket):
    await ws.accept()
    rooms.setdefault(room, set()).add(ws)

async def leave_room(room: str, ws: WebSocket):
    conns = rooms.get(room)
    if conns and ws in conns:
        conns.remove(ws)
        if not conns:
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
    await ws.send_text('{"type":"info","msg":"welcome","room":"%s"}' % room)

    async def keepalive():
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send_text('{"type":"ping"}')
            except Exception:
                break
    ka = asyncio.create_task(keepalive())

    try:
        while True:
            data = await ws.receive_text()
            await broadcast(room, data, sender=ws)
    except WebSocketDisconnect:
        pass
    finally:
        ka.cancel()
        await leave_room(room, ws)
