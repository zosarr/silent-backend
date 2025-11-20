# models.py – versione compatibile con main.py + Coinbase Commerce

from sqlalchemy import Column, Integer, String, DateTime, Enum
from sqlalchemy.orm import declarative_base
import enum
from datetime import datetime, timezone


Base = declarative_base()


class LicenseStatus(str, enum.Enum):
    TRIAL = "trial"
    DEMO = "demo"
    PRO = "pro"


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    install_id = Column(String, unique=True, index=True, nullable=False)

    status = Column(Enum(LicenseStatus), nullable=False, default=LicenseStatus.TRIAL)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    activated_at = Column(DateTime(timezone=True), nullable=True)

    # Per Coinbase Commerce
    last_invoice_id = Column(String, nullable=True)
