import asyncio
import logging
import traceback
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query

app = FastAPI()

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
    await join_room(room, ws)

    # opzionale: messaggio di servizio
    try:
        await ws.send_text('{"type":"info","msg":"welcome","room":"%s"}' % room)
    except Exception:
        pass

    try:
        while True:
            try:
                packet = await ws.receive()  # {'type': 'websocket.receive', 'text': ...} oppure {'bytes': ...}
            except WebSocketDisconnect:
                logger.info("WS disconnect (WebSocketDisconnect): room=%s", room)
                break
            except RuntimeError as e:
                # Questo è l'errore che vedevi: il client ha già inviato 'disconnect'
                logger.info('WS disconnect (RuntimeError on receive): %s', e)
                break
            except Exception as e:
                logger.error("Unexpected receive exception: %s\n%s", e, traceback.format_exc())
                break

            # Alcune versioni possono comunque esporre il tipo evento:
            ev_type = packet.get("type")
            if ev_type == "websocket.disconnect":
                logger.info("WS disconnect event: room=%s", room)
                break

            if packet.get("text") is not None:
                text = packet["text"]
                # inoltra testo agli altri peer della stanza
                for peer in list(rooms.get(room, [])):
                    if peer is not ws:
                        await safe_send_text(peer, text, room)

            elif packet.get("bytes") is not None:
                data = packet["bytes"]
                # inoltra binario (immagini/audio) agli altri
                for peer in list(rooms.get(room, [])):
                    if peer is not ws:
                        await safe_send_bytes(peer, data, room)

            else:
                # keep-alive / ignora
                await asyncio.sleep(0)

    finally:
        await leave_room(room, ws)
