from fastapi import APIRouter, HTTPException, Depends
import httpx
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from sqlalchemy.orm import Session

from main import get_db, get_or_create_license, LicenseStatus
from config import settings

router = APIRouter()

BTC_RECEIVE_ADDRESS = "15Vf5fmhY4uihXWkSvd91aSsDaiZdUkVN8"
LICENSE_PRICE_BTC = settings.license_price_btc if hasattr(settings, "license_price_btc") else 0.00025

# ===========================================================
# REQUEST: CREATE PAYMENT
# ===========================================================

class PaymentRequest(BaseModel):
    install_id: str


@router.get("/payment/create")
def create_payment(install_id: str, db: Session = Depends(get_db)):
    """
    PREPARA IL PAGAMENTO:
    - Salva richieste pagamento
    - Ritorna indirizzo, importo e timestamp
    """

    lic = get_or_create_license(db, install_id)
    lic.last_invoice_id = f"BTC-{install_id}-{datetime.now().timestamp()}"
    db.commit()

    return {
        "btc_address": BTC_RECEIVE_ADDRESS,
        "amount_btc": LICENSE_PRICE_BTC,
        "invoice_id": lic.last_invoice_id
    }


# ===========================================================
# CHECK TRANSACTION (POLLING)
# ===========================================================

@router.get("/payment/status")
async def payment_status(install_id: str, db: Session = Depends(get_db)):
    """
    CONTROLLA SE CI SONO UTXO SU QUESTO INDIRIZZO
    Se esiste 1 transazione >= importo richiesto → PRO
    """

    lic = get_or_create_license(db, install_id)
    address = BTC_RECEIVE_ADDRESS

    # Blockstream API
    url = f"https://blockstream.info/api/address/{address}/utxo"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(url)

        utxos = res.json()
    except Exception:
        raise HTTPException(500, "Errore connessione Blockstream")

    # Controlliamo transazioni
    paid = False
    for utxo in utxos:
        # valore in satoshi → converti a BTC
        btc = utxo["value"] / 100_000_000
        if btc >= LICENSE_PRICE_BTC:
            paid = True
            break

    if paid:
        lic.status = LicenseStatus.PRO
        lic.activated_at = datetime.now(timezone.utc)
        db.commit()
        return {"status": "pro"}

    return {"status": lic.status.value}
