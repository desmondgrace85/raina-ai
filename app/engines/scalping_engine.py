"""
Scalping engine.

Speed-optimised for 1m/5m/15m charts. Momentum and volatility dominate —
they capture fast short-term shifts. Volume and breakout confirmation are
added to filter out low-quality setups. MTF is lightweight (one level up).
"""
from functools import partial

from app.analysis import breakout, momentum, multi_timeframe, support_resistance, trend, volatility, volume
from app.config import settings
from app.data_providers.base import DataProvider
from app.engines.base_engine import build_signal
from app.models.signal import Signal

# Synchronous factors
_SYNC_FACTORS = [
    partial(momentum.analyze,           weight=0.30),
    partial(volatility.analyze,         weight=0.20),
    partial(support_resistance.analyze, weight=0.15),
    partial(volume.analyze,             weight=0.20),
    partial(breakout.analyze,           weight=0.15),
]


async def generate_signal(
    provider: DataProvider,
    symbol: str,
    timeframe: str = "5m",
) -> Signal:
    if timeframe not in settings.scalp_timeframes:
        timeframe = "5m"

    async def _mtf_factor(candles):
        return await multi_timeframe.analyze(
            provider=provider,
            symbol=symbol,
            working_timeframe=timeframe,
            working_candles=candles,
            weight=0.12,
        )

    async def _news_factor(candles):
        # News is extra-important for scalping — CPI spikes move markets fast
        from app.analysis.news_sentiment import analyze as news_analyze
        return await news_analyze(candles=candles, symbol=symbol, weight=0.25)

    return await build_signal(
        provider=provider,
        symbol=symbol,
        timeframe=timeframe,
        engine_name="scalp",
        factor_funcs=_SYNC_FACTORS,
        async_factor_funcs=[_mtf_factor, _news_factor],
        min_confidence=settings.scalp_min_confidence,
        candle_limit=150,
    )
