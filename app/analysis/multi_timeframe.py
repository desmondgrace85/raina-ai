"""
Multi-timeframe (MTF) confluence module.

Fetches candles from higher timeframes and checks whether trend direction
agrees with the working timeframe. When all timeframes align, confidence
rises significantly. Disagreement across timeframes penalises the score.

This module is special: it is async and needs the provider + symbol to
fetch its own candle sets, unlike the other factor modules which receive
pre-fetched candles from the engine.
"""
import asyncio

import numpy as np

from app.data_providers.base import DataProvider
from app.models.signal import Candle, FactorResult


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    ema = np.zeros_like(values, dtype=float)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = alpha * values[i] + (1 - alpha) * ema[i - 1]
    return ema


def _trend_direction(candles: list[Candle]) -> float:
    """Returns a score in -100..100 for trend direction on given candles."""
    if len(candles) < 30:
        return 0.0
    closes = np.array([c.close for c in candles], dtype=float)
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    gap_pct = (fast[-1] - slow[-1]) / slow[-1] * 100
    slope = (fast[-1] - fast[-10]) / fast[-10] * 100
    return float(np.clip(gap_pct * 15 + slope * 20, -100, 100))


# Timeframe hierarchy for MTF lookups
_HIGHER_TF: dict[str, list[str]] = {
    "1m":  ["5m", "15m", "1h"],
    "5m":  ["15m", "1h", "4h"],
    "15m": ["1h", "4h", "1d"],
    "1h":  ["4h", "1d"],
    "4h":  ["1d"],
    "1d":  [],
}


async def analyze(
    provider: DataProvider,
    symbol: str,
    working_timeframe: str,
    working_candles: list[Candle],
    weight: float = 0.20,
) -> FactorResult:
    higher_tfs = _HIGHER_TF.get(working_timeframe, [])
    if not higher_tfs:
        return FactorResult(name="mtf", score=0, weight=weight,
                            reason="No higher timeframes to compare against.")

    working_score = _trend_direction(working_candles)

    # Fetch all higher TF candles concurrently
    tasks = [provider.get_candles(symbol, tf, 100) for tf in higher_tfs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    scores: list[tuple[str, float]] = [(working_timeframe, working_score)]
    for tf, result in zip(higher_tfs, results):
        if isinstance(result, Exception) or not result:
            continue
        scores.append((tf, _trend_direction(result)))

    if len(scores) < 2:
        return FactorResult(name="mtf", score=0, weight=weight,
                            reason="Could not fetch higher timeframe data.")

    # Agreement: how many TFs point the same direction as the working TF
    directions = [1 if s > 5 else (-1 if s < -5 else 0) for _, s in scores]
    working_dir = directions[0]
    agreeing = sum(1 for d in directions[1:] if d == working_dir and working_dir != 0)
    total_higher = len(directions) - 1

    if working_dir == 0:
        agreement_score = 0.0
        verdict = "working timeframe trend is neutral/ranging"
    else:
        agreement_ratio = agreeing / total_higher if total_higher > 0 else 0
        agreement_score = float(np.clip(working_dir * agreement_ratio * 80, -80, 80))
        aligned_tfs = [tf for (tf, _), d in zip(scores[1:], directions[1:]) if d == working_dir]
        verdict = (
            f"{'bullish' if working_dir > 0 else 'bearish'} on {working_timeframe}, "
            f"{agreeing}/{total_higher} higher TF(s) agree "
            f"({', '.join(aligned_tfs) if aligned_tfs else 'none'})"
        )

    return FactorResult(name="mtf", score=agreement_score, weight=weight, reason=verdict)
