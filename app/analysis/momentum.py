"""
Momentum analysis.

RSI-based momentum reading plus simple momentum-shift detection
(rate of change accelerating or decelerating over the recent window).
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    deltas = np.diff(closes)
    if len(deltas) < period:
        return 50.0
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def analyze(candles: list[Candle], weight: float = 0.2) -> FactorResult:
    closes = np.array([c.close for c in candles])
    if len(closes) < 20:
        return FactorResult(name="momentum", score=0, weight=weight, reason="Not enough data for momentum analysis")

    rsi = _rsi(closes)

    # RSI-based score: overbought/oversold pulls score toward mean reversion,
    # mid-range with a clear slope indicates trending momentum.
    if rsi >= 70:
        rsi_score = -1 * (rsi - 70) * 2  # overbought -> bearish pressure
        rsi_desc = f"RSI {rsi:.1f} (overbought)"
    elif rsi <= 30:
        rsi_score = (30 - rsi) * 2  # oversold -> bullish pressure
        rsi_desc = f"RSI {rsi:.1f} (oversold)"
    else:
        rsi_score = (rsi - 50) * 1.2  # mild directional bias
        rsi_desc = f"RSI {rsi:.1f} (neutral zone)"

    # Rate of change over last 10 vs prior 10 bars
    roc_recent = (closes[-1] - closes[-10]) / closes[-10] * 100
    roc_prior = (closes[-10] - closes[-20]) / closes[-20] * 100
    accelerating = abs(roc_recent) > abs(roc_prior)
    shift_score = np.clip(roc_recent * 8, -30, 30)

    total = float(np.clip(rsi_score + shift_score, -100, 100))
    shift_desc = "momentum accelerating" if accelerating else "momentum decelerating"

    reason = f"{rsi_desc}; {shift_desc} ({roc_recent:+.2f}% last window vs {roc_prior:+.2f}% prior)."
    return FactorResult(name="momentum", score=total, weight=weight, reason=reason)
