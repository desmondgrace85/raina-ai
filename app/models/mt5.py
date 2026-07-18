"""
MT5 data models for RainX auto-trading.
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
    PENDING = "pending"       # queued, waiting for EA to pick up
    SENT = "sent"             # EA acknowledged
    OPEN = "open"             # trade is live on MT5
    CLOSED = "closed"         # trade closed
    CANCELLED = "cancelled"   # cancelled before execution
    FAILED = "failed"         # EA reported failure


class RiskSettings(BaseModel):
    risk_percent: float = Field(default=1.0, ge=0.1, le=10.0)   # % of balance per trade
    max_open_trades: int = Field(default=3, ge=1, le=20)
    max_daily_loss_percent: float = Field(default=5.0, ge=1.0, le=50.0)
    scalping_enabled: bool = False
    account_mode: AccountMode = AccountMode.DEMO
    min_confidence: float = Field(default=70.0, ge=60.0, le=95.0)


class MT5Account(BaseModel):
    telegram_id: int
    api_key: str                      # user's unique EA connection key
    account_mode: AccountMode = AccountMode.DEMO
    is_connected: bool = False        # EA has sent a heartbeat recently
    broker_name: Optional[str] = None
    account_number: Optional[str] = None   # display only, not credentials
    balance: Optional[float] = None
    equity: Optional[float] = None
    last_heartbeat: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TradeOrder(BaseModel):
    id: Optional[int] = None
    telegram_id: int
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
    mt5_ticket: Optional[int] = None    # MT5 ticket number after execution
    open_price: Optional[float] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    comment: str = "RainX"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class TradeResult(BaseModel):
    """Sent back by EA after execution."""
    api_key: str
    order_id: int
    success: bool
    mt5_ticket: Optional[int] = None
    open_price: Optional[float] = None
    error: Optional[str] = None


class TradeClose(BaseModel):
    """Sent by EA when a trade closes."""
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
