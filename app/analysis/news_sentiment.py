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
        logger.debug(
            f"[news] {symbol} score={score:.1f} "
            f"events={len(events)} headlines={len(headlines)}"
        )
        # No events, no headlines, or a genuinely flat net score: this factor
        # has no real opinion. Keeping full weight on a 0 score was diluting
        # the agreement ratio on every ordinary day (only real news days
        # would ever let confidence clear the 65-70% bar). Zero the weight
        # instead so a quiet news day doesn't cap TA-only signals.
        effective_weight = weight if (events or headlines) and abs(score) > 1e-6 else 0.0
        return FactorResult(
            name="news_sentiment",
            score=score,
            weight=effective_weight,
            reason=explanation if effective_weight else "No active news/economic events for this symbol — factor excluded from this signal.",
        )
    except Exception as e:
        logger.warning(f"news_sentiment factor failed for {symbol}: {e}")
        # Weight 0 so it doesn't drag confidence down — but doesn't help either
        return FactorResult(
            name="news_sentiment",
            score=0.0,
            weight=0.0,
            reason="News data unavailable — factor excluded from this signal.",
        )
