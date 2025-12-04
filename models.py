import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Enum
from database import Base

class LicenseStatus(str, enum.Enum):
    TRIAL = "trial"
    DEMO = "demo"
    PRO = "pro"

class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    install_id = Column(String, unique=True, index=True)

    status = Column(String, default="trial")     # trial / pro / demo
    created_at = Column(DateTime, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)

    # info pagamento BTC
    btc_address = Column(String, nullable=True)
    amount_btc = Column(String, nullable=True)
    invoice_id = Column(String, nullable=True)


