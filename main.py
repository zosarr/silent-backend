"""
Silent backend - Relay binario minimale orientato alla privacy

Caratteristiche:
- Accetta solo frame binari (riceve bytes) e li ritrasmette ai peer senza ispezionarli
- Non logga IP, dimensioni o contenuti dei messaggi
- Mantiene solo strutture in RAM (nessuna persistenza)
- Limiti semplici anti-DoS: dimensione massima per frame e rate-limit per connessione
- Endpoint /healthz per controllare che il servizio sia up (risposta minimale)
- Progettato per essere compatibile con il client privacy che invia ArrayBuffer cifrati

Note di privacy importanti:
- Questo relay *non* elimina l'informazione dell'IP (lo stack TCP la conserva). Per anonimato di rete usare Tor o un relay che rimuova gli IP.
- Il relay non decodifica i payload: non conosce metadati applicativi, ma vede comunque il volume e il timing del traffico.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status, HTTPException
import asyncio
import time
from typing import Set

app = FastAPI()

# Configurazioni di base
MAX_MESSAGE_SIZE = 1 * 1024 * 1024  # 1 MiB per singolo frame
RATE_LIMIT_WINDOW = 60.0  # secondi
MAX_MESSAGES_PER_WINDOW = 200   # massimo messaggi per finestra per singola connessione

# Stato globale in RAM
CONNS_LOCK = asyncio.Lock()
CONNS: Set[WebSocket] = set()

# Per rate limiting: mappa websocket -> list di timestamp dei messaggi recenti
_recent_msgs = {}  # websocket -> list[timestamp]


@app.get("/healthz")
async def healthz():
    # risposta minimale, senza metadata
    return {"status": "ok"}


async def _register(ws: WebSocket):
    async with CONNS_LOCK:
        CONNS.add(ws)
    # inizializza struttura rate-limit
    _recent_msgs[ws] = []


async def _unregister(ws: WebSocket):
    async with CONNS_LOCK:
        if ws in CONNS:
            CONNS.remove(ws)
    # pulizia rate-limit
    try:
        del _recent_msgs[ws]
    except KeyError:
        pass


def _record_and_check_rate(ws: WebSocket) -> bool:
    """
    Registra l'arrivo di un messaggio per ws e controlla se supera il rate-limit.
    Restituisce True se il messaggio è consentito, False se bisogna chiudere la connessione.
    """
    now = time.time()
    arr = _recent_msgs.get(ws)
    if arr is None:
        # connessione non registrata correttamente: consideriamo come violazione
        return False
    # rimuovi vecchi timestamp
    cutoff = now - RATE_LIMIT_WINDOW
    while arr and arr[0] < cutoff:
        arr.pop(0)
    arr.append(now)
    if len(arr) > MAX_MESSAGES_PER_WINDOW:
        return False
    return True


async def _broadcast(sender: WebSocket, data: bytes):
    """
    Ritrasmette `data` a tutti i peer connessi eccetto `sender`.
    Non logga nulla e non memorizza payload.
    """
    async with CONNS_LOCK:
        targets = [ws for ws in CONNS if ws is not sender]
    send_coros = []
    for ws in targets:
        # invio asincrono, ignorando errori di consegna (cleanup successivo)
        async def _send(w, d):
            try:
                await w.send_bytes(d)
            except Exception:
                # non loggare, segnaliamo al caller che la ws è da rimuovere
                raise
        send_coros.append(_send(ws, data))
    # invia in parallelo, gestendo eccezioni singole
    if not send_coros:
        return
    results = await asyncio.gather(*send_coros, return_exceptions=True)
    # rimuovi connessioni fallite
    failed = []
    for ws, res in zip(targets, results):
        if isinstance(res, Exception):
            failed.append(ws)
    if failed:
        async with CONNS_LOCK:
            for f in failed:
                if f in CONNS:
                    CONNS.remove(f)
                try:
                    del _recent_msgs[f]
                except KeyError:
                    pass


@app.websocket("/ws")
async def websocket_relay(ws: WebSocket):
    """
    Endpoint WebSocket main: accetta la connessione e fa da relay binario.
    Non fornisce alcuna autenticazione o gestione stanza.
    """
    # Accetta la websocket ma NON loggare headers o IP
    await ws.accept()
    await _register(ws)
    try:
        while True:
            # ricevi bytes (se non sono bytes -> chiudi)
            try:
                data = await ws.receive_bytes()
            except WebSocketDisconnect:
                break
            except Exception:
                # ricezione non-binaria o errore -> chiudi la connessione
                break

            # Controlli base di sicurezza
            # 1) Dimensione massima
            if len(data) > MAX_MESSAGE_SIZE:
                # chiudi per eccesso dimensione (do not log)
                try:
                    await ws.close(code=1009)  # message too big
                except Exception:
                    pass
                break

            # 2) Rate-limit (client-side)
            ok = _record_and_check_rate(ws)
            if not ok:
                try:
                    await ws.close(code=1013)  # try again later (server overload)
                except Exception:
                    pass
                break

            # 3) Ritrasmetti il frame ai peer senza ispezionarlo
            try:
                await _broadcast(ws, data)
            except Exception:
                # in caso di eccezioni durante broadcast, prosegui (i failed verranno rimossi)
                pass

    finally:
        # pulizia della connessione senza loggare nulla sensibile
        await _unregister(ws)
        try:
            await ws.close()
        except Exception:
            pass
