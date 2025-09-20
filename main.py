from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query ,Request, Body
from typing import Dict, Set
import asyncio
import json

app = FastAPI()
PUSH_SUBSCRIPTIONS = set()
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
    peers = rooms.get(room, set())
    dead = []
    for ws in peers:
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
    return json.dumps({"type": "presence", "room": room, "peers": count})

async def enter_room(room: str, ws: WebSocket):
    peers = rooms.setdefault(room, set())
    peers.add(ws)
    # Announce new presence to everyone
    await broadcast(room, presence_payload(room))

async def leave_room(room: str, ws: WebSocket):
    peers = rooms.get(room)
    if not peers:
        return
    peers.discard(ws)
    # Announce updated presence
    try:
        await broadcast(room, presence_payload(room))
    except Exception:
        pass
    if not peers:
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


@app.get("/vapid-public-key")
async def vapid_public_key():
    pub = os.environ.get("VAPID_PUBLIC_KEY", "")
    return {"publicKey": pub}


@app.post("/push/subscribe")
async def push_subscribe(payload: dict = Body(...)):
    # payload is the PushSubscription JSON
    try:
        sub_json = json.dumps(payload, sort_keys=True)
        PUSH_SUBSCRIPTIONS.add(sub_json)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/push/unsubscribe")
async def push_unsubscribe(payload: dict = Body(...)):
    try:
        sub_json = json.dumps(payload, sort_keys=True)
        if sub_json in PUSH_SUBSCRIPTIONS: PUSH_SUBSCRIPTIONS.remove(sub_json)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/push/send-test")
async def push_send_test(payload: dict = Body({"title":"Silent","body":"Test notifica"})):
    vapid_pub = os.environ.get("VAPID_PUBLIC_KEY", "")
    vapid_priv = os.environ.get("VAPID_PRIVATE_KEY", "")
    if not vapid_pub or not vapid_priv:
        return JSONResponse(status_code=500, content={"ok": False, "error": "Missing VAPID keys in env"})
    sent = 0; failed = 0
    for sub_json in list(PUSH_SUBSCRIPTIONS):
        try:
            sub = json.loads(sub_json)
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=vapid_priv,
                vapid_claims={"sub": "mailto:admin@example.com"}
            )
            sent += 1
        except Exception as e:
            failed += 1
    return {"ok": True, "sent": sent, "failed": failed}
