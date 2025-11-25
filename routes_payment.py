from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
import httpx
import logging

from main import (
    settings, 
    get_db, 
    Order, 
    License, 
    LicenseStatus, 
)

router = APIRouter()

# ============================================================
#   CONFIGURAZIONE PAGAMENTO BTC
# ============================================================

BTC_FIXED_ADDRESS = "15Vf5fmhY4uihXWkSvd91aSsDaiZdUkVN8"

PRICE_EUR = 2.99
PAYMENT_VALID_MINUTES = 60   # 1 ora

logger = logging.getLogger("btc-payment")

# ============================================================
#   1) /payment/create - genera ordine BTC
# ============================================================

@router.post("/payment/create")
async def payment_create(install_id: str, db: Session = Depends(get_db)):

    install_id = install_id.strip()
    if not install_id:
        raise HTTPException(400, "install_id mancante")

    # ---- 1) recupera prezzo BTC/EUR da Binance ----
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCEUR"
            )
        btc_eur_price = float(r.json()["price"])
    except Exception as e:
        logger.error(f"Errore Binance: {e}")
        raise HTTPException(502, "Errore recupero prezzo BTC")

    # ---- 2) calcola importo BTC per 2.99 € ----
    amount_btc = round(PRICE_EUR / btc_eur_price, 8)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=PAYMENT_VALID_MINUTES)

    # ---- 3) salva ordine nel DB ----
    order = Order(
        install_id=install_id,
        amount_btc=str(amount_btc),
        address=BTC_FIXED_ADDRESS,
        created_at=now,
        expires_at=expires,
        paid=0,
        txid=None,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # ---- 4) ritorna all’app ----
    return {
        "status": "ok",
        "order_id": order.id,
        "btc_address": BTC_FIXED_ADDRESS,
        "amount_btc": amount_btc,
        "expires_at": expires.isoformat(),
        "qr": f"bitcoin:{BTC_FIXED_ADDRESS}?amount={amount_btc}"
    }


# ============================================================
#   2) /payment/check — verifica pagamento sulla blockchain
# ============================================================

@router.get("/payment/check")
async def payment_check(order_id: int, db: Session = Depends(get_db)):

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Ordine non trovato")

    if order.paid:
        return {"paid": True, "txid": order.txid}

    now = datetime.now(timezone.utc)
    if order.expires_at < now:
        return {"paid": False, "expired": True}

    # ---- Controllo blockchain via Blockstream ----
    url = f"https://blockstream.info/api/address/{BTC_FIXED_ADDRESS}/utxo"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
        utxos = r.json()
    except:
        raise HTTPException(502, "Errore blockchain")

    # ---- Cerca una transazione con importo corretto ----
    required_sats = int(float(order.amount_btc) * 100_000_000)

    for tx in utxos:
        if tx.get("value") == required_sats:
            # PAGAMENTO TROVATO --------
            order.paid = 1
            order.txid = tx.get("txid")

            # attiva licenza PRO
            lic = db.query(License).filter(License.install_id == order.install_id).first()
            if lic:
                lic.status = LicenseStatus.PRO
                lic.activated_at = datetime.now(timezone.utc)

            db.commit()

            return {"paid": True, "txid": tx.get("txid")}

    return {"paid": False, "expired": False}
