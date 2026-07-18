"""
Core data models shared across the whole engine.

Every analysis module and engine speaks these types, so a new data
provider or a new strategy only needs to produce/consume these shapes.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Candle(BaseModel):
    """One OHLCV bar. All data providers must produce a list of these."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class FactorResult(BaseModel):
    """
    The output of a single analysis module (trend, momentum, S/R, etc).

    score: -100..100, negative = bearish evidence, positive = bullish evidence
    weight: how much this factor counts toward the combined confidence score
    reason: short human-readable explanation, gets stitched into the
            signal's final explanation text
    """

    name: str
    score: float = Field(ge=-100, le=100)
    weight: float = Field(ge=0, le=1)
    reason: str


class Signal(BaseModel):
    """A complete trading signal, ready to hand to a user or bot."""

    asset: str
    engine: str  # "long_term" or "scalp"
    direction: Direction
    entry_zone: tuple[float, float] | None = None
    stop_loss: float | None = None
    take_profit: list[float] = []
    confidence: float = Field(ge=0, le=100)
    risk_level: RiskLevel
    risk_reward_ratio: float | None = None
    explanation: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    timeframe: str | None = None
