import os
from decimal import Decimal
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./silent.db")
    trial_hours: int = int(os.getenv("TRIAL_HOURS", "24"))
    license_price_eur: Decimal = Decimal(os.getenv("LICENSE_PRICE_EUR", "10"))

    coinbase_api_key: str = os.getenv("COINBASE_API_KEY", "")
    coinbase_webhook_secret: str = os.getenv("COINBASE_WEBHOOK_SECRET", "")
    coinbase_api_url: str = os.getenv("COINBASE_API_URL", "https://api.commerce.coinbase.com")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
