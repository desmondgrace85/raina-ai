"""
Shared pipeline for turning a list of FactorResults into a Signal.

Both the long-term and scalping engines build their own factor lists and
call `build_signal`. Supports both synchronous analysis modules and
async modules (e.g. multi-timeframe, which needs to fetch extra candles).
"""
import asyncio

import numpy as np

from app.analysis import risk_reward
from app.data_providers.base import DataProvider
from app.models.signal import Candle, Direction, FactorResult, Signal


def combine_factors(factors: list[FactorResult]) -> tuple[float, float]:
    """
    Returns (directional_score, confidence).
    directional_score: -100..100, sign = BUY vs SELL
    confidence: 0..100
    """
    if not factors:
        return 0.0, 0.0

    total_weight   = sum(f.weight for f in factors) or 1.0
    weighted_score = sum(f.score * f.weight for f in factors) / total_weight

    net_direction  = 1 if weighted_score >= 0 else -1
    agreeing_weight = sum(
        f.weight for f in factors
        if (f.score > 5 and net_direction > 0) or (f.score < -5 and net_direction < 0)
    )
    agreement_ratio = agreeing_weight / total_weight

    strength   = min(abs(weighted_score), 100)
    confidence = strength * 0.55 + agreement_ratio * 100 * 0.45

    return weighted_score, float(min(confidence, 100))


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    deltas = np.diff(closes)
    if len(deltas) < period:
        return 50.0
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = gains[-period:].mean()
    avg_l  = losses[-period:].mean()
    if avg_l == 0:
        return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))


def _build_rainas_read(
    candles: list[Candle],
    direction: Direction,
    factors: list[FactorResult],
    confidence: float,
) -> str:
    """Generate professional market commentary for the signal card."""
    closes = np.array([c.close for c in candles], dtype=float)
    price  = closes[-1]
    fmt    = (lambda v: f"{v:,.2f}") if price > 100 else (lambda v: f"{v:.5f}")

    parts = []

    # Price vs key SMAs
    sma20  = closes[-20:].mean() if len(closes) >= 20  else None
    sma50  = closes[-50:].mean() if len(closes) >= 50  else None
    sma200 = closes[-200:].mean() if len(closes) >= 200 else None

    if sma20 is not None:
        rel20 = "above" if price > sma20 else "below"
        line  = f"Price {fmt(price)} sits {rel20} SMA20 {fmt(sma20)}"
        if sma50 is not None:
            rel50  = "above" if price > sma50 else "below"
            line  += f" and {rel50} SMA50 {fmt(sma50)}"
        if sma200 is not None:
            rel200 = "above" if price > sma200 else "below"
            line  += f", {rel200} SMA200 {fmt(sma200)}"
        parts.append(line)

    # MA stack verdict
    if sma20 is not None and sma50 is not None:
        if sma20 > sma50 and price > sma20:
            parts.append("MA stack is fully bullish — all averages aligned upward")
        elif sma20 < sma50 and price < sma20:
            parts.append("MA stack is fully bearish — all averages aligned downward")
        else:
            parts.append("mixed MA alignment — conflicting signals between averages")

    # RSI
    rsi = _rsi(closes)
    if rsi >= 70:
        parts.append(f"RSI(14) at {rsi:.1f} is overbought — momentum stretched, caution on new longs")
    elif rsi <= 30:
        parts.append(f"RSI(14) at {rsi:.1f} is oversold — potential bounce setup building")
    elif rsi >= 55:
        parts.append(f"RSI(14) at {rsi:.1f} shows positive momentum building")
    elif rsi <= 45:
        parts.append(f"RSI(14) at {rsi:.1f} shows fading momentum without being oversold")
    else:
        parts.append(f"RSI(14) at {rsi:.1f} is neutral — no strong directional bias from momentum")

    # Recent price change
    change5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
    if abs(change5) > 0.3:
        word = "up" if change5 > 0 else "down"
        parts.append(f"Recent move {word} {abs(change5):.2f}% over last 5 candles")
    else:
        parts.append(f"Recent change flat at {change5:+.2f}% — no strong directional push")

    # Pull key factor commentary
    factor_map = {f.name: f for f in factors}
    for fname in ("adx", "macd", "candlestick"):
        if fname in factor_map:
            snippet = factor_map[fname].reason.split(";")[0].strip().rstrip(".")
            if snippet and snippet.lower() not in ("", "no significant pattern — price action neutral"):
                parts.append(snippet)

    # Conclusion
    if direction == Direction.HOLD:
        parts.append("Conflicting signals favour a wait-and-see stance — no high-probability setup yet")
    elif direction == Direction.BUY:
        parts.append(
            f"Setup shows {confidence:.0f}% confidence in upside — "
            f"watch entry zone for confirmation before committing"
        )
    else:
        parts.append(
            f"Setup shows {confidence:.0f}% confidence in downside — "
            f"watch entry zone for confirmation before committing"
        )

    return ". ".join(parts) + "."


async def build_signal(
    provider: DataProvider,
    symbol: str,
    timeframe: str,
    engine_name: str,
    factor_funcs: list,
    min_confidence: float,
    candle_limit: int = 200,
    async_factor_funcs: list | None = None,
) -> Signal:
    candles: list[Candle] = await provider.get_candles(symbol, timeframe, candle_limit)

    if len(candles) < 30:
        return Signal(
            asset=symbol, engine=engine_name, direction=Direction.HOLD,
            confidence=0, risk_level="LOW",
            explanation="Insufficient data to analyse this asset/timeframe.",
            timeframe=timeframe,
        )

    sync_factors: list[FactorResult] = [f(candles) for f in factor_funcs]

    async_factors: list[FactorResult] = []
    if async_factor_funcs:
        results = await asyncio.gather(
            *[f(candles) for f in async_factor_funcs],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, FactorResult):
                async_factors.append(r)

    factors = sync_factors + async_factors
    directional_score, confidence = combine_factors(factors)

    rainas_read = _build_rainas_read(candles, Direction.HOLD, factors, confidence)

    # HOLD when confidence is too low — directional_score threshold lowered to 5
    if confidence < min_confidence or abs(directional_score) < 5:
        return Signal(
            asset=symbol, engine=engine_name, direction=Direction.HOLD,
            confidence=round(confidence, 1), risk_level="LOW",
            explanation=rainas_read,
            timeframe=timeframe,
        )

    direction = Direction.BUY if directional_score > 0 else Direction.SELL
    rr = risk_reward.calculate(candles, direction)

    rainas_read = _build_rainas_read(candles, direction, factors, confidence)

    return Signal(
        asset=symbol, engine=engine_name, direction=direction,
        entry_zone=rr["entry_zone"], stop_loss=rr["stop_loss"],
        take_profit=rr["take_profit"], confidence=round(confidence, 1),
        risk_level=rr["risk_level"], risk_reward_ratio=rr["risk_reward_ratio"],
        explanation=rainas_read,
        timeframe=timeframe,
    )
