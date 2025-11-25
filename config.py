from pydantic_settings import BaseSettings
from decimal import Decimal
import os

class Settings(BaseSettings):
    # === DATABASE ===
    database_url: str = "sqlite:////opt/silent-backend/silent.db"

    # === TRIAL ===
    trial_hours: int = 24

    # === PREZZO LICENZA IN EURO ===
    btc_amount_eur: Decimal = Decimal("2.99")

    # === INDIRIZZO BTC FISSO ===
    btc_address: str = "15Vf5fmhY4uihXWkSvd91aSsDaiZdUkVN8"   # <-- METTI IL TUO

    # === BLOCKCHAIN API ===
    blockstream_url: str = "https://blockstream.info/api"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
