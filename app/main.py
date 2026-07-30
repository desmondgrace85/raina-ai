"""
Raina AI — FastAPI application entry point.

Startup sequence:
  1. Build the data provider (yfinance by default).
  2. Start the background scanner (periodic scans → Supabase push).
  3. Start the Telegram bot if TELEGRAM_BOT_TOKEN is configured (optional).

To swap in a different data source: add a branch in _build_provider().
"""
import logging

from fastapi import FastAPI

from app.api.routes import router, set_provider
from app.api.chat import router as chat_router, set_provider as chat_set_provider
from app.api.mt5_routes import router as mt5_router
from app.api.test_page import router as test_router
from app.config import settings
from app.data_providers.base import DataProvider

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Raina AI",
    description=(
        "Standalone trading intelligence engine — "
        "rule-based multi-market signal generation for Forex, Crypto, Gold & Commodities."
    ),
    version="0.2.0",
)


def _build_provider() -> DataProvider:
    if settings.data_provider in ("yfinance", "multi"):
        from app.data_providers.multi_provider import MultiProvider
        return MultiProvider()
    if settings.data_provider == "mock":
        from app.data_providers.mock_provider import MockDataProvider
        return MockDataProvider()
    raise ValueError(f"Unknown data provider: {settings.data_provider!r}")


@app.on_event("startup")
async def startup():
    from app.storage.database import init_db
    await init_db()

    # Pre-load VAPID keys while we're in async context — fixes the always-regenerate bug
    from app.api.routes import _load_vapid_keys_async
    await _load_vapid_keys_async()

    provider = _build_provider()
    set_provider(provider)
    chat_set_provider(provider)
    logger.info(f"Data provider: {settings.data_provider}")

    from app.scanner.background_scanner import start_background_scanner
    start_background_scanner(provider)

    import asyncio

    async def _keep_alive():
        import httpx, os
        url = f"http://0.0.0.0:{os.getenv(chr(80)+chr(79)+chr(82)+chr(84), 8000)}/health"
        async with httpx.AsyncClient() as client:
            while True:
                await asyncio.sleep(240)
                try:
                    await client.get(url, timeout=5)
                except Exception:
                    pass
    asyncio.create_task(_keep_alive())

    async def _stale_sweeper():
        from app.storage.mt5_repo import mark_disconnected_stale
        while True:
            await asyncio.sleep(300)
            await mark_disconnected_stale(minutes=5)
    asyncio.create_task(_stale_sweeper())

    async def _trade_reconciler():
        from app.storage.mt5_repo import reconcile_stale_open_trades
        # Run once 30s after startup to clear any existing stuck trades
        await asyncio.sleep(30)
        try:
            cleared = await reconcile_stale_open_trades(stale_hours=4)
            if cleared:
                logger.info("[trade-reconciler] Startup sweep cleared %d stale trade(s)", cleared)
        except Exception as e:
            logger.warning("[trade-reconciler] Startup sweep error: %s", e)
        # Then repeat every 30 minutes
        while True:
            await asyncio.sleep(1800)
            try:
                cleared = await reconcile_stale_open_trades(stale_hours=4)
                if cleared:
                    logger.info("[trade-reconciler] Cleared %d stale trade(s)", cleared)
            except Exception as e:
                logger.warning("[trade-reconciler] Error: %s", e)
    asyncio.create_task(_trade_reconciler())

    from app.news_bot.news_flow import start_news_flow
    start_news_flow()


@app.on_event("shutdown")
async def shutdown():
    from app.news_bot.news_flow import stop_news_flow
    stop_news_flow()

    from app.scanner.background_scanner import stop_background_scanner
    stop_background_scanner()

    from app.storage.database import close_db
    await close_db()


app.include_router(router)
app.include_router(chat_router)
app.include_router(mt5_router)
app.include_router(test_router)

from app.api.community_ai import router as community_ai_router
app.include_router(community_ai_router)


@app.get("/")
async def root():
    return {
        "name": "Raina AI",
        "version": "0.2.0",
        "status": "running",
        "data_provider": settings.data_provider,
        "docs": "/docs",
        "endpoints": {
            "long_term_signal": "/signals/long-term/{symbol}",
            "scalp_signal": "/signals/scalp/{symbol}",
            "scan_long_term": "/scan/long-term",
            "scan_scalp": "/scan/scalp",
            "symbols": "/symbols",
        },
    }

