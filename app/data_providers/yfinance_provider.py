"""
Real market data provider using yfinance (Yahoo Finance).

Covers Forex, Crypto, Gold, and Commodities with zero API keys.
Symbols are mapped from Raina AI's internal naming to yfinance tickers.
"""
import asyncio
import logging
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from app.data_providers.base import DataProvider
from app.models.signal import Candle

logger = logging.getLogger(__name__)

# Internal symbol → yfinance ticker
_SYMBOL_MAP: dict[str, str] = {
    # Forex
    "EURUSD":   "EURUSD=X",
    "GBPUSD":   "GBPUSD=X",
    "USDJPY":   "USDJPY=X",
    "AUDUSD":   "AUDUSD=X",
    "USDCAD":   "USDCAD=X",
    "USDCHF":   "USDCHF=X",
    "NZDUSD":   "NZDUSD=X",
    "GBPJPY":   "GBPJPY=X",
    "EURJPY":   "EURJPY=X",
    # Crypto
    "BTCUSD":   "BTC-USD",
    "ETHUSD":   "ETH-USD",
    "BNBUSD":   "BNB-USD",
    "SOLUSD":   "SOL-USD",
    "XRPUSD":   "XRP-USD",
    "ADAUSD":   "ADA-USD",
    # Gold & metals
    "XAUUSD":   "GC=F",
    "XAGUSD":   "SI=F",
    # Commodities
    "WTICOUSD": "CL=F",
    "BRENTUSD": "BZ=F",
    "NATGAS":   "NG=F",
}

# yfinance interval for each Raina timeframe (4h resampled from 1h)
_YF_INTERVAL: dict[str, str] = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "1h":  "1h",
    "4h":  "1h",   # fetched as 1h, resampled below
    "1d":  "1d",
}

# How far back to pull so we always have enough candles
_YF_PERIOD: dict[str, str] = {
    "1m":  "7d",
    "5m":  "60d",
    "15m": "60d",
    "1h":  "730d",
    "4h":  "730d",
    "1d":  "5y",
}

# Expected price column names
_PRICE_FIELDS = {"Open", "High", "Low", "Close", "Volume", "Adj Close"}


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Robustly flatten MultiIndex columns from yfinance.

    yfinance 0.2.x returns MultiIndex columns for single-ticker downloads.
    The level ordering varies by version:
      - Old: (Price, Ticker)  → level 0 = 'Close', 'Open', ...
      - New: (Ticker, Price)  → level 0 = 'BTC-USD', 'BTC-USD', ...
    We detect which level holds the price names and use that one.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    l0_vals = set(df.columns.get_level_values(0).tolist())
    l1_vals = set(df.columns.get_level_values(1).tolist())

    if _PRICE_FIELDS.intersection(l0_vals):
        df.columns = df.columns.get_level_values(0)
    elif _PRICE_FIELDS.intersection(l1_vals):
        df.columns = df.columns.get_level_values(1)
    else:
        # Last resort — just take level 0
        df.columns = df.columns.get_level_values(0)

    return df


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV data to 4h bars."""
    df.index = pd.to_datetime(df.index, utc=True)
    resampled = df.resample("4h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
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
        try:
            candles.append(Candle(
                timestamp=ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0) or 0),
            ))
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Skipping malformed candle row: {e}")
            continue
    return candles


class YFinanceProvider(DataProvider):
    """Live market data from Yahoo Finance via yfinance."""

    async def get_available_symbols(self) -> list[str]:
        return list(_SYMBOL_MAP.keys())

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        ticker = _SYMBOL_MAP.get(symbol.upper(), symbol)
        yf_interval = _YF_INTERVAL.get(timeframe, "1h")
        period = _YF_PERIOD.get(timeframe, "60d")

        # yfinance is blocking — run in a thread pool
        loop = asyncio.get_event_loop()
        try:
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
        except Exception as e:
            logger.error(f"yfinance download failed for {symbol} ({ticker}) [{timeframe}]: {e}")
            return []

        if df is None or df.empty:
            logger.warning(f"yfinance returned empty data for {symbol} [{timeframe}]")
            return []

        # Flatten MultiIndex columns (handles both old and new yfinance formats)
        df = _flatten_columns(df)

        # Validate required columns exist
        required = {"Open", "High", "Low", "Close"}
        if not required.issubset(set(df.columns)):
            logger.error(
                f"Missing OHLC columns for {symbol} [{timeframe}]. "
                f"Got: {list(df.columns)}"
            )
            return []

        if timeframe == "4h":
            df = _resample_to_4h(df)

        candles = _df_to_candles(df, limit)
        logger.debug(f"[yfinance] {symbol} [{timeframe}] → {len(candles)} candles")
        return candles
