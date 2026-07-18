"""
Trend direction analysis.

Uses SMA(20/50/200), EMA(12/26) relationships, and swing structure
(higher highs / higher lows) to score trend strength and direction.
SMA values are shown in the app UI and drive the core trend read.
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    ema = np.zeros_like(values, dtype=float)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = alpha * values[i] + (1 - alpha) * ema[i - 1]
    return ema


def analyze(candles: list[Candle], weight: float = 0.20) -> FactorResult:
    closes = np.array([c.close for c in candles], dtype=float)
    if len(closes) < 30:
        return FactorResult(name="trend", score=0, weight=weight,
                            reason="Not enough data for trend analysis")

    price = closes[-1]

    # SMA 20 / 50 / 200
    sma20  = closes[-20:].mean()
    sma50  = closes[-50:].mean()  if len(closes) >= 50  else None
    sma200 = closes[-200:].mean() if len(closes) >= 200 else None

    # EMA 12 / 26
    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    ema_gap_pct = (ema_fast[-1] - ema_slow[-1]) / ema_slow[-1] * 100
    slope_pct   = (ema_fast[-1] - ema_fast[-10]) / ema_fast[-10] * 100

    # SMA stack scoring
    sma_score = 0.0
    sma_desc_parts = []

    vs20 = "above" if price > sma20 else "below"
    sma_desc_parts.append(f"price {vs20} SMA20 {sma20:.2f}")

    if price > sma20:
        sma_score += 20
    else:
        sma_score -= 20

    if sma50 is not None:
        vs50 = "above" if price > sma50 else "below"
        sma_desc_parts.append(f"{vs50} SMA50 {sma50:.2f}")
        if price > sma50:
            sma_score += 20
        else:
            sma_score -= 20
        # Golden/death cross bonus
        if sma20 > sma50:
            sma_score += 10
            sma_desc_parts.append("SMA20 > SMA50 (bullish stack)")
        else:
            sma_score -= 10
            sma_desc_parts.append("SMA20 < SMA50 (bearish stack)")

    if sma200 is not None:
        vs200 = "above" if price > sma200 else "below"
        sma_desc_parts.append(f"{vs200} SMA200 {sma200:.2f}")
        if price > sma200:
            sma_score += 15
        else:
            sma_score -= 15

    # Swing structure
    recent = closes[-20:]
    fh_high, sh_high = recent[:10].max(), recent[10:].max()
    fh_low,  sh_low  = recent[:10].min(), recent[10:].min()
    higher_highs = sh_high > fh_high
    higher_lows  = sh_low  > fh_low
    lower_highs  = sh_high < fh_high
    lower_lows   = sh_low  < fh_low

    structure_score = 0
    if higher_highs and higher_lows:
        structure_score = 35
        structure_desc = "higher highs and higher lows — uptrend structure"
    elif lower_highs and lower_lows:
        structure_score = -35
        structure_desc = "lower highs and lower lows — downtrend structure"
    else:
        structure_desc = "no clear swing structure — ranging/choppy"

    ema_score   = float(np.clip(ema_gap_pct * 12, -25, 25))
    slope_score = float(np.clip(slope_pct  * 15, -20, 20))

    total = float(np.clip(sma_score + ema_score + slope_score + structure_score, -100, 100))
    direction_word = "bullish" if total > 15 else "bearish" if total < -15 else "neutral/ranging"

    reason = (
        ", ".join(sma_desc_parts)
        + f"; {structure_desc}"
        + f"; EMA(12/26) spread {ema_gap_pct:+.2f}%"
        + f"; overall trend {direction_word}."
    )
    return FactorResult(name="trend", score=total, weight=weight, reason=reason)
