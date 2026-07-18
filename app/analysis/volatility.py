"""
Volatility analysis (ATR-based).

Volatility itself isn't bullish or bearish, but it matters for signal
quality: very low volatility means weak conviction / choppy conditions
(penalize confidence), and volatility that's expanding in the direction
of the recent move supports the setup (small confidence boost). This
module also feeds the ATR value back out for stop-loss/take-profit
sizing in risk_reward.py.
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, prev_close = candles[i].high, candles[i].low, candles[i - 1].close
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    return float(np.mean(trs[-period:]))


def analyze(candles: list[Candle], weight: float = 0.15) -> FactorResult:
    if len(candles) < 30:
        return FactorResult(name="volatility", score=0, weight=weight, reason="Not enough data for volatility analysis")

    current_atr = atr(candles, 14)
    baseline_atr = atr(candles[:-10], 14) if len(candles) > 40 else current_atr
    price = candles[-1].close

    atr_pct = (current_atr / price) * 100 if price else 0

    # Extremely low volatility -> low conviction, dampen the signal
    if atr_pct < 0.05:
        score = -20
        desc = f"volatility very low (ATR {atr_pct:.3f}% of price) — choppy, low-conviction conditions"
    elif atr_pct > 3.0:
        score = -10
        desc = f"volatility unusually high (ATR {atr_pct:.2f}% of price) — elevated risk of noise/whipsaw"
    else:
        expanding = current_atr > baseline_atr * 1.1
        score = 10 if expanding else 0
        desc = (
            f"volatility normal (ATR {atr_pct:.2f}% of price), "
            f"{'expanding' if expanding else 'stable'} vs prior baseline"
        )

    return FactorResult(name="volatility", score=float(score), weight=weight, reason=desc)
