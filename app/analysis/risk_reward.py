"""
Risk/reward calculation.

Given a direction and the ATR-based volatility of the asset, derive a
stop loss, staged take-profit targets, entry zone, and the resulting
risk/reward ratio. Also classifies an overall risk level for the signal
based on volatility and RR quality.
"""
from app.analysis.volatility import atr
from app.models.signal import Candle, Direction, RiskLevel


def calculate(
    candles: list[Candle],
    direction: Direction,
    atr_stop_multiplier: float = 1.5,
    tp_multipliers: tuple[float, ...] = (1.5, 2.5, 4.0),
) -> dict:
    price = candles[-1].close
    current_atr = atr(candles, 14) or price * 0.005  # fallback so we never divide by zero

    if direction == Direction.BUY:
        entry_zone = (price - current_atr * 0.15, price + current_atr * 0.05)
        stop_loss = price - current_atr * atr_stop_multiplier
        take_profit = [price + current_atr * m for m in tp_multipliers]
    elif direction == Direction.SELL:
        entry_zone = (price - current_atr * 0.05, price + current_atr * 0.15)
        stop_loss = price + current_atr * atr_stop_multiplier
        take_profit = [price - current_atr * m for m in tp_multipliers]
    else:
        return {
            "entry_zone": None, "stop_loss": None, "take_profit": [],
            "risk_reward_ratio": None, "risk_level": RiskLevel.LOW,
        }

    risk = abs(price - stop_loss)
    reward = abs(take_profit[0] - price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else None

    atr_pct = (current_atr / price) * 100 if price else 0
    if atr_pct > 2.5 or (rr_ratio is not None and rr_ratio < 1.2):
        risk_level = RiskLevel.HIGH
    elif atr_pct > 1.0 or (rr_ratio is not None and rr_ratio < 2.0):
        risk_level = RiskLevel.MEDIUM
    else:
        risk_level = RiskLevel.LOW

    return {
        "entry_zone": entry_zone,
        "stop_loss": round(stop_loss, 5),
        "take_profit": [round(tp, 5) for tp in take_profit],
        "risk_reward_ratio": rr_ratio,
        "risk_level": risk_level,
    }
