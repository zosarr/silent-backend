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

    id = Column(Integer, primary_key=True)
    install_id = Column(String, unique=True)
    status = Column(Enum(LicenseStatus), default=LicenseStatus.TRIAL)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime(timezone=True), nullable=True)
    last_invoice_id = Column(String, nullable=True)
