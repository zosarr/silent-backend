from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:////opt/silent-backend/silent.db"

    # Licenza trial
    trial_hours: int = 24

    # Prezzo in euro
    license_price_eur: float = 2.99

    # Pagamento Bitcoin
    btc_address: str = "15Vf5fmhY4uihXWkSvd91aSsDaiZdUkVN8"
    blockstream_url: str = "https://blockstream.info/api"

    # Convertito automaticamente quando il client richiede il pagamento
    btc_amount_eur: float = 0.0

    class Config:
        extra = "allow"   # permette variabili extra senza errori
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
