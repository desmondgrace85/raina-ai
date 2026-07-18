"""
Support / resistance & liquidity zone analysis.

Finds recent swing highs/lows (pivot points) to build S/R levels, then
scores where the current price sits relative to the nearest zones —
close to support with room above = bullish bias, close to resistance
with room below = bearish bias. Also flags likely breakout / fake
breakout conditions when price has pierced a zone and closed back
inside it (a classic liquidity-grab / stop-hunt pattern).
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def _find_pivots(highs: np.ndarray, lows: np.ndarray, window: int = 3):
    pivot_highs, pivot_lows = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            pivot_highs.append(highs[i])
        if lows[i] == min(lows[i - window:i + window + 1]):
            pivot_lows.append(lows[i])
    return pivot_highs, pivot_lows


def analyze(candles: list[Candle], weight: float = 0.2) -> FactorResult:
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    closes = np.array([c.close for c in candles])

    if len(candles) < 20:
        return FactorResult(name="support_resistance", score=0, weight=weight, reason="Not enough data for S/R analysis")

    pivot_highs, pivot_lows = _find_pivots(highs, lows)
    price = closes[-1]

    resistance = min([p for p in pivot_highs if p > price], default=None)
    support = max([p for p in pivot_lows if p < price], default=None)

    score = 0.0
    parts = []

    if support is not None and resistance is not None:
        range_size = resistance - support
        if range_size > 0:
            pos_in_range = (price - support) / range_size  # 0 = at support, 1 = at resistance
            # Closer to support => bullish score, closer to resistance => bearish score
            score = (0.5 - pos_in_range) * 100
            parts.append(
                f"price sits {pos_in_range * 100:.0f}% of the way between nearby support "
                f"({support:.4f}) and resistance ({resistance:.4f})"
            )
    elif support is not None:
        parts.append(f"price trading above nearest identified support ({support:.4f}), no resistance overhead in range")
        score = 20
    elif resistance is not None:
        parts.append(f"price trading below nearest identified resistance ({resistance:.4f}), no support below in range")
        score = -20
    else:
        parts.append("no clear pivot-based S/R levels found in the lookback window")

    # Fake breakout / liquidity grab detection: last candle wicks beyond
    # a recent pivot but closes back inside it.
    last = candles[-1]
    fake_breakout = False
    if resistance is not None and last.high > resistance and last.close < resistance:
        score -= 15
        fake_breakout = True
        parts.append("recent candle wicked above resistance and closed back below it (possible liquidity grab / fake breakout)")
    if support is not None and last.low < support and last.close > support:
        score += 15
        fake_breakout = True
        parts.append("recent candle wicked below support and closed back above it (possible liquidity grab / fake breakout)")

    score = float(np.clip(score, -100, 100))
    reason = "; ".join(parts)
    return FactorResult(name="support_resistance", score=score, weight=weight, reason=reason)
