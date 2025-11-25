from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from decimal import Decimal

from config import settings
from database import get_db
from models import License, LicenseStatus

import httpx

router = APIRouter(prefix="/payment", tags=["payment"])

# ================================================
#  CREA PAY REQUEST
# ================================================

@router.post("/start")
async def payment_start(install_id: str, db: Session = Depends(get_db)):

    if not install_id:
        raise HTTPException(400, "install_id mancante")

    # Calcolo importo in BTC tramite API blockchain.info
    url = "https://blockchain.info/tobtc?currency=EUR&value=" + str(settings.LICENSE_PRICE_EUR)

    async with httpx.AsyncClient() as client:
        btc_amount = await client.get(url)
        btc_amount = btc_amount.text

    # Salva sul DB
    lic = db.query(License).filter_by(install_id=install_id).first()
    if not lic:
        lic = License(install_id=install_id)
        db.add(lic)
    lic.last_invoice_id = "btc-manual"
    db.commit()

    return {
        "status": "ok",
        "btc_address": settings.BTC_ADDRESS,
        "amount_btc": btc_amount
    }
