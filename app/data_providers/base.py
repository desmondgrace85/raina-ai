"""
Abstract interface every market data source must implement.

This is the ONLY place engines and analysis modules touch to get data.
Swap MockDataProvider for a real broker/exchange/MT5 feed by writing a
new class here and pointing app/main.py at it — nothing else changes.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from app.models.signal import Candle


class DataProvider(ABC):
    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        before: Optional[datetime] = None,
    ) -> list[Candle]:
        """
        Return up to `limit` candles for `symbol` at `timeframe`, oldest-first.

        If `before` is given, return only candles with timestamp < before,
        enabling backward pagination for the full-chart history scroll.

        timeframe examples: "1m", "5m", "15m", "1h", "4h", "1d"
        """
        raise NotImplementedError

    @abstractmethod
    async def get_available_symbols(self) -> list[str]:
        """Return the list of symbols this provider can serve."""
        raise NotImplementedError
