# main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set, Optional
import asyncio, json, secrets, time

app = FastAPI()

# Stato in memoria
rooms: Dict[str, Set[WebSocket]] = {}
meta: Dict[WebSocket, dict] = {}  # { id, room, joinedAt, lastSeen, tokens, lastRefill }

PING_EVERY_MS = 30_000            # ping server→client
DROP_IF_SILENT_MS = 70_000        # se nessuna attività (pong o msg) per ~2 ping → drop
MSGS_PER_SEC = 20                 # rate limit (token bucket)
BURST = 40
MAX_MSG_BYTES = 2_000_000         # ~2MB (protezione: per immagini/audio meglio mandare URL, non binario su WS)

def now_ms() -> int:
    return int(time.time() * 1000)

def in_room(room: str) -> Set[WebSocket]:
    if room not in rooms:
        rooms[room] = set()
    return rooms[room]

def broadcast(room: str, payload: dict, except_ws: Optional[WebSocket] = None):
    data = json.dumps(payload)
    dead = []
    for ws in list(rooms.get(room, ())):
        if ws is except_ws:
            continue
        try:
            asyncio.create_task(ws.send_text(data))
        except Exception:
            dead.append(ws)
    for ws in dead:
        asyncio.create_task(leave_room(meta.get(ws, {}).get("room"), ws))

def snapshot(room: str) -> dict:
    members = []
    for ws in rooms.get(room, ()):
        m = meta.get(ws)
        if not m: 
            continue
        members.append({"id": m["id"], "joinedAt": m["joinedAt"]})
    return {"type": "presence", "room": room, "members": members, "ts": now_ms()}

async def join_room(room: str, ws: WebSocket):
    await ws.accept()
    in_room(room).add(ws)
    meta[ws] = {
        "id": secrets.token_urlsafe(6),
        "room": room,
        "joinedAt": now_ms(),
        "lastSeen": now_ms(),
        "tokens": BURST,
        "lastRefill": time.time(),
    }

async def leave_room(room: Optional[str], ws: WebSocket):
    try:
        peers = rooms.get(room or "", None)
        if peers and ws in peers:
            peers.remove(ws)
            if not peers:
                rooms.pop(room, None)
        m = meta.pop(ws, None)
        if m and room:
            broadcast(room, {"type": "leave", "id": m["id"], "ts": now_ms()})
    except Exception:
        pass

def consume_rate(ws: WebSocket) -> bool:
    m = meta.get(ws)
    if not m:
        return True
    now = time.time()
    elapsed = now - m["lastRefill"]
    m["lastRefill"] = now
    m["tokens"] = min(BURST, m["tokens"] + elapsed * MSGS_PER_SEC)
    if m["tokens"] < 1:
        return False
    m["tokens"] -= 1
    return True

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "ok"}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, room: str = Query("default")):
    await join_room(room, ws)
    me = meta[ws]
    # Benvenuto + presenza iniziale
    await ws.send_text(json.dumps({"type": "joined", "id": me["id"], "room": room, "ts": now_ms()}))
    await ws.send_text(json.dumps(snapshot(room)))
    broadcast(room, {"type": "join", "id": me["id"], "ts": now_ms()}, except_ws=ws)

    # Heartbeat: ping periodico
    async def pinger():
        while True:
            await asyncio.sleep(PING_EVERY_MS / 1000)
            try:
                await ws.send_text(json.dumps({"type": "ping", "ts": now_ms()}))
            except Exception:
                break

    # Watchdog: chiude se silenzioso
    async def watchdog():
        while True:
            await asyncio.sleep(1.0)
            m = meta.get(ws)
            if not m:
                break
            if now_ms() - m["lastSeen"] > DROP_IF_SILENT_MS:
                try:
                    await ws.close()
                except Exception:
                    pass
                break

    ping_task = asyncio.create_task(pinger())
    wd_task = asyncio.create_task(watchdog())

    try:
        while True:
            data = await ws.receive_text()
            if len(data.encode("utf-8")) > MAX_MSG_BYTES:
                # troppo grande: ignora silenziosamente
                continue

            # Prova a decodare JSON
            msg = None
            try:
                msg = json.loads(data)
            except Exception:
                pass

            # Attività vista
            if ws in meta:
                meta[ws]["lastSeen"] = now_ms()

            # Rate-limit
            if not consume_rate(ws):
                continue

            # Gestione ping/pong applicativo
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "ts": now_ms()}))
                continue
            if isinstance(msg, dict) and msg.get("type") == "pong":
                # solo aggiorna lastSeen
                continue

            # Broadcast messaggi applicativi (E2E-agnostico)
            if isinstance(msg, dict):
                envelope = {**msg, "from": me["id"], "ts": now_ms()}
                broadcast(room, envelope, except_ws=ws)
            else:
                # Non-JSON → inoltra raw con mittente
                broadcast(room, json.loads(json.dumps({
                    "type": "raw",
                    "from": me["id"],
                    "payload": data,
                    "ts": now_ms()
                })), except_ws=ws)

    except WebSocketDisconnect:
        pass
    finally:
        ping_task.cancel()
        wd_task.cancel()
        await leave_room(room, ws)
