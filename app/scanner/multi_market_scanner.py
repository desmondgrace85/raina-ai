"""
Multi-market scanner.

Runs a given engine (long-term or scalp) across a watchlist of assets
concurrently and returns all resulting signals. This is what a
Telegram bot or scheduled job would call on a loop later.
"""
import asyncio

from app.data_providers.base import DataProvider
from app.engines import long_term_engine, scalping_engine
from app.models.signal import Signal


async def scan(
    provider: DataProvider,
    watchlist: list[str],
    engine: str = "long_term",
    timeframe: str | None = None,
    only_actionable: bool = False,
) -> list[Signal]:
    """
    engine: "long_term" or "scalp"
    only_actionable: if True, filter out HOLD signals from the results
    """
    if engine == "scalp":
        tf = timeframe or "5m"
        tasks = [scalping_engine.generate_signal(provider, symbol, tf) for symbol in watchlist]
    else:
        tf = timeframe or "4h"
        tasks = [long_term_engine.generate_signal(provider, symbol, tf) for symbol in watchlist]

    signals: list[Signal] = await asyncio.gather(*tasks)

    if only_actionable:
        signals = [s for s in signals if s.direction.value != "HOLD"]

    return signals
