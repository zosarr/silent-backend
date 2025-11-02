# silent-backend-main/silent-backend-main/models.py
from sqlalchemy import Column, String, DateTime, Enum
from sqlalchemy.orm import declarative_base
import enum

Base = declarative_base()

class LicenseStatus(str, enum.Enum):
    trial = "trial"
    pro = "pro"
    blocked = "blocked"

class License(Base):
    __tablename__ = "licenses"

    install_id = Column(String, primary_key=True, index=True)
    status = Column(Enum(LicenseStatus), nullable=False)

    # DateTime con timezone=True per confronti corretti (UTC)
    trial_started_at = Column(DateTime(timezone=True), nullable=False)
    trial_expires_at = Column(DateTime(timezone=True), nullable=False)
    pro_activated_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # profilo limiti (opzionale) per la demo
    limits_profile = Column(String, nullable=True)
