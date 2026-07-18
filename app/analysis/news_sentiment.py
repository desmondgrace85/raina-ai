"""
News sentiment analysis factor.

Integrates economic calendar data and market news headlines into the
signal pipeline. During high-impact news events (CPI, NFP, FOMC etc.)
this factor carries significant weight and can override pure TA HOLD calls.
"""
import logging
from app.models.signal import Candle, FactorResult

logger = logging.getLogger(__name__)


async def analyze(
    candles: list[Candle],
    symbol: str,
    weight: float = 0.20,
) -> FactorResult:
    """
    Fetch news + economic events and return a FactorResult.
    score > 0 = BUY bias, score < 0 = SELL bias.
    """
    try:
        from app.scanner.news_scanner import (
            get_todays_events, get_asset_news, compute_news_bias
        )
        import asyncio
        events, headlines = await asyncio.gather(
            get_todays_events(),
            get_asset_news(symbol),
        )
        score, explanation = compute_news_bias(symbol, events, headlines)
        logger.debug(f"[news] {symbol} score={score:.1f} events={len(events)} headlines={len(headlines)}")
        return FactorResult(
            name="news_sentiment",
            score=score,
            weight=weight,
            explanation=explanation,
        )
    except Exception as e:
        logger.warning(f"news_sentiment factor failed for {symbol}: {e}")
        return FactorResult(name="news_sentiment", score=0.0, weight=0.0, explanation="News data unavailable.")
