from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import uvicorn
import os

app = FastAPI(title="Silent WS Relay")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rooms: Dict[str, Set[WebSocket]] = {}

@app.get("/")
async def health():
    return {"status":"ok"}

async def join_room(room_id: str, ws: WebSocket):
    await ws.accept()
    rooms.setdefault(room_id, set()).add(ws)
    print(f"[join] {room_id} size={len(rooms[room_id])}")

async def leave_room(room_id: str, ws: WebSocket):
    peers = rooms.get(room_id)
    if peers and ws in peers:
        peers.remove(ws)
        print(f"[leave] {room_id} size={len(peers)}")

async def relay(room_id: str, ws: WebSocket, message, is_binary: bool):
    for peer in list(rooms.get(room_id, set())):
        if peer is ws:
            continue
        try:
            if is_binary:
                await peer.send_bytes(message)
            else:
                await peer.send_text(message)
        except Exception as e:
            # Drop broken connections
            await leave_room(room_id, peer)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, room: str):
    await join_room(room, websocket)
    try:
        while True:
            packet = await websocket.receive()
            data_text = packet.get("text")
            data_bytes = packet.get("bytes")
            if data_bytes is not None:
                await relay(room, websocket, data_bytes, True)
            elif data_text is not None:
                await relay(room, websocket, data_text, False)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass
    finally:
        await leave_room(room, websocket)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
