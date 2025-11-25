import os
from decimal import Decimal
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # === DATABASE ===
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./silent.db")

    # === LICENZA ===
    trial_hours: int = int(os.getenv("TRIAL_HOURS", "24"))

    # prezzo in EUR
    license_price_eur: Decimal = Decimal(os.getenv("LICENSE_PRICE_EUR", "2.99"))

    # === BITCOIN PAYMENT ===
    # indirizzo BTC fisso (Binance)
    btc_address: str = os.getenv("BTC_ADDRESS", "15Vf5fmhY4uihXWkSvd91aSsDaiZdUkVN8")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
