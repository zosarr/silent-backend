from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str = "anonymous"):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Messaggio ricevuto da {username}: {data}")
    except WebSocketDisconnect:
        print(f"{username} disconnesso.")
    except Exception as e:
        print(f"Errore durante la gestione della connessione di {username}: {e}")
