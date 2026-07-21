"""
Mock data provider — a seeded random-walk price generator.

Lets you run and test the entire engine (analysis -> scoring -> signals)
with zero external API keys. The random walk includes a mild trend and
volatility bursts so trend/momentum/volatility modules have something
realistic to detect. Same symbol+timeframe always regenerates the same
series within a run (seeded by symbol+timeframe), so results are
reproducible while you're testing.
"""
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.data_providers.base import DataProvider
from app.models.signal import Candle

_TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440,
}

_BASE_PRICES = {
    "EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 157.20,
    "BTCUSD": 63000.0, "ETHUSD": 3400.0,
    "XAUUSD": 2380.0, "WTICOUSD": 78.50,
}


def _seed_for(symbol: str, timeframe: str) -> int:
    digest = hashlib.sha256(f"{symbol}:{timeframe}".encode()).hexdigest()
    return int(digest[:8], 16)


class MockDataProvider(DataProvider):
    async def get_available_symbols(self) -> list[str]:
        return list(_BASE_PRICES.keys())

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200, before: Optional[datetime] = None) -> list[Candle]:
        base_price = _BASE_PRICES.get(symbol, 100.0)
        minutes = _TIMEFRAME_MINUTES.get(timeframe, 60)
        rng = np.random.default_rng(_seed_for(symbol, timeframe))

        # Mild underlying drift so trend-detection has signal to find,
        # plus a slow sine wave to create swing highs/lows for S/R.
        drift = rng.uniform(-0.00015, 0.00015)
        vol = base_price * rng.uniform(0.0015, 0.004)

        candles: list[Candle] = []
        price = base_price
        now = datetime.now(timezone.utc)

        for i in range(limit):
            swing = math.sin(i / 14.0) * vol * 1.5
            shock = rng.normal(0, vol)
            price = max(price + drift * base_price + shock * 0.3 + swing * 0.05, base_price * 0.5)

            open_p = price
            close_p = max(price + rng.normal(0, vol * 0.4), base_price * 0.5)
            high_p = max(open_p, close_p) + abs(rng.normal(0, vol * 0.3))
            low_p = min(open_p, close_p) - abs(rng.normal(0, vol * 0.3))
            volume = abs(rng.normal(1000, 300))

            ts = now - timedelta(minutes=minutes * (limit - i))
            candles.append(Candle(
                timestamp=ts, open=open_p, high=high_p, low=low_p,
                close=close_p, volume=volume,
            ))
            price = close_p

        return candles
