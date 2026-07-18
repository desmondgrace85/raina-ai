"""
API routes for Raina AI.

Thin layer — all logic lives in engines/analysis/scanner. Routes just
wire HTTP requests to those functions using the shared data provider
instance (injected from app.main).
"""
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.data_providers.base import DataProvider
from app.engines import long_term_engine, scalping_engine
from app.models.signal import Signal
from app.scanner import multi_market_scanner

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}

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


@router.get("/telegram/chatid")
async def telegram_chatid():
    """
    Helper: shows Chat IDs of everyone who has messaged the bot.
    Step 1 — Send /start to your bot in Telegram.
    Step 2 — Visit this endpoint.
    Step 3 — Copy your chat_id and save it as the TELEGRAM_CHAT_ID secret.
    """
    from app.telegram.bot import _seen_chats
    if not _seen_chats:
        return {
            "instruction": (
                "No chats seen yet. Open Telegram, find your bot, and send /start. "
                "Then reload this page."
            ),
            "chats": [],
        }
    return {
        "instruction": "Save the correct chat_id below as your TELEGRAM_CHAT_ID secret.",
        "chats": _seen_chats,
    }


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
