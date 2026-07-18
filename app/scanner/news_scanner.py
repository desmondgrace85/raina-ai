"""
News & Economic Calendar scanner.

Sources (all free, no API key needed):
  1. ForexFactory unofficial JSON  — high-impact economic events
  2. yfinance .news                — asset-specific market news headlines

Provides:
  get_todays_events()     — list of today's high-impact calendar events
  get_asset_news(symbol)  — recent news headlines for an asset
  compute_news_bias(...)  — net directional bias from events + headlines (-100..100)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── ForexFactory calendar ─────────────────────────────────────────────────────
_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

HIGH_IMPACT_EVENTS = {
    "cpi", "core cpi", "nfp", "non-farm", "fomc", "interest rate",
    "gdp", "ppi", "core ppi", "retail sales", "unemployment", "ism",
    "pce", "core pce", "rba", "boe", "ecb", "boc", "fed", "inflation",
}

CURRENCY_ASSETS = {
    "USD": ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDCHF", "AUDUSD"],
    "EUR": ["EURUSD", "EURJPY", "EURGBP"],
    "GBP": ["GBPUSD", "GBPJPY", "EURGBP"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "XAU": ["XAUUSD"],
    "BTC": ["BTCUSD"],
}


async def get_todays_events() -> list[dict]:
    """Return today's high-impact economic events from ForexFactory."""
    today = datetime.now(timezone.utc).date()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_FF_URL)
            if resp.status_code != 200:
                return []
            events = resp.json()
    except Exception as e:
        logger.warning(f"FF calendar fetch failed: {e}")
        return []

    results = []
    for ev in events:
        try:
            ev_date = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00")).date()
        except Exception:
            continue
        if ev_date != today:
            continue
        if ev.get("impact", "").lower() != "high":
            continue
        title = ev.get("title", "").lower()
        if not any(k in title for k in HIGH_IMPACT_EVENTS):
            continue
        results.append({
            "title": ev.get("title"),
            "currency": ev.get("currency", ""),
            "actual": ev.get("actual"),
            "forecast": ev.get("forecast"),
            "previous": ev.get("previous"),
            "time": ev.get("date"),
        })
    return results


def _parse_num(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    try:
        return float(str(val).replace("%", "").replace("K", "").replace("M", "").replace("B", "").strip())
    except Exception:
        return None


def event_bias(event: dict) -> tuple[float, str]:
    """
    Returns (score, explanation) for a single economic event.
    score > 0 = bullish for the event's currency
    score < 0 = bearish for the event's currency
    """
    actual   = _parse_num(event.get("actual"))
    forecast = _parse_num(event.get("forecast"))
    previous = _parse_num(event.get("previous"))
    title    = event.get("title", "CPI")
    currency = event.get("currency", "USD")

    if actual is None:
        return 0.0, f"{title}: no actual data yet"

    # Beat vs miss
    if forecast is not None:
        diff_pct = (actual - forecast) / (abs(forecast) + 1e-9) * 100
        if diff_pct > 5:
            return 70.0, f"{title} BEAT forecast ({actual} vs {forecast}) — {currency} strengthens"
        elif diff_pct < -5:
            return -70.0, f"{title} MISSED forecast ({actual} vs {forecast}) — {currency} weakens"

    # vs previous
    if previous is not None:
        diff_pct = (actual - previous) / (abs(previous) + 1e-9) * 100
        if diff_pct > 5:
            return 50.0, f"{title} rose vs previous ({actual} vs {previous}) — {currency} positive"
        elif diff_pct < -5:
            return -50.0, f"{title} fell vs previous ({actual} vs {previous}) — {currency} negative"

    return 0.0, f"{title}: result in line with expectations — neutral"


# ── yfinance news ─────────────────────────────────────────────────────────────

_BULLISH_WORDS = {
    "beat", "surge", "rally", "strong", "rise", "gain", "bullish", "high",
    "better", "above", "record", "jump", "soar", "positive", "growth",
    "recover", "breakout", "upside", "hawkish", "rate hike", "hot",
}
_BEARISH_WORDS = {
    "miss", "drop", "fall", "weak", "decline", "bearish", "low", "below",
    "worse", "plunge", "crash", "negative", "slow", "recession", "dovish",
    "cut", "cool", "soft", "sell-off", "selloff", "slump",
}


def _headline_sentiment(title: str) -> float:
    """Quick keyword sentiment: -1..+1"""
    tl = title.lower()
    bull = sum(1 for w in _BULLISH_WORDS if w in tl)
    bear = sum(1 for w in _BEARISH_WORDS if w in tl)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


async def get_asset_news(symbol: str) -> list[dict]:
    """Fetch recent news headlines from yfinance for the asset."""
    try:
        import yfinance as yf
        ticker_map = {
            "EURUSD": "EURUSD=X", "USDJPY": "JPY=X", "GBPUSD": "GBPUSD=X",
            "XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "USDCAD": "CAD=X",
            "AUDUSD": "AUDUSD=X", "USDCHF": "CHF=X",
        }
        tk_sym = ticker_map.get(symbol, symbol)
        news = await asyncio.to_thread(lambda: yf.Ticker(tk_sym).news)
        return [{"title": n.get("content", {}).get("title") or n.get("title", ""),
                 "published": n.get("content", {}).get("pubDate") or ""} for n in (news or [])[:10]]
    except Exception as e:
        logger.debug(f"yfinance news fetch for {symbol}: {e}")
        return []


def compute_news_bias(
    symbol: str,
    events: list[dict],
    headlines: list[dict],
) -> tuple[float, str]:
    """
    Returns (score, explanation) where score is -100..100.
    Combines economic events + headline sentiment, adjusted for asset direction.
    """
    parts = []
    net_score = 0.0
    weight_sum = 0.0

    # ── Economic events ───────────────────────────────────────────────────────
    for ev in events:
        currency = ev.get("currency", "")
        score, msg = event_bias(ev)
        if score == 0.0:
            parts.append(f"📅 {msg}")
            continue

        # Flip sign if currency is the QUOTE in the pair (e.g. JPY in USDJPY)
        sym_upper = symbol.upper()
        if len(sym_upper) == 6:
            base  = sym_upper[:3]
            quote = sym_upper[3:]
            if currency == quote:
                score = -score
            elif currency != base:
                score *= 0.3  # indirect impact

        net_score  += score * 3.0   # news events carry triple weight
        weight_sum += 3.0
        parts.append(f"📅 {msg}")

    # ── Headline sentiment ────────────────────────────────────────────────────
    headline_scores = [_headline_sentiment(h["title"]) for h in headlines if h.get("title")]
    if headline_scores:
        avg_sentiment = sum(headline_scores) / len(headline_scores)
        hs = avg_sentiment * 40.0  # scale to -40..40
        net_score  += hs
        weight_sum += 1.0
        mood = "broadly bullish" if avg_sentiment > 0.2 else "broadly bearish" if avg_sentiment < -0.2 else "mixed"
        parts.append(f"📰 Market headlines are {mood} for {symbol}")

    if weight_sum == 0:
        return 0.0, "No significant news events today."

    final = max(-100.0, min(100.0, net_score / weight_sum))
    explanation = " | ".join(parts) if parts else "No impactful news data."
    return final, explanation
