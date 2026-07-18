"""
Real market data provider using yfinance (Yahoo Finance).

Covers Forex, Crypto, Gold, and Commodities with zero API keys.
Symbols are mapped from Raina AI's internal naming to yfinance tickers.
"""
import asyncio
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from app.data_providers.base import DataProvider
from app.models.signal import Candle

# Internal symbol → yfinance ticker
_SYMBOL_MAP: dict[str, str] = {
    # Forex
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
    "GBPJPY": "GBPJPY=X",
    "EURJPY": "EURJPY=X",
    # Crypto
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "BNBUSD": "BNB-USD",
    "SOLUSD": "SOL-USD",
    "XRPUSD": "XRP-USD",
    "ADAUSD": "ADA-USD",
    # Gold & metals
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    # Commodities
    "WTICOUSD": "CL=F",
    "BRENTUSD": "BZ=F",
    "NATGAS": "NG=F",
}

# yfinance interval for each Raina timeframe (4h resampled from 1h)
_YF_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "1h",   # fetched as 1h, resampled below
    "1d": "1d",
}

# How far back to pull so we always have enough candles
_YF_PERIOD: dict[str, str] = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "1h": "730d",
    "4h": "730d",
    "1d": "5y",
}


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV data to 4h bars."""
    df.index = pd.to_datetime(df.index, utc=True)
    resampled = df.resample("4h").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    return resampled


def _df_to_candles(df: pd.DataFrame, limit: int) -> list[Candle]:
    df = df.tail(limit)
    candles = []
    for ts, row in df.iterrows():
        # Ensure timezone-aware
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        candles.append(Candle(
            timestamp=ts,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row.get("Volume", 0) or 0),
        ))
    return candles


class YFinanceProvider(DataProvider):
    """Live market data from Yahoo Finance via yfinance."""

    async def get_available_symbols(self) -> list[str]:
        return list(_SYMBOL_MAP.keys())

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        ticker = _SYMBOL_MAP.get(symbol.upper(), symbol)
        yf_interval = _YF_INTERVAL.get(timeframe, "1h")
        period = _YF_PERIOD.get(timeframe, "60d")

        # yfinance is blocking — run in a thread pool
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None,
            lambda: yf.download(
                ticker,
                period=period,
                interval=yf_interval,
                progress=False,
                auto_adjust=True,
            ),
        )

        if df is None or df.empty:
            return []

        # Older yfinance returns a MultiIndex columns (Price, Ticker) — flatten it
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if timeframe == "4h":
            df = _resample_to_4h(df)

        return _df_to_candles(df, limit)
