from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Set
import logging
from datetime import datetime, timedelta, timezone
import enum
from decimal import Decimal
import os

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SqlEnum
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from config import settings
from routes_payment import router as payment_router

app = FastAPI()

# ========== CORS ==========
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== WEBSOCKETS ==========
rooms: Dict[str, Set[WebSocket]] = {}

@app.websocket("/ws/{room}")
async def websocket_endpoint(ws: WebSocket, room: str):
    await ws.accept()
    rooms.setdefault(room, set()).add(ws)

    try:
        while True:
            data = await ws.receive_text()
            for client in rooms[room]:
                if client != ws:
                    await client.send_text(data)
    except WebSocketDisconnect:
        rooms[room].remove(ws)

# ========== DATABASE ==========
Base = declarative_base()

class LicenseStatus(str, enum.Enum):
    TRIAL = "trial"
    DEMO = "demo"
    PRO = "pro"

class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    install_id = Column(String, unique=True, index=True)
    status = Column(SqlEnum(LicenseStatus), default=LicenseStatus.TRIAL)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activated_at = Co_
