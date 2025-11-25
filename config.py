from pydantic_settings import BaseSettings
from decimal import Decimal
import os

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////opt/silent-backend/silent.db")

    # Licenza
    TRIAL_HOURS: int = int(os.getenv("TRIAL_HOURS", "24"))

    # Prezzo in EUR
    LICENSE_PRICE_EUR: Decimal = Decimal(os.getenv("LICENSE_PRICE_EUR", "2.99"))

    # Pagamenti BTC
    BTC_ADDRESS: str = os.getenv("BTC_ADDRESS", "")
    BLOCKSTREAM_URL: str = os.getenv("BLOCKSTREAM_URL", "https://blockstream.info/api")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
