"""
Real market data provider using yfinance (Yahoo Finance).

Covers Forex, Crypto, Gold, and Commodities with zero API keys.
Symbols are mapped from Raina AI's internal naming to yfinance tickers.

Robustness features:
  • Multi-period fallback: tries progressively shorter periods if the
    primary period returns no data (common for futures/forex on 1h)
  • Ticker.history() fallback when yf.download() fails
  • NaN row filtering to remove trading-hour gaps in futures data
  • Smart MultiIndex flattening for both old and new yfinance versions
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
    "BRENTUSD":  "BZ=F",
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

# Primary period, with fallback list if primary returns empty data.
# Futures (GC=F, CL=F) and some forex tickers often fail on very long 1h pulls.
_YF_PERIOD_CHAIN: dict[str, list[str]] = {
    "1m":  ["7d"],
    "5m":  ["60d", "30d"],
    "15m": ["60d", "30d"],
    "1h":  ["365d", "180d", "90d", "60d"],   # multiple fallbacks
    "4h":  ["365d", "180d", "90d", "60d"],
    "1d":  ["5y", "2y", "1y"],
}

# Expected price column names
_PRICE_FIELDS = {"Open", "High", "Low", "Close", "Volume", "Adj Close"}


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Robustly flatten MultiIndex columns from yfinance.

    yfinance 0.2.x returns MultiIndex for single-ticker downloads.
    Level ordering varies by version:
      Old (0.2.x ≤44): (Price, Ticker) → level 0 has field names
      New (0.2.x ≥50): (Ticker, Price) → level 1 has field names
    We auto-detect by checking which level contains OHLCV field names.
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
        logger.warning(f"Unexpected MultiIndex columns: {df.columns.tolist()[:6]}")
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


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with NaN in any OHLC column (trading-hour gaps in futures data)."""
    ohlc_cols = [c for c in ("Open", "High", "Low", "Close") if c in df.columns]
    return df.dropna(subset=ohlc_cols)


def _df_to_candles(df: pd.DataFrame, limit: int) -> list[Candle]:
    df = _clean_df(df).tail(limit)
    candles = []
    for ts, row in df.iterrows():
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            c = float(row["Close"])
            if c != c:  # NaN check
                continue
            candles.append(Candle(
                timestamp=ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=c,
                volume=float(row.get("Volume", 0) or 0),
            ))
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Skipping malformed candle row: {e}")
    return candles


def _download_with_fallback(
    ticker: str, interval: str, periods: list[str]
) -> pd.DataFrame:
    """
    Try yf.download with each period in order, returning the first
    non-empty result. Falls back to Ticker.history() on all failures.
    """
    for period in periods:
        try:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
            if df is not None and not df.empty:
                logger.debug(f"yf.download {ticker} [{interval}, {period}] → {len(df)} rows")
                return df
            logger.debug(f"yf.download {ticker} [{interval}, {period}] → empty, trying next")
        except Exception as e:
            logger.debug(f"yf.download {ticker} [{interval}, {period}] failed: {e}")

    # Last resort: Ticker.history() — different code path, sometimes succeeds
    # when download() fails (e.g. futures roll-over periods)
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=periods[-1], interval=interval, auto_adjust=True)
        if df is not None and not df.empty:
            logger.info(f"Ticker.history fallback succeeded for {ticker} [{interval}]")
            return df
    except Exception as e:
        logger.warning(f"Ticker.history fallback also failed for {ticker}: {e}")

    return pd.DataFrame()


class YFinanceProvider(DataProvider):
    """Live market data from Yahoo Finance via yfinance."""

    async def get_available_symbols(self) -> list[str]:
        return list(_SYMBOL_MAP.keys())

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> list[Candle]:
        ticker = _SYMBOL_MAP.get(symbol.upper(), symbol)
        yf_interval = _YF_INTERVAL.get(timeframe, "1h")
        periods = _YF_PERIOD_CHAIN.get(timeframe, ["60d"])

        loop = asyncio.get_event_loop()
        try:
            df = await loop.run_in_executor(
                None,
                lambda: _download_with_fallback(ticker, yf_interval, periods),
            )
        except Exception as e:
            logger.error(f"Data fetch error {symbol} ({ticker}) [{timeframe}]: {e}")
            return []

        if df is None or df.empty:
            logger.warning(f"No data returned for {symbol} ({ticker}) [{timeframe}]")
            return []

        # Flatten MultiIndex columns (handles both old and new yfinance formats)
        df = _flatten_columns(df)

        # Validate required columns exist
        required = {"Open", "High", "Low", "Close"}
        if not required.issubset(set(df.columns)):
            logger.error(
                f"Missing OHLC columns for {symbol} [{timeframe}]. "
                f"Columns: {list(df.columns)}"
            )
            return []

        if timeframe == "4h":
            df = _resample_to_4h(df)

        candles = _df_to_candles(df, limit)
        logger.debug(f"[yfinance] {symbol} [{timeframe}] → {len(candles)} clean candles")
        return candles
