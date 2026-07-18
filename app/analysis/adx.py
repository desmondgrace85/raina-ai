"""
ADX (Average Directional Index) + Directional Movement analysis.

ADX measures TREND STRENGTH (0-100), not direction alone.
+DI > -DI = bullish; -DI > +DI = bearish.
ADX > 25 = trending market; < 20 = ranging/choppy.

Critical for gold (XAUUSD) — prevents false HOLDs on strongly
trending sessions where other indicators show mixed signals.
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(result[-1] * (period - 1) / period + v)
    return result


def analyze(candles: list[Candle], weight: float = 0.15) -> FactorResult:
    if len(candles) < 40:
        return FactorResult(name="adx", score=0, weight=weight,
                            reason="Insufficient data for ADX analysis.")

    period = 14
    highs  = [c.high  for c in candles]
    lows   = [c.low   for c in candles]
    closes = [c.close for c in candles]

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, len(candles)):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up   = highs[i]  - highs[i - 1]
        down = lows[i - 1] - lows[i]
        trs.append(tr)
        plus_dms.append(up   if (up > down and up > 0)   else 0.0)
        minus_dms.append(down if (down > up and down > 0) else 0.0)

    atr_s     = _wilder_smooth(trs,       period)
    plus_di_s = _wilder_smooth(plus_dms,  period)
    minus_di_s= _wilder_smooth(minus_dms, period)

    if not atr_s:
        return FactorResult(name="adx", score=0, weight=weight,
                            reason="ADX calculation failed — insufficient data.")

    dx_list, pdi_vals, mdi_vals = [], [], []
    for atr_v, p, m in zip(atr_s, plus_di_s, minus_di_s):
        if atr_v == 0:
            continue
        pdi = p / atr_v * 100
        mdi = m / atr_v * 100
        pdi_vals.append(pdi)
        mdi_vals.append(mdi)
        diff  = abs(pdi - mdi)
        total = pdi + mdi
        dx_list.append(diff / total * 100 if total > 0 else 0)

    adx_vals = _wilder_smooth(dx_list, period)
    if not adx_vals or not pdi_vals:
        return FactorResult(name="adx", score=0, weight=weight,
                            reason="Not enough bars for ADX smoothing.")

    adx      = adx_vals[-1]
    plus_di  = pdi_vals[-1]
    minus_di = mdi_vals[-1]
    direction = 1 if plus_di > minus_di else -1

    if adx > 45:
        base = direction * 80
        strength_desc = f"very strong trend (ADX {adx:.1f})"
    elif adx > 30:
        base = direction * 55
        strength_desc = f"strong trending market (ADX {adx:.1f})"
    elif adx > 20:
        base = direction * 30
        strength_desc = f"trend developing (ADX {adx:.1f})"
    else:
        base = direction * 10
        strength_desc = f"ranging/choppy (ADX {adx:.1f}) — low trend strength"

    di_desc = (
        f"+DI {plus_di:.1f} vs -DI {minus_di:.1f} "
        f"({'bullish' if direction > 0 else 'bearish'} bias)"
    )

    total = float(np.clip(base, -100, 100))
    reason = f"{strength_desc}; {di_desc}."
    return FactorResult(name="adx", score=total, weight=weight, reason=reason)
