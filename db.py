# db.py – compatibile con main.py, routes_license e Coinbase Commerce

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Usa .env o variabile ambiente
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./silent.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)
