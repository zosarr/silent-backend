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
    # messaggio di benvenuto/keepalive puoi lasciarlo se vuoi

    try:
        while True:
            packet = await ws.receive()
            if packet.get("text") is not None:
                text = packet["text"]
                # inoltra testo
                for peer in list(rooms.get(room, [])):
                    if peer is not ws:
                        await peer.send_text(text)
            elif packet.get("bytes") is not None:
                b = packet["bytes"]
                # inoltra binario (immagini, ecc.)
                for peer in list(rooms.get(room, [])):
                    if peer is not ws:
                        await peer.send_bytes(b)
    except WebSocketDisconnect:
        pass
    finally:
        await leave_room(room, ws)
