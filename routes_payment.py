from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import httpx

from config import settings
from main import get_db, get_or_create_license, LicenseStatus

router = APIRouter(prefix="/payment", tags=["Payment"])

# ============================================================
# 1) CREAZIONE RICHIESTA DI PAGAMENTO BTC
# ============================================================

@router.post("/create")
async def create_payment(install_id: str, db: Session = Depends(get_db)):
    """
    Crea una richiesta BTC per la licenza PRO basata su:
    - indirizzo fisso Binance
    - importo in satoshi convertito da EUR
    - tempo limite 1 ora
    """

    if not install_id:
        raise HTTPException(400, "install_id mancante")

    # prezzo in euro
    eur_price = settings.license_price_eur

    # API Blockstream per ottenere prezzo BTC
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://blockstream.info/api/price")
            btc_price = r.json()["EUR"]  # prezzo 1 BTC in EUR
    except:
        raise HTTPException(500, "Impossibile ottenere prezzo BTC")

    # converto EUR → BTC
    btc_amount = float(eur_price) / float(btc_price)

    # converto BTC → satoshi
    satoshi = int(btc_amount * 100_000_000)

    payment = {
        "address": settings.btc_address,   # indirizzo Binance fisso
        "amount_satoshi": satoshi,
        "expire_at": datetime.now(timezone.utc).timestamp() + 3600,
        "install_id": install_id
    }

    return payment


# ============================================================
# 2) CONTROLLO PAGAMENTO (usato dal frontend ogni 10s)
# ============================================================

@router.get("/check")
async def check_payment(address: str, amount: int, install_id: str, db: Session = Depends(get_db)):
    """
    Controlla se il pagamento BTC è stato effettuato.
    """

    if not address or not amount:
        raise HTTPException(400, "Parametri mancanti")

    # API blockstream — controlla transazioni ricevute
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"https://blockstream.info/api/address/{address}"
            r = await client.get(url)
            data = r.json()
    except:
        raise HTTPException(500, "Errore durante il controllo transazione")

    # somma totale ricevuta
    received = data.get("chain_stats", {}).get("funded_txo_sum", 0)

    # se ricevuto >= amount richiesto → attiva licenza PRO
    if received >= amount:
        lic = get_or_create_license(db, install_id)
        lic.status = LicenseStatus.PRO
        lic.activated_at = datetime.now(timezone.utc)
        db.commit()

        return {"paid": True, "status": "upgraded"}

    return {"paid": False}
