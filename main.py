from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.websocket("/wss/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Messaggio ricevuto da {username}: {data}")
    except WebSocketDisconnect:
        print(f"{username} disconnesso.")
