"""
Long-term / position trading engine.

Full-spectrum analysis: trend, structure, momentum, MACD, ADX,
candlestick patterns, volatility, volume, breakout, MTF confluence,
AND real-time news/economic calendar sentiment (CPI, NFP, FOMC etc.).

News sentiment carries 20% weight and can override pure-TA HOLD calls
when high-impact events beat or miss expectations significantly.

When OPENAI_API_KEY is set, GPT-4o-mini synthesises all factor evidence
for a smarter, more nuanced final signal — just like your best trader
reading every indicator at once.

Only signals with 65%+ confidence reach subscribers.
"""
import logging
from functools import partial

from app.analysis import (
    adx, breakout, candlestick, macd,
    momentum, multi_timeframe, support_resistance,
    trend, volatility, volume,
)
from app.config import settings
from app.data_providers.base import DataProvider
from app.engines.base_engine import build_signal, combine_factors
from app.models.signal import Direction, Signal

logger = logging.getLogger(__name__)

# Weights reduced slightly to make room for news sentiment (total ≈1.30 with async)
_SYNC_FACTORS = [
    partial(trend.analyze,              weight=0.18),
    partial(macd.analyze,               weight=0.18),
    partial(adx.analyze,                weight=0.13),
    partial(support_resistance.analyze, weight=0.13),
    partial(momentum.analyze,           weight=0.09),
    partial(candlestick.analyze,        weight=0.09),
    partial(volume.analyze,             weight=0.07),
    partial(breakout.analyze,           weight=0.07),
    partial(volatility.analyze,         weight=0.03),
]


async def generate_signal(
    provider: DataProvider,
    symbol: str,
    timeframe: str = "1h",
) -> Signal:
    async def _mtf_factor(candles):
        return await multi_timeframe.analyze(
            provider=provider,
            symbol=symbol,
            working_timeframe=timeframe,
            working_candles=candles,
            weight=0.13,
        )

    async def _news_factor(candles):
        from app.analysis.news_sentiment import analyze as news_analyze
        return await news_analyze(candles=candles, symbol=symbol, weight=0.20)

    sig = await build_signal(
        provider=provider,
        symbol=symbol,
        timeframe=timeframe,
        engine_name="long_term",
        factor_funcs=_SYNC_FACTORS,
        async_factor_funcs=[_mtf_factor, _news_factor],
        min_confidence=settings.min_signal_confidence,
        candle_limit=300,
    )

    # ── AI enhancement (optional) ──────────────────────────────────────────
    # When OPENAI_API_KEY is configured, GPT-4o-mini re-evaluates all factors
    # for a smarter, more contextual signal — catches things pure TA misses.
    try:
        from app.analysis.ai_enhancer import enhance_signal, ai_available
        if ai_available():
            candles = await provider.get_candles(symbol, timeframe, 300)
            # Rebuild factors for the AI context
            sync_factors = [f(candles) for f in _SYNC_FACTORS]
            ai_dir, ai_conf, ai_read = await enhance_signal(
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
                factors=sync_factors,
                ta_direction=sig.direction,
                ta_confidence=sig.confidence,
            )
            if ai_read:
                # Merge AI output into the signal
                sig = sig.model_copy(update={
                    "direction":   ai_dir,
                    "confidence":  round(ai_conf, 1),
                    "explanation": ai_read,
                })
    except Exception as e:
        logger.warning(f"[long_term] AI enhance skipped for {symbol}: {e}")

    return sig
