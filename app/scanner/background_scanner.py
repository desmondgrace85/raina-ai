"""
Background market scanner — Raina AI.

Two watchers run continuously:
  M15  — analyses 15-minute candles, checks every 15 min
  H1   — analyses 1-hour candles,  checks every 60 min
  H4   — analyses 4-hour candles,  checks every 240 min
  SCALP — 5m scalp scan every 5 min, queues MT5 trades for premium users

A signal is only pushed when:
  • direction is BUY or SELL (not HOLD)
  • confidence >= MIN_SIGNAL_CONFIDENCE (default 65%)

When nothing qualifies, the scanner stays silent — no spam.
Signals are saved to the history database regardless of whether
they meet the push threshold.

Scalping: qualifying signals are auto-queued to connected premium MT5 users.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.data_providers.base import DataProvider
from app.models.signal import Signal

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _scan_and_push(provider: DataProvider, timeframe: str) -> None:
    """Run a full watchlist scan for the given timeframe and push qualifying signals."""
    from app.scanner import multi_market_scanner
    from app.storage.signal_repo import save_signal
    from app.storage.supabase_push import push_signal_to_supabase, get_symbol_to_users

      # Build {symbol: [user_ids]} from user-selected markets
      symbol_to_users = {}
      try:
          symbol_to_users = await get_symbol_to_users()
      except Exception as _e:
          logger.warning(f"Could not load user markets: {_e}")
      watchlist = list(symbol_to_users.keys()) if symbol_to_users else settings.default_watchlist
    
    try:
        signals: list[Signal] = await multi_market_scanner.scan(
            provider,
            settings.default_watchlist,
            engine="long_term",
            timeframe=timeframe,
            only_actionable=False,   # we apply our own threshold below
        )
    except Exception as e:
        logger.error(f"Scan error ({timeframe}): {e}")
        return

    pushed = 0
    for sig in signals:
        # Save every signal regardless of quality
        row_id = -1
        try:
            row_id = await save_signal(sig, sent_telegram=False)
        except Exception as e:
            logger.warning(f"DB save failed: {e}")

        # Only push signals that clear the confidence gate
        if sig.direction.value == "HOLD":
            continue
        if sig.confidence < settings.min_signal_confidence:
            logger.debug(
                f"[{timeframe}] {sig.asset} {sig.direction.value} "
                f"{sig.confidence:.1f}% — below threshold, suppressed"
            )
            continue

        logger.info(
            f"[{timeframe}] SIGNAL {sig.direction.value} {sig.asset} "
            f"confidence={sig.confidence:.1f}%"
        )
        try:
            target_users = symbol_to_users.get(sig.asset.upper(), []) if 'symbol_to_users' in dir() else []
            sent = await push_signal_to_supabase(sig, target_users, timeframe)
            pushed += sent
        except Exception as e:
            logger.warning(f"Push failed: {e}")

    if pushed:
        logger.info(f"[{timeframe}] Delivered {pushed} signal message(s) to subscribers")
    else:
        logger.info(f"[{timeframe}] Scan complete — no signals above 65% confidence")


async def _news_watcher() -> None:
    """
    Runs every 30 min. Fetches high-impact economic events for today.
    If a new event is found that hasn't been announced yet, pushes a
    Telegram notification to all subscribers before the signal fires.
    """
    from app.scanner.news_scanner import get_todays_events
    # Telegram removed — news_flow.py handles community posts

    try:
        events = await get_todays_events()
    except Exception as e:
        logger.warning(f"[news_watcher] fetch error: {e}")
        return

    if not events:
        return

    # Build a summary of upcoming/released events
    lines = ["📰 *Market News Alert*\n"]
    for ev in events:
        actual   = ev.get("actual")
        forecast = ev.get("forecast")
        currency = ev.get("currency", "")
        title    = ev.get("title", "")

        if actual:
            beat = ""
            try:
                a = float(str(actual).replace("%",""))
                f = float(str(forecast).replace("%","")) if forecast else None
                if f is not None:
                    beat = " 🟢 Beat" if a > f else " 🔴 Miss"
            except Exception:
                pass
            lines.append(f"📅 *{title}* ({currency}): `{actual}` (forecast: {forecast or '—'}){beat}")
        else:
            lines.append(f"⏰ *{title}* ({currency}) releasing soon — forecast: `{forecast or '—'}`")

    lines.append("\n🔍 Running market scan for entry opportunities...")
    msg = "\n".join(lines)

        logger.info(f"[news_watcher] {len(events)} event(s) — news_flow.py handles community posts")


async def _scalp_and_trade(provider: DataProvider) -> None:
    """
    5-minute scalp scan.  Qualifying signals are:
      1. Pushed as Telegram notifications to subscribers (standard+)
      2. Queued as MT5 trade orders for connected premium users
    """
    from app.scanner import multi_market_scanner
    from app.storage.signal_repo import save_signal
    from app.storage.supabase_push import push_signal_to_supabase, get_symbol_to_users
    from app.mt5.trade_manager import queue_signal_for_all

    try:
        signals: list[Signal] = await multi_market_scanner.scan(
            provider,
            settings.default_watchlist,
            engine="scalp",
            timeframe="5m",
            only_actionable=False,
        )
    except Exception as e:
        logger.error(f"Scalp scan error: {e}")
        return

    for sig in signals:
        try:
            row_id = await save_signal(sig, sent_telegram=False)
        except Exception:
            row_id = -1

        if sig.direction.value == "HOLD" or sig.confidence < settings.min_signal_confidence:
            continue

        logger.info(f"[5m scalp] SIGNAL {sig.direction.value} {sig.asset} {sig.confidence:.1f}%")

        # Push Telegram notification
        try:
            target_users = symbol_to_users.get(sig.asset.upper(), []) if 'symbol_to_users' in dir() else []
            sent = await push_signal_to_supabase(sig, target_users, timeframe)
            pushed += sent
        except Exception as e:
            logger.warning(f"Scalp push failed: {e}")

        # Queue MT5 trades for eligible premium users
        try:
            queued = await queue_signal_for_all(sig)
            if queued:
                logger.info(f"[5m scalp] Queued MT5 trade for {queued} user(s)")
        except Exception as e:
            logger.warning(f"MT5 queue failed: {e}")


def start_background_scanner(provider: DataProvider) -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # M15 watcher
    _scheduler.add_job(
        _scan_and_push,
        trigger="interval",
        minutes=settings.m15_scan_interval_minutes,
        args=[provider, "15m"],
        id="m15_scan",
        name="M15 market watcher",
        misfire_grace_time=60,
    )

    # H1 watcher
    _scheduler.add_job(
        _scan_and_push,
        trigger="interval",
        minutes=settings.h1_scan_interval_minutes,
        args=[provider, "1h"],
        id="h1_scan",
        name="H1 market watcher",
        misfire_grace_time=120,
    )

    # H4 watcher
    _scheduler.add_job(
        _scan_and_push,
        trigger="interval",
        minutes=settings.h4_scan_interval_minutes,
        args=[provider, "4h"],
        id="h4_scan",
        name="H4 market watcher",
        misfire_grace_time=300,
    )

    # News/calendar watcher — pushes CPI, NFP etc. alerts
    _scheduler.add_job(
        _news_watcher,
        trigger="interval",
        minutes=30,
        id="news_watch",
        name="Economic calendar watcher",
        misfire_grace_time=60,
    )

    # 5m scalp watcher — also queues MT5 trades
    _scheduler.add_job(
        _scalp_and_trade,
        trigger="interval",
        minutes=5,
        args=[provider],
        id="scalp_scan",
        name="5m scalp watcher + MT5 queue",
        misfire_grace_time=30,
    )

    _scheduler.start()
    logger.info(
        f"Background scanner started — "
        f"M15 every {settings.m15_scan_interval_minutes}m | "
        f"H1 every {settings.h1_scan_interval_minutes}m | "
        f"H4 every {settings.h4_scan_interval_minutes}m | "
        f"Threshold: {settings.min_signal_confidence}%"
    )
    print(
        f"✅ Scanner running — M15/{settings.m15_scan_interval_minutes}m  "
        f"H1/{settings.h1_scan_interval_minutes}m  "
        f"H4/{settings.h4_scan_interval_minutes}m  "
        f"(min confidence {settings.min_signal_confidence}%)",
        flush=True,
    )


def stop_background_scanner() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
