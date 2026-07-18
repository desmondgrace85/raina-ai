"""
MACD (Moving Average Convergence Divergence) analysis.

One of the most battle-tested momentum + trend indicators.
Tracks EMA(12) vs EMA(26) and their signal line to detect
crossovers, histogram momentum, and zero-line breaks.
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
    if len(closes) < 35:
        return FactorResult(name="macd", score=0, weight=weight,
                            reason="Insufficient data for MACD analysis.")

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    histogram = macd_line - signal_line

    macd_now  = macd_line[-1]
    signal_now = signal_line[-1]
    hist_now   = histogram[-1]
    hist_prev  = histogram[-2] if len(histogram) > 1 else 0
    hist_2ago  = histogram[-3] if len(histogram) > 2 else hist_prev

    price = closes[-1]
    scale = abs(price) * 0.001 if price != 0 else 1.0

    # Fresh crossover = highest-conviction signal
    just_crossed_bull = (macd_now > signal_now) and (macd_line[-2] <= signal_line[-2])
    just_crossed_bear = (macd_now < signal_now) and (macd_line[-2] >= signal_line[-2])

    if just_crossed_bull:
        cross_score = 55
        cross_desc = "fresh bullish MACD crossover — momentum turning up"
    elif just_crossed_bear:
        cross_score = -55
        cross_desc = "fresh bearish MACD crossover — momentum turning down"
    elif macd_now > signal_now:
        cross_score = 28
        cross_desc = "MACD above signal line (sustained bullish momentum)"
    else:
        cross_score = -28
        cross_desc = "MACD below signal line (sustained bearish momentum)"

    # Histogram expansion = momentum accelerating
    hist_expanding = (abs(hist_now) > abs(hist_prev)) and (abs(hist_prev) >= abs(hist_2ago))
    hist_score = float(np.clip((hist_now / (scale + 1e-9)) * 35, -35, 35))
    if hist_expanding:
        hist_score *= 1.25

    # Zero-line position
    zero_score = 15 if macd_now > 0 else -15

    total = float(np.clip(cross_score * 0.50 + hist_score * 0.35 + zero_score * 0.15, -100, 100))

    reason = (
        f"{cross_desc}; histogram {'expanding' if hist_expanding else 'contracting'} "
        f"({'above' if hist_now > 0 else 'below'} zero); "
        f"MACD line {'above' if macd_now > 0 else 'below'} zero."
    )
    return FactorResult(name="macd", score=total, weight=weight, reason=reason)
