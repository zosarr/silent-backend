from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import httpx
from decimal import Decimal

from config import settings
from main import get_db, get_or_create_license, LicenseStatus

router = APIRouter(prefix="/payment", tags=["payment"])

class StartPaymentRequest(BaseModel):
    install_id: str

@router.post("/start")
async def start_payment(req: StartPaymentRequest, db=Depends(get_db)):
    install_id = req.install_id.strip()
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    # Convert EUR → BTC
    async with httpx.AsyncClient() as client:
        price_resp = await client.get("https://blockchain.info/ticker")
        eur_price = Decimal(str(price_resp.json()["EUR"]["last"]))
        btc_amount = Decimal("2.99") / eur_price

    # Prepara licenza
    lic = get_or_create_license(db, install_id)

    return {
        "btc_address": settings.btc_address,
        "btc_amount": float(btc_amount),
        "checkout_id": install_id
    }

@router.get("/check")
async def check_payment(install_id: str, db=Depends(get_db)):
    lic = get_or_create_license(db, install_id)

    async with httpx.AsyncClient() as client:
        txs = await client.get(f"https://blockstream.info/api/address/{settings.btc_address}/txs")

    total_received = sum(
        float(o["value"]) for tx in txs.json()
        for o in tx["vout"]
        if settings.btc_address in o.get("scriptpubkey_address", "")
    ) / 100_000_000

    if total_received >= float(settings.min_btc):
        lic.status = LicenseStatus.PRO
        db.commit()
        return {"paid": True}

    return {"paid": False}
