"""
MT5 data models for RainX auto-trading.
user_id = mt5_login (broker account number) — no Telegram dependency.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AccountMode(str, Enum):
    DEMO = "demo"
    REAL = "real"


class TradeDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RiskSettings(BaseModel):
    risk_percent: float = Field(default=1.0, ge=0.1, le=10.0)
    max_open_trades: int = Field(default=3, ge=1, le=20)
    max_trades_per_symbol: int = Field(default=2, ge=1, le=10)
    max_daily_loss_percent: float = Field(default=5.0, ge=1.0, le=50.0)
    scalping_enabled: bool = False
    account_mode: AccountMode = AccountMode.DEMO
    # Floor is 10 — Quick Scalp operates at 55%+ so any user setting works
    min_confidence: float = Field(default=70.0, ge=10.0, le=99.0)


class MT5Account(BaseModel):
    user_id: str
    api_key: str
    account_mode: AccountMode = AccountMode.DEMO
    is_connected: bool = False
    broker_name: Optional[str] = None
    account_number: Optional[str] = None
    balance: Optional[float] = None
    equity: Optional[float] = None
    last_heartbeat: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TradeOrder(BaseModel):
    id: Optional[int] = None
    user_id: str
    api_key: str
    signal_id: Optional[int] = None
    asset: str
    direction: TradeDirection
    lot_size: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: float
    timeframe: Optional[str] = None
    status: TradeStatus = TradeStatus.PENDING
    mt5_ticket: Optional[int] = None
    open_price: Optional[float] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    comment: str = "RainX"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class TradeResult(BaseModel):
    api_key: str
    order_id: int
    success: bool
    mt5_ticket: Optional[int] = None
    open_price: Optional[float] = None
    error: Optional[str] = None


class TradeClose(BaseModel):
    api_key: str
    mt5_ticket: int
    close_price: float
    profit: float
    closed_at: Optional[datetime] = None


class EAHeartbeat(BaseModel):
    api_key: str
    account_mode: AccountMode
    broker_name: Optional[str] = None
    account_number: Optional[str] = None
    balance: Optional[float] = None
    equity: Optional[float] = None
