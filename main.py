from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict

app = FastAPI()
connected_clients: Dict[str, WebSocket] = {}

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    if not username or username in connected_clients:
        await websocket.close(code=4000, reason="Username non valido o già in uso")
        return

    await websocket.accept()
    connected_clients[username] = websocket
    print(f"{username} connesso. Client totali: {len(connected_clients)}")

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Messaggio ricevuto da {username}: {data}")
    except WebSocketDisconnect:
        connected_clients.pop(username, None)
        print(f"{username} disconnesso. Client totali: {len(connected_clients)}")
    except Exception as e:
        connected_clients.pop(username, None)
        print(f"Errore durante la gestione della connessione di {username}: {e}")
