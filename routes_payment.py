from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import SessionLocal, Payment
from datetime import datetime, timedelta

router = APIRouter()


class PaymentStartResponse(BaseModel):
    btc_address: str
    amount_btc: float


@router.post("/payment/start")
async def start_payment(install_id: str):
    """
    Genera indirizzo e importo fittizio per la demo.
    """
    try:
        db = SessionLocal()

        pay = Payment(
            install_id=install_id,
            btc_address="bc1qexampleaddressxxxxxxxxxxxxx",
            amount_btc=0.00050,
            status="pending",
            created_at=datetime.utcnow()
        )
        db.add(pay)
        db.commit()

        return PaymentStartResponse(
            btc_address=pay.btc_address,
            amount_btc=pay.amount_btc
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payment/status")
async def payment_status(install_id: str):
    """
    Restituisce lo stato del pagamento associato all’install_id.
    """
    db = SessionLocal()
    pay = db.query(Payment).filter(Payment.install_id == install_id).first()

    if not pay:
        return {"status": "none"}

    return {"status": pay.status}
