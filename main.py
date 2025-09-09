import asyncio
import logging
import traceback
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query

app = FastAPI()

# Log più verboso
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("silent-backend")

rooms: Dict[str, Set[WebSocket]] = {}

async def join_room(room: str, ws: WebSocket):
    await ws.accept()
    rooms.setdefault(room, set()).add(ws)
    logger.info("WS joined: room=%s peers=%d", room, len(rooms[room]))

async def leave_room(room: str, ws: WebSocket):
    peers = rooms.get(room)
    if peers and ws in peers:
        peers.remove(ws)
        logger.info("WS left: room=%s peers=%d", room, len(peers))
        if not peers:
            rooms.pop(room, None)

async def safe_send_text(peer: WebSocket, text: str, room: str):
    try:
        await peer.send_text(text)
    except Exception as e:
        logger.warning("send_text failed, removing peer: %s", e)
        await leave_room(room, peer)

async def safe_send_bytes(peer: WebSocket, data: bytes, room: str):
    try:
        await peer.send_bytes(data)
    except Exception as e:
        logger.warning("send_bytes failed, removing peer: %s", e)
        await leave_room(room, peer)

@app.get("/")
async def root():
    return {"status": "ok"}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, room: str = Query("default")):
    # Accetta connessione e registra il peer
    await join_room(room, ws)

    # (facoltativo) messaggio di benvenuto di servizio
    try:
        await ws.send_text('{"type":"info","msg":"welcome","room":"%s"}' % room)
    except Exception:
        pass

    # loop ricezione/relay con gestione TEXT + BYTES
    try:
        while True:
            packet = await ws.receive()
            # packet è un dict; chiavi attese: 'text' o 'bytes'
            if packet.get("text") is not None:
                text = packet["text"]
                # inoltra agli altri
                for peer in list(rooms.get(room, [])):
                    if peer is not ws:
                        await safe_send_text(peer, text, room)

            elif packet.get("bytes") is not None:
                data = packet["bytes"]
                # inoltra agli altri
                for peer in list(rooms.get(room, [])):
                    if peer is not ws:
                        await safe_send_bytes(peer, data, room)

            else:
                # altri tipi/keepalive
                await asyncio.sleep(0)

    except WebSocketDisconnect:
        logger.info("WS disconnect: room=%s", room)

    except Exception as e:
        # LOG completo per capire l’errore reale su Render
        logger.error("Exception in WS loop: %s\n%s", e, traceback.format_exc())

    finally:
        await leave_room(room, ws)
