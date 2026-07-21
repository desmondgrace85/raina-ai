"""
Cloud-safe market data provider for Raina AI.

Yahoo Finance's yfinance library is blocked from cloud IPs (Railway, Heroku,
Render, etc.) because they share IP ranges with scrapers. Binance is also
geo-restricted from US-region cloud IPs. This provider uses:

  • OKX REST API      — crypto (BTC, ETH, SOL, …)  — free, no auth, cloud-safe
  • Yahoo Finance v8  — forex, gold, commodities     — direct HTTP with browser
                         session + crumb token (bypasses the yfinance block)

No API keys required.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

from app.data_providers.base import DataProvider
from app.models.signal import Candle

logger = logging.getLogger(__name__)

# ── Symbol maps ─────────────────────────────────────────────────────────────

_OKX_SYMBOLS: dict[str, str] = {
    "BTCUSD":   "BTC-USDT",
    "ETHUSD":   "ETH-USDT",
    "BNBUSD":   "BNB-USDT",
    "SOLUSD":   "SOL-USDT",
    "XRPUSD":   "XRP-USDT",
    "ADAUSD":   "ADA-USDT",
    "DOTUSD":   "DOT-USDT",
    "MATICUSD": "MATIC-USDT",
    "LINKUSD":  "LINK-USDT",
}

# OKX bar values — note 1H/4H/1D are uppercase
_OKX_INTERVALS: dict[str, str] = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "1h":  "1H",
    "4h":  "4H",
    "1d":  "1D",
}

_YAHOO_SYMBOLS: dict[str, str] = {
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
    # Gold & metals
    "XAUUSD":   "GC=F",
    "XAGUSD":   "SI=F",
    # Commodities
    "WTICOUSD": "CL=F",
    "BRENTUSD": "BZ=F",
    "NATGAS":   "NG=F",
}

# Yahoo interval per timeframe (4h is fetched as 1h then resampled)
_YAHOO_INTERVALS: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "1h", "1d": "1d",
}

# Yahoo range (how much history to request) — longer = more data for TA
_YAHOO_RANGES: dict[str, str] = {
    "1m":  "1d",
    "5m":  "5d",
    "15m": "60d",
    "1h":  "730d",
    "4h":  "730d",
    "1d":  "5y",
}

# Browser-like headers so Yahoo Finance doesn't return an empty 200 body
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://finance.yahoo.com/",
    "Origin":          "https://finance.yahoo.com",
}

# ── Yahoo Finance v8 session + crumb (module-level, reused across calls) ────

_yahoo_session: Optional[requests.Session] = None
_yahoo_crumb:   Optional[str]              = None
_yahoo_crumb_ts: float                     = 0.0
_CRUMB_TTL = 3600  # seconds before we refresh the crumb


def _build_yahoo_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_BROWSER_HEADERS)
    # Warm up cookies — Yahoo requires a prior visit to finance.yahoo.com
    for url in [
        "https://finance.yahoo.com",
        "https://fc.yahoo.com",  # sets GUCS / A3 consent cookie
    ]:
        try:
            s.get(url, timeout=10)
        except Exception:
            pass
    return s


def _get_yahoo_session() -> requests.Session:
    global _yahoo_session
    if _yahoo_session is None:
        _yahoo_session = _build_yahoo_session()
    return _yahoo_session


def _get_yahoo_crumb() -> str:
    global _yahoo_crumb, _yahoo_crumb_ts
    now = time.time()
    if _yahoo_crumb and (now - _yahoo_crumb_ts) < _CRUMB_TTL:
        return _yahoo_crumb

    s = _get_yahoo_session()
    for endpoint in [
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
    ]:
        try:
            r = s.get(endpoint, timeout=10)
            if r.status_code == 200 and r.text and r.text.strip() not in ("", "null"):
                _yahoo_crumb = r.text.strip()
                _yahoo_crumb_ts = now
                logger.debug(f"Yahoo crumb refreshed from {endpoint}")
                return _yahoo_crumb
        except Exception as e:
            logger.debug(f"Crumb endpoint {endpoint} failed: {e}")

    logger.warning("Could not fetch Yahoo crumb — proceeding without it")
    return ""


# ── OKX fetcher ──────────────────────────────────────────────────────────────

def _fetch_okx_sync(inst_id: str, bar: str, limit: int, after_ms: Optional[int] = None) -> list[Candle]:
    """
    Fetch OHLCV candles from OKX public REST API (no auth, no geo-block).
    OKX returns data newest-first; we reverse to oldest-first before returning.
    Response columns: [ts_ms, open, high, low, close, vol_base, volCcy, volCcyQuote, confirm]

    after_ms: if set, only return candles with timestamp < after_ms (older candles).
              Maps to OKX's `after` pagination param (returns bars older than the ts).
    """
    url = "https://www.okx.com/api/v5/market/candles"
    # OKX max limit per request is 300
    fetch_limit = min(limit + 50, 300)
    params: dict = {"instId": inst_id, "bar": bar, "limit": fetch_limit}
    if after_ms is not None:
        params["after"] = str(after_ms)  # OKX: returns candles older than this ts_ms

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()

    body = r.json()
    if body.get("code") != "0":
        raise ValueError(f"OKX API error: {body.get('msg', 'unknown')} (code {body.get('code')})")

    data = body.get("data", [])

    candles: list[Candle] = []
    for row in reversed(data):  # OKX newest-first → reverse to oldest-first
        try:
            ts = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
            candles.append(Candle(
                timestamp=ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            ))
        except (ValueError, TypeError, IndexError):
            pass

    return candles[-limit:]


# ── Yahoo Finance v8 fetcher ────────────────────────────────────────────────

def _parse_yahoo_chart(j: dict) -> pd.DataFrame:
    """Parse a Yahoo Finance v8 chart JSON response into a DataFrame."""
    results = j.get("chart", {}).get("result") or []
    if not results:
        error = j.get("chart", {}).get("error") or {}
        raise ValueError(f"Yahoo v8 empty result — error: {error}")

    result     = results[0]
    timestamps = result.get("timestamp") or []
    quote      = (result.get("indicators", {}).get("quote") or [{}])[0]

    opens   = quote.get("open",   [])
    highs   = quote.get("high",   [])
    lows    = quote.get("low",    [])
    closes  = quote.get("close",  [])
    volumes = quote.get("volume", [])

    rows: list[dict] = []
    for i, ts in enumerate(timestamps):
        try:
            o = opens[i]; h = highs[i]; l = lows[i]; c = closes[i]
            if None in (o, h, l, c):
                continue
            # NaN check
            if any(v != v for v in (o, h, l, c)):
                continue
            rows.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                "Open":   float(o),
                "High":   float(h),
                "Low":    float(l),
                "Close":  float(c),
                "Volume": float(volumes[i] or 0) if i < len(volumes) else 0.0,
            })
        except (IndexError, TypeError, ValueError):
            continue

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("timestamp")
    return df


def _fetch_yahoo_sync(ticker: str, interval: str, range_: str, period2: Optional[int] = None) -> pd.DataFrame:
    """
    Fetch OHLCV from Yahoo Finance v8 chart endpoint using a browser-like
    session + crumb token so Railway's shared IP is not blocked.

    period2: if set, fetch data ending at this Unix timestamp (seconds),
             using period1/period2 instead of range for backward paging.
    """
    s     = _get_yahoo_session()
    crumb = _get_yahoo_crumb()

    if period2 is not None:
        # Compute how many seconds of history to fetch based on interval
        _range_seconds: dict[str, int] = {
            "1m": 86400, "5m": 5 * 86400, "15m": 60 * 86400,
            "1h": 730 * 86400, "4h": 730 * 86400, "1d": 5 * 365 * 86400,
        }
        window = _range_seconds.get(interval, 730 * 86400)
        params: dict = {
            "interval": interval,
            "period1": period2 - window,
            "period2": period2,
        }
    else:
        params: dict = {"interval": interval, "range": range_}
    if crumb:
        params["crumb"] = crumb

    last_exc: Exception | None = None
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}"
        try:
            r = s.get(url, params=params, timeout=20)
            if r.status_code == 401:
                # Crumb expired — rebuild session and retry once
                global _yahoo_session, _yahoo_crumb
                _yahoo_session = None
                _yahoo_crumb   = None
                s     = _get_yahoo_session()
                crumb = _get_yahoo_crumb()
                params["crumb"] = crumb
                r = s.get(url, params=params, timeout=20)

            r.raise_for_status()
            body = r.text.strip()
            if not body:
                raise ValueError("Empty response body from Yahoo v8")
            return _parse_yahoo_chart(r.json())
        except Exception as e:
            last_exc = e
            logger.debug(f"Yahoo v8 {host} failed for {ticker}: {e}")

    raise RuntimeError(f"All Yahoo v8 hosts failed for {ticker}: {last_exc}")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    df.index = pd.to_datetime(df.index, utc=True)
    return df.resample("4h").agg({
        "Open": "first", "High": "max",
        "Low":  "min",   "Close": "last",
        "Volume": "sum",
    }).dropna()


def _df_to_candles(df: pd.DataFrame, limit: int) -> list[Candle]:
    candles: list[Candle] = []
    for ts, row in df.tail(limit).iterrows():
        try:
            c = float(row["Close"])
            if c != c:
                continue
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            candles.append(Candle(
                timestamp=dt,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=c,
                volume=float(row.get("Volume", 0) or 0),
            ))
        except (KeyError, ValueError, TypeError):
            pass
    return candles


# ── Provider class ───────────────────────────────────────────────────────────

class MultiProvider(DataProvider):
    """
    Cloud-safe market data: OKX for crypto, Yahoo Finance v8 for forex/gold.
    """

    async def get_available_symbols(self) -> list[str]:
        return list(_OKX_SYMBOLS) + list(_YAHOO_SYMBOLS)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        before: Optional[datetime] = None,
    ) -> list[Candle]:
        sym  = symbol.upper()
        loop = asyncio.get_event_loop()

        # ── Crypto → OKX ────────────────────────────────────────────────
        if sym in _OKX_SYMBOLS:
            inst_id  = _OKX_SYMBOLS[sym]
            bar      = _OKX_INTERVALS.get(timeframe, "1H")
            # Convert `before` datetime → OKX `after` param (ms timestamp of oldest known bar)
            after_ms = int(before.timestamp() * 1000) if before else None
            try:
                candles = await loop.run_in_executor(
                    None,
                    lambda: _fetch_okx_sync(inst_id, bar, limit, after_ms=after_ms),
                )
                logger.debug(f"[OKX] {sym} [{timeframe}] → {len(candles)} candles (before={before})")
                return candles
            except Exception as e:
                logger.error(f"[OKX] {sym} [{timeframe}] failed: {e}")
                return []

        # ── Forex / Gold / Commodities → Yahoo Finance v8 ───────────────
        if sym in _YAHOO_SYMBOLS:
            ticker   = _YAHOO_SYMBOLS[sym]
            interval = _YAHOO_INTERVALS.get(timeframe, "1h")
            range_   = _YAHOO_RANGES.get(timeframe, "60d")
            # When paging backwards, use period1/period2 instead of range
            period2  = int(before.timestamp()) if before else None
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda: _fetch_yahoo_sync(ticker, interval, range_, period2=period2),
                )
                if df is None or df.empty:
                    logger.warning(f"[Yahoo] {sym} ({ticker}) [{timeframe}] → empty")
                    return []
                if timeframe == "4h":
                    df = _resample_to_4h(df)
                # When paging, filter out bars >= before to avoid overlap
                if before:
                    df = df[df.index < before]
                candles = _df_to_candles(df, limit)
                logger.debug(f"[Yahoo] {sym} [{timeframe}] → {len(candles)} candles (before={before})")
                return candles
            except Exception as e:
                logger.error(f"[Yahoo] {sym} [{timeframe}] failed: {e}")
                return []

        logger.warning(f"[MultiProvider] Unknown symbol: {sym}")
        return []
