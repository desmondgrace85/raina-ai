"""
Breakout and fake-out detection module.

Detects whether price is breaking out of a consolidation range with
conviction, or merely faking out. Key checks:
  - Consolidation box (recent range relative to ATR)
  - Candle close position relative to range boundary
  - Volume confirmation
  - Wick-to-body ratio (large wicks = fake-out risk)
  - Follow-through: does price stay beyond the breakout level?
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def _atr(candles: list[Candle], period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return float(np.mean(trs[-period:]))


def analyze(candles: list[Candle], weight: float = 0.20) -> FactorResult:
    if len(candles) < 40:
        return FactorResult(name="breakout", score=0, weight=weight,
                            reason="Insufficient data for breakout analysis.")

    atr = _atr(candles)
    if atr == 0:
        return FactorResult(name="breakout", score=0, weight=weight,
                            reason="ATR is zero — cannot assess breakout.")

    # Define consolidation box as the range of the 20–40 bars ago period
    box = candles[-40:-5]
    box_high = max(c.high for c in box)
    box_low = min(c.low for c in box)
    box_range = box_high - box_low

    # Tight box = potential energy for breakout
    is_tight = box_range < atr * 3

    last = candles[-1]
    prev = candles[-2]
    volumes = [c.volume for c in candles]
    avg_vol = np.mean(volumes[-20:]) if any(volumes) else 1
    recent_vol = volumes[-1]
    vol_confirmed = recent_vol > avg_vol * 1.2 if avg_vol > 0 else True

    score = 0.0
    reasons = []

    # Bullish breakout: close above box high with body above it
    if last.close > box_high and last.open > box_high * 0.998:
        breakout_strength = min((last.close - box_high) / atr * 30, 40)
        score += breakout_strength

        # Wick ratio — large upper wick reduces conviction
        body = abs(last.close - last.open)
        upper_wick = last.high - max(last.close, last.open)
        wick_penalty = -15 if upper_wick > body * 1.5 else 0
        score += wick_penalty

        # Volume confirmation
        vol_bonus = 20 if vol_confirmed else -10
        score += vol_bonus

        if is_tight:
            score += 15
            reasons.append("breakout from tight consolidation box")
        else:
            reasons.append("bullish breakout above range")
        if wick_penalty < 0:
            reasons.append("large upper wick — possible fake-out risk")
        reasons.append("volume " + ("confirms" if vol_confirmed else "does NOT confirm") + " move")

    # Bearish breakout: close below box low
    elif last.close < box_low and last.open < box_low * 1.002:
        breakout_strength = min((box_low - last.close) / atr * 30, 40)
        score -= breakout_strength

        body = abs(last.close - last.open)
        lower_wick = min(last.close, last.open) - last.low
        wick_penalty = 15 if lower_wick > body * 1.5 else 0
        score += wick_penalty

        vol_bonus = -20 if vol_confirmed else 10
        score += vol_bonus

        if is_tight:
            score -= 15
            reasons.append("breakdown from tight consolidation box")
        else:
            reasons.append("bearish breakdown below range")
        if wick_penalty > 0:
            reasons.append("large lower wick — possible fake-out risk")
        reasons.append("volume " + ("confirms" if vol_confirmed else "does NOT confirm") + " move")

    # Inside the box — no breakout
    else:
        if is_tight:
            reasons.append(f"price coiling in tight range (range={box_range:.4f}, ATR={atr:.4f}) — watching for breakout")
        else:
            reasons.append("no breakout — price within consolidation range")

    score = float(np.clip(score, -100, 100))
    return FactorResult(name="breakout", score=score, weight=weight,
                        reason=" | ".join(reasons) if reasons else "No breakout detected.")
