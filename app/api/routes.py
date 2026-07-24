"""
API routes for Raina AI.

Thin layer — all logic lives in engines/analysis/scanner. Routes just
wire HTTP requests to those functions using the shared data provider
instance (injected from app.main).
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.data_providers.base import DataProvider
from app.engines import long_term_engine, scalping_engine
from app.models.signal import Signal
from app.scanner import multi_market_scanner
from app.storage.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# Set by app.main at startup — see get_provider() below.
_provider: DataProvider | None = None


def set_provider(provider: DataProvider) -> None:
    global _provider
    _provider = provider


def get_provider() -> DataProvider:
    if _provider is None:
        raise RuntimeError("Data provider not initialized")
    return _provider


@router.get("/health")
async def health():
    return {"status": "ok", "data_provider": settings.data_provider}
@router.get("/symbols")
async def symbols():
    return {"symbols": await get_provider().get_available_symbols()}


@router.get("/signals/long-term/{symbol}", response_model=Signal)
async def long_term_signal(symbol: str, timeframe: str = Query(default="4h")):
    return await long_term_engine.generate_signal(get_provider(), symbol.upper(), timeframe)


@router.get("/signals/scalp/{symbol}", response_model=Signal)
async def scalp_signal(symbol: str, timeframe: str = Query(default="5m")):
    return await scalping_engine.generate_signal(get_provider(), symbol.upper(), timeframe)


@router.get("/scan/long-term", response_model=list[Signal])
async def scan_long_term(
    only_actionable: bool = Query(default=False),
    timeframe: str = Query(default="4h"),
):
    return await multi_market_scanner.scan(
        get_provider(), settings.default_watchlist, engine="long_term",
        timeframe=timeframe, only_actionable=only_actionable,
    )


@router.get("/scan/scalp", response_model=list[Signal])
async def scan_scalp(
    only_actionable: bool = Query(default=False),
    timeframe: str = Query(default="5m"),
):
    return await multi_market_scanner.scan(
        get_provider(), settings.default_watchlist, engine="scalp",
        timeframe=timeframe, only_actionable=only_actionable,
    )


# ─── Signal History ───────────────────────────────────────────────────────────

@router.get("/history")
async def history(
    asset: str | None = Query(default=None, description="Filter by symbol e.g. EURUSD"),
    engine: str | None = Query(default=None, description="long_term or scalp"),
    direction: str | None = Query(default=None, description="BUY, SELL, or HOLD"),
    only_actionable: bool = Query(default=False, description="Exclude HOLD signals"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0),
):
    """Return stored signals with optional filters. Newest first."""
    from app.storage.signal_repo import get_signals
    return await get_signals(
        asset=asset, engine=engine, direction=direction,
        only_actionable=only_actionable, limit=limit, offset=offset,
    )


@router.get("/history/stats")
async def history_stats():
    """Summary counts and averages across all stored signals."""
    from app.storage.signal_repo import get_signal_stats
    return await get_signal_stats()


@router.get("/history/{asset}")
async def history_for_asset(
    asset: str,
    engine: str | None = Query(default=None),
    only_actionable: bool = Query(default=False),
    limit: int = Query(default=50, le=500),
):
    """Shorthand: history for a single symbol."""
    from app.storage.signal_repo import get_signals
    return await get_signals(
        asset=asset, engine=engine, only_actionable=only_actionable, limit=limit,
    )


# ─── RainX Web App Webhooks ───────────────────────────────────────────────────

@router.post("/webhook/subscription")
async def webhook_subscription(payload: dict):
    """
    Called by the RainX web app when a user's subscription changes.

    Expected payload:
    {
        "telegram_id": 123456789,
        "email": "user@example.com",
        "subscription": "standard" | "premium" | "none",
        "is_active": true | false
    }
    """
    from app.storage.user_repo import upsert_user
    tid = payload.get("telegram_id")
    if not tid:
        raise HTTPException(status_code=400, detail="telegram_id required")

    await upsert_user(
        telegram_id=int(tid),
        email=payload.get("email", ""),
        subscription=payload.get("subscription", "none"),
        is_active=bool(payload.get("is_active", False)),
    )

    # Notify the user on Telegram if bot is running
    from app.telegram.bot import _app
    if _app:
        sub = payload.get("subscription", "none")
        active = bool(payload.get("is_active", False))
        if active and sub != "none":
            label = "💎 Premium — MT5 auto-trading enabled" if sub == "premium" else "📊 Standard — Long-term signals active"
            try:
                await _app.bot.send_message(
                    chat_id=tid,
                    text=f"✅ Subscription activated!\n\n{label}\n\nSignals will arrive here automatically when strong setups appear (65%+ confidence).",
                )
            except Exception:
                pass

    return {"ok": True, "telegram_id": tid}


@router.get("/webhook/users/stats")
async def webhook_user_stats():
    """User counts by subscription tier — for the RainX admin dashboard."""
    from app.storage.user_repo import get_all_users_count
    return await get_all_users_count()


# ─── Price & Candle data ────────────────────────────────────────────────────

@router.get("/price")
async def live_price(symbol: str = Query(...)):
    """Latest price for any supported symbol via the active data provider."""
    provider = get_provider()
    sym = symbol.upper()
    try:
        # Get the most recent candle — works for Binance, Yahoo v8, etc.
        candles = await provider.get_candles(sym, "1m", limit=1)
        if not candles:
            # Fallback to 5m if 1m unavailable (e.g. forex on weekends)
            candles = await provider.get_candles(sym, "5m", limit=1)
        if not candles:
            raise ValueError(f"No price data returned for {sym}")
        return {"price": candles[-1].close, "symbol": sym}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/candles")
async def candle_series(
    symbol: str = Query(...),
    interval: str = Query(default="1h"),
    limit: int = Query(default=60, ge=10, le=500),
    before: Optional[int] = Query(default=None, description="Fetch candles ending before this Unix timestamp (seconds). Used for infinite-scroll history loading."),
):
    """
    OHLCV series via yfinance / OKX.
    Response shape: { values: [{ datetime, open, high, low, close }] }  newest-first

    `limit` controls how many candles to return (10–500, default 60).
    `before` (optional Unix epoch seconds) returns candles strictly older than that timestamp,
    enabling the frontend to page backwards through history.
    """
    provider = get_provider()
    # Normalise interval labels from either Twelve Data or RainX keys
    tf_alias = {"60min": "1h", "240min": "4h", "1day": "1d", "daily": "1d"}
    tf = tf_alias.get(interval, interval)
    before_dt: Optional[datetime] = (
        datetime.fromtimestamp(before, tz=timezone.utc) if before else None
    )
    try:
        candles = await provider.get_candles(symbol.upper(), tf, limit=limit, before=before_dt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    values = [
        {
            "datetime": c.timestamp.isoformat(),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
        }
        for c in reversed(candles)  # newest first
    ]
    return {"values": values, "symbol": symbol.upper(), "interval": interval}


# ─────────────────────────────────────────────────────────────────────────────
# Web Push Notification Endpoints
# ─────────────────────────────────────────────────────────────────────────────

# VAPID keys — generated once and stored in env or auto-generated on first run.
# Set VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY env vars on Railway to persist them.
_vapid_keys: dict | None = None


def _get_vapid_keys() -> dict:
    """
    Return VAPID key pair.

    Expected env vars (set these on Railway to persist across restarts):
      VAPID_PUBLIC_KEY  — base64url-encoded uncompressed EC P-256 point (65 raw bytes → ~87 chars)
      VAPID_PRIVATE_KEY — base64url-encoded raw EC P-256 scalar (32 bytes → ~43 chars)

    If not set, keys are auto-generated on first call but lost on restart.
    The public key MUST be base64url (not hex) — that is what browsers expect
    when calling PushManager.subscribe({ applicationServerKey }).
    """
    global _vapid_keys
    if _vapid_keys:
        return _vapid_keys
    private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    public_key = os.getenv("VAPID_PUBLIC_KEY", "")
    if private_key and public_key:
        _vapid_keys = {"private": private_key, "public": public_key}
        return _vapid_keys
    # Auto-generate (keys will be lost on restart unless env vars are set)
    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption,
        )
        priv = generate_private_key(SECP256R1())
        pub  = priv.public_key()
        # Public key: uncompressed point (65 bytes) encoded as base64url — what browsers need
        raw_pub = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64url = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
        # Private key: raw scalar (32 bytes) encoded as base64url — accepted by pywebpush
        priv_jwk = priv.private_numbers()
        raw_priv = priv_jwk.private_value.to_bytes(32, "big")
        priv_b64url = base64.urlsafe_b64encode(raw_priv).rstrip(b"=").decode()
        _vapid_keys = {"private": priv_b64url, "public": pub_b64url}
        logger.info("VAPID keys auto-generated. Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY env vars on Railway to persist them.")
        logger.info(f"VAPID_PUBLIC_KEY={pub_b64url}")
    except Exception as e:
        logger.warning(f"VAPID key generation failed: {e}")
        _vapid_keys = {"private": "", "public": ""}
    return _vapid_keys


class PushSubscriptionBody(BaseModel):
    subscription: dict  # {endpoint, keys: {p256dh, auth}}
    userId: str = ""
    activeMarkets: list[str] = []


@router.get("/push/keys")
async def push_keys():
    """Return the VAPID public key for the frontend to use when subscribing."""
    keys = _get_vapid_keys()
    return {"publicKey": keys.get("public", "")}


@router.post("/push/subscribe")
async def push_subscribe(body: PushSubscriptionBody):
    """Store or update a push subscription for a user."""
    sub = body.subscription
    endpoint = sub.get("endpoint", "")
    keys = sub.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Invalid subscription object")

    db = get_db()
    active_markets_json = json.dumps(body.activeMarkets)
    now = __import__("datetime").datetime.utcnow().isoformat()
    await db.execute(
        """
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, active_markets, created_at, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id = excluded.user_id,
            p256dh = excluded.p256dh,
            auth = excluded.auth,
            active_markets = excluded.active_markets,
            last_seen = excluded.last_seen
        """,
        (body.userId, endpoint, p256dh, auth, active_markets_json, now, now),
    )
    await db.commit()
    return {"ok": True}


@router.post("/push/send")
async def push_send(payload: dict):
    """
    Internal endpoint: send a push notification to a specific user or all subscribers.
    Body: { userId?: str, symbol?: str, title: str, body: str, category?: str }
    If symbol is provided, only send to subscribers who have that symbol in active_markets.
    """
    title = payload.get("title", "RainX")
    body_text = payload.get("body", "")
    symbol = payload.get("symbol")
    target_user = payload.get("userId")
    category = payload.get("category", "trading")

    db = get_db()
    if target_user:
        cursor = await db.execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (target_user,))
    else:
        cursor = await db.execute("SELECT * FROM push_subscriptions")
    rows = await cursor.fetchall()

    sent = 0
    failed = 0
    keys = _get_vapid_keys()
    if not keys.get("private"):
        return {"ok": False, "error": "VAPID keys not configured"}

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return {"ok": False, "error": "pywebpush not installed"}

    notification_payload = json.dumps({
        "title": title,
        "body": body_text,
        "category": category,
        "tag": f"rainx-{category}-{symbol or 'all'}",
    })

    for row in rows:
        # Filter by symbol if provided
        if symbol:
            try:
                active = json.loads(row["active_markets"] or "[]")
                if symbol not in active:
                    continue
            except Exception:
                continue

        subscription_info = {
            "endpoint": row["endpoint"],
            "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=notification_payload,
                vapid_private_key=keys["private"],
                vapid_claims={"sub": "mailto:admin@rainx.app"},
            )
            sent += 1
        except WebPushException as ex:
            logger.warning(f"Push failed for {row['endpoint'][:40]}: {ex}")
            if ex.response and ex.response.status_code in (404, 410):
                # Subscription expired — remove it
                await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (row["endpoint"],))
            failed += 1
        except Exception as ex:
            logger.warning(f"Push error: {ex}")
            failed += 1

    await db.commit()
    return {"ok": True, "sent": sent, "failed": failed}


@router.get("/signals/track/{symbol}")
async def track_signal(
    symbol: str,
    entry: float = Query(...),
    direction: str = Query(...),
    stop_loss: float = Query(...),
    take_profit_1: float = Query(...),
    take_profit_2: float = Query(default=0),
):
    """Track an active trade — returns current pip P&L and TP/SL hit status."""
    provider = get_provider()
    try:
        bars = await provider.get_ohlcv(symbol.upper(), "1m", limit=1)
        if not bars:
            raise HTTPException(status_code=404, detail="Price not available")
        current = float(bars[-1]["close"])
        sym = symbol.upper()
        if "JPY" in sym: pip = 0.01
        elif any(x in sym for x in ["BTC","ETH","BNB","SOL"]): pip = 1.0
        elif "XAU" in sym: pip = 0.1
        else: pip = 0.0001
        if direction.upper() == "BUY":
            pips = (current - entry) / pip
            tp1_hit = current >= take_profit_1
            tp2_hit = bool(take_profit_2) and current >= take_profit_2
            sl_hit = current <= stop_loss
        else:
            pips = (entry - current) / pip
            tp1_hit = current <= take_profit_1
            tp2_hit = bool(take_profit_2) and current <= take_profit_2
            sl_hit = current >= stop_loss
        pips = round(pips, 1)
        status = "active"
        if sl_hit: status = "stopped_out"
        elif tp2_hit: status = "tp2_hit"
        elif tp1_hit: status = "tp1_hit"
        sign = "+" if pips > 0 else ""
        return {"symbol": sym, "direction": direction.upper(), "entry": entry, "current_price": current, "pips": pips, "pips_label": sign + str(int(pips)) + " pips", "status": status, "tp1_hit": tp1_hit, "tp2_hit": tp2_hit, "sl_hit": sl_hit}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
