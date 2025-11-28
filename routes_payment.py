from fastapi import APIRouter, HTTPException
from decimal import Decimal
import httpx
from config import settings

router = APIRouter(prefix="/payment", tags=["payment"])

@router.post("/start")
async def payment_start(install_id: str):
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    # EUR → BTC conversion
    url = "https://blockchain.info/tobtc?currency=EUR&value=" + str(settings.license_price_eur)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url)

    if r.status_code != 200:
        raise HTTPException(502, "Errore conversione EUR → BTC")

    btc_amount = Decimal(r.text)

    # Salva il valore globale
    settings.btc_amount_eur = float(btc_amount)

    return {
        "status": "ok",
        "btc_address": settings.btc_address,
        "amount_btc": str(btc_amount)
    }
# 🔥 WebSocket real-time notification
from main import rooms

room = install_id  # ogni utente ha la propria "stanza"
if room in rooms:
    for ws in rooms[room]:
        await ws.send_text("payment_confirmed")
