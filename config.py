from pydantic_settings import BaseSettings
from decimal import Decimal

class Settings(BaseSettings):
    LICENSE_PRICE_EUR: Decimal = Decimal("2.99")
    BTC_ADDRESS: str = "15Vf5fmhY4uihXWkSvd91aSsDaiZdUkVN8"

    class Config:
        env_file = ".env"

settings = Settings()
