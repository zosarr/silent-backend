from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///./silent.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# =====================================================
#  MODELLI DATABASE
# =====================================================

class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    install_id = Column(String, index=True, unique=True)
    mode = Column(String)                      # demo | trial | pro
    trial_started = Column(DateTime, nullable=True)
    pro_expires = Column(DateTime, nullable=True)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    install_id = Column(String, index=True)
    btc_address = Column(String)
    amount_btc = Column(Float)
    status = Column(String, default="pending")  # pending | confirmed
    created_at = Column(DateTime, default=datetime.utcnow)


# =====================================================
#  UTILS
# =====================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

