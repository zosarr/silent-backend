from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
from typing import Dict

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_users: Dict[str, WebSocket] = {}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    connected_users[username] = websocket
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            to_user = message["to"]
            if to_user in connected_users:
                await connected_users[to_user].send_text(json.dumps({
                    "from": username,
                    "message": message["message"]
                }))
    except WebSocketDisconnect:
        connected_users.pop(username, None)
