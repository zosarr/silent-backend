from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")
