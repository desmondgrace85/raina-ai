"""
Candlestick pattern recognition.

Detects high-probability reversal and continuation patterns
on the last 3 candles: pin bars, engulfing, doji, morning/evening
star, and strong momentum candles (marubozu-like).
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def analyze(candles: list[Candle], weight: float = 0.10) -> FactorResult:
    if len(candles) < 5:
        return FactorResult(name="candlestick", score=0, weight=weight,
                            reason="Insufficient data for pattern detection.")

    last  = candles[-1]
    prev  = candles[-2]
    prev2 = candles[-3]

    body      = abs(last.close - last.open)
    full_range = last.high - last.low if last.high != last.low else 0.0001
    upper_wick = last.high - max(last.close, last.open)
    lower_wick = min(last.close, last.open) - last.low

    avg_body = np.mean([abs(c.close - c.open) for c in candles[-20:]]) or 1.0
    prev_body = abs(prev.close - prev.open)

    score    = 0.0
    patterns = []

    # 1. Bullish Pin Bar / Hammer
    if (lower_wick > body * 2.5 and lower_wick > upper_wick * 2
            and body < full_range * 0.4):
        sign = 1 if last.close >= last.open else 0.7
        score += 55 * sign
        patterns.append("bullish pin bar / hammer — buyers rejected lower prices")

    # 2. Bearish Pin Bar / Shooting Star
    elif (upper_wick > body * 2.5 and upper_wick > lower_wick * 2
          and body < full_range * 0.4):
        sign = 1 if last.close <= last.open else 0.7
        score -= 55 * sign
        patterns.append("bearish pin bar / shooting star — sellers rejected higher prices")

    # 3. Bullish Engulfing
    elif (prev.close < prev.open
          and last.close > last.open
          and last.open <= prev.close
          and last.close >= prev.open
          and body >= prev_body * 0.8):
        score += 60
        patterns.append("bullish engulfing — strong demand overwhelmed prior selling")

    # 4. Bearish Engulfing
    elif (prev.close > prev.open
          and last.close < last.open
          and last.open >= prev.close
          and last.close <= prev.open
          and body >= prev_body * 0.8):
        score -= 60
        patterns.append("bearish engulfing — strong supply overwhelmed prior buying")

    # 5. Morning Star (3-bar bullish reversal)
    prev2_body = abs(prev2.close - prev2.open)
    if (prev2.close < prev2.open
            and abs(prev.close - prev.open) < prev2_body * 0.35
            and last.close > last.open
            and last.close > (prev2.open + prev2.close) / 2):
        score += 50
        patterns.append("morning star — 3-bar bullish reversal")

    # 6. Evening Star (3-bar bearish reversal)
    elif (prev2.close > prev2.open
          and abs(prev.close - prev.open) < prev2_body * 0.35
          and last.close < last.open
          and last.close < (prev2.open + prev2.close) / 2):
        score -= 50
        patterns.append("evening star — 3-bar bearish reversal")

    # 7. Strong Bullish Candle (marubozu-like)
    elif (last.close > last.open
          and body > avg_body * 1.6
          and body > full_range * 0.65):
        score += 35
        patterns.append("strong bullish candle — sustained buying pressure")

    # 8. Strong Bearish Candle
    elif (last.close < last.open
          and body > avg_body * 1.6
          and body > full_range * 0.65):
        score -= 35
        patterns.append("strong bearish candle — sustained selling pressure")

    # 9. Doji — indecision
    elif body < full_range * 0.08:
        score = 0
        patterns.append("doji — market indecision, wait for next candle confirmation")

    if not patterns:
        patterns.append("no significant pattern — price action neutral")

    reason = "; ".join(patterns) + "."
    return FactorResult(name="candlestick", score=float(np.clip(score, -100, 100)),
                        weight=weight, reason=reason)
