"""
Volume analysis module.

Detects volume-confirmed trends, anomalous spikes, and divergence between
price direction and volume flow. Strong moves with high volume behind them
score higher; moves on declining volume are flagged as weak.
"""
import numpy as np

from app.models.signal import Candle, FactorResult


def analyze(candles: list[Candle], weight: float = 0.20) -> FactorResult:
    if len(candles) < 20:
        return FactorResult(name="volume", score=0, weight=weight,
                            reason="Insufficient data for volume analysis.")

    volumes = np.array([c.volume for c in candles])
    closes = np.array([c.close for c in candles])

    # Skip if volume data is all zeros (common with some forex feeds)
    if volumes.max() == 0:
        return FactorResult(name="volume", score=0, weight=weight,
                            reason="No volume data available for this instrument.")

    avg_vol = volumes[-20:].mean()
    recent_vol = volumes[-5:].mean()

    # On-Balance Volume (OBV): cumulative volume signed by price direction
    obv = np.zeros(len(candles))
    for i in range(1, len(candles)):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]

    # OBV trend: slope over last 20 bars
    obv_recent = obv[-20:]
    obv_slope = np.polyfit(np.arange(20), obv_recent, 1)[0]
    obv_normalized = np.clip(obv_slope / (avg_vol + 1e-9) * 100, -60, 60)

    # Volume spike: current volume vs recent average
    vol_ratio = recent_vol / (avg_vol + 1e-9)
    price_direction = 1 if closes[-1] > closes[-5] else -1
    spike_score = 0.0
    spike_desc = ""
    if vol_ratio > 1.5:
        spike_score = price_direction * min((vol_ratio - 1) * 20, 30)
        spike_desc = f"volume spike {vol_ratio:.1f}x average confirms {('buying' if price_direction > 0 else 'selling')} pressure"
    elif vol_ratio < 0.6:
        spike_desc = "volume declining — weak conviction behind recent move"
        # Weak volume dampens the direction (score towards 0)
        spike_score = -price_direction * 15

    # Volume trend confirmation: compare OBV direction to price direction
    price_trend = 1 if closes[-1] > closes[-20] else -1
    divergence_desc = ""
    divergence_score = 0.0
    if obv_normalized > 10 and price_trend < 0:
        divergence_desc = "OBV rising while price falls — potential bullish divergence"
        divergence_score = 20
    elif obv_normalized < -10 and price_trend > 0:
        divergence_desc = "OBV falling while price rises — potential bearish divergence"
        divergence_score = -20

    total = float(np.clip(obv_normalized * 0.5 + spike_score + divergence_score, -100, 100))

    parts = [f"OBV {'rising' if obv_normalized > 0 else 'falling'}"]
    if spike_desc:
        parts.append(spike_desc)
    if divergence_desc:
        parts.append(divergence_desc)
    reason = "; ".join(parts) + "."

    return FactorResult(name="volume", score=total, weight=weight, reason=reason)
