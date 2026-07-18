"""
Raina AI Telegram bot.

All signal access is gated behind authentication.
Users must login or signup via /start before any signals are delivered.

Delivery modes:
  • Auto-push (background scanner) — sent to every active subscriber
  • On-demand  (/signal, /scan)    — only for authenticated users

Subscription tiers:
  standard — long-term M15 + H1-H4 signals
  premium  — standard + MT5 auto-trade execution (future)
"""
import logging
import os
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.data_providers.base import DataProvider
from app.models.signal import Signal

logger = logging.getLogger(__name__)

_app: Optional[Application] = None
_provider: Optional[DataProvider] = None
_seen_chats: list[dict] = []


# ── Signal formatter ───────────────────────────────────────────────────────────

def _fmt(signal: Signal) -> str:
    direction_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(signal.direction.value, "⚪")
    risk_emoji = {"LOW": "🟡", "MEDIUM": "🟠", "HIGH": "🔴"}.get(
        str(signal.risk_level.value if hasattr(signal.risk_level, "value") else signal.risk_level), "⚪"
    )

    lines = [
        f"{direction_emoji} RAINA AI — {signal.direction.value}",
        f"Asset: {signal.asset}   Timeframe: {signal.timeframe or 'N/A'}",
        f"Engine: {signal.engine}   Confidence: {signal.confidence:.1f}%",
        f"{risk_emoji} Risk: {signal.risk_level}",
    ]
    if signal.entry_zone:
        lines.append(f"Entry Zone: {signal.entry_zone[0]:.5f} – {signal.entry_zone[1]:.5f}")
    if signal.stop_loss is not None:
        lines.append(f"Stop Loss: {signal.stop_loss:.5f}")
    if signal.take_profit:
        tps = "  /  ".join(f"{tp:.5f}" for tp in signal.take_profit)
        lines.append(f"Take Profit: {tps}")
    if signal.risk_reward_ratio is not None:
        lines.append(f"Risk/Reward: 1:{signal.risk_reward_ratio:.1f}")
    lines += ["", f"{signal.explanation}", "", f"Generated: {signal.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"]
    return "\n".join(lines)


def _fmt_hold(signal: Signal) -> str:
    tf_label = {"15m": "15 Minute", "1h": "1 Hour", "4h": "4 Hour"}.get(
        signal.timeframe or "", signal.timeframe or "N/A"
    )
    return (
        f"⚪ HOLD — {signal.asset}\n"
        f"{tf_label} signal · {signal.generated_at.strftime('%H:%M UTC')}\n"
        f"Confidence: {signal.confidence:.0f}%\n"
        f"\n"
        f"No trade recommended right now — signals are mixed. "
        f"No entry, stop loss, or take profit is being tracked for this call.\n"
        f"\n"
        f"📖 Raina's Read\n"
        f"{signal.explanation}"
    )


# ── Auth guard ─────────────────────────────────────────────────────────────────

async def _require_auth(update: Update) -> dict | None:
    """Return user record if authenticated and subscribed, else reply and return None."""
    from app.storage.user_repo import get_user, touch_user
    tid = update.effective_user.id
    user = await get_user(tid)
    if not user or not user.get("email"):
        await update.message.reply_text(
            "Please login or create an account first.\nSend /start to begin."
        )
        return None
    if not user.get("is_active") or user.get("subscription", "none") == "none":
        await update.message.reply_text(
            "Your account is not subscribed.\n"
            "Visit RainX to activate your plan and return here."
        )
        return None
    await touch_user(tid)
    return user


# ── Command handlers ───────────────────────────────────────────────────────────

async def _cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from app.storage.user_repo import get_user
    tid = update.effective_user.id
    user = await get_user(tid)
    if not user or not user.get("email"):
        await update.message.reply_text("Send /start to login or create an account.")
        return
    sub = user.get("subscription", "none")
    active = bool(user.get("is_active"))
    tier_line = "⛔ No active subscription — visit RainX to subscribe." if not active else (
        "💎 Premium — Long-term signals + MT5 auto-trading" if sub == "premium"
        else "📊 Standard — Long-term signals (M15 and H1-H4)"
    )
    await update.message.reply_text(
        f"Raina AI — Main Menu\n\n"
        f"Account: {user.get('email')}\n"
        f"Plan: {tier_line}\n\n"
        "Commands:\n"
        "/signal SYMBOL — on-demand signal (e.g. /signal EURUSD)\n"
        "/scan — scan all markets now\n"
        "/symbols — list tracked markets\n"
        "/status — your account details\n\n"
        + ("💎 *MT5 Auto-Trading (Premium)*\n"
           "/mt5 — MT5 dashboard\n"
           "/mt5connect — connect your MT5 account\n"
           "/scalping — toggle auto-scalping on/off\n"
           "/risk — set risk per trade\n"
           "/trades — open positions\n"
           "/history — closed trades\n"
           "/performance — win rate & P/L\n" if sub == "premium" and active else "")
    )


async def _cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from app.storage.user_repo import get_user
    tid = update.effective_user.id
    user = await get_user(tid)
    if not user:
        await update.message.reply_text("Not logged in. Send /start.")
        return
    sub = user.get("subscription", "none")
    active = bool(user.get("is_active"))
    await update.message.reply_text(
        f"Account: {user.get('email') or 'N/A'}\n"
        f"Subscription: {sub}\n"
        f"Active: {'Yes' if active else 'No'}\n"
        f"Last seen: {user.get('last_seen', 'N/A')}"
    )


async def _cmd_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_auth(update):
        return
    if _provider is None:
        await update.message.reply_text("Engine not ready yet.")
        return
    syms = await _provider.get_available_symbols()
    await update.message.reply_text("Tracked markets:\n" + "  ".join(syms))


async def _cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from app.config import settings
    from app.engines import long_term_engine
    from app.storage.signal_repo import save_signal

    user = await _require_auth(update)
    if not user:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /signal SYMBOL  e.g. /signal EURUSD")
        return
    symbol = ctx.args[0].upper()
    timeframes = [ctx.args[1].upper()] if len(ctx.args) > 1 else ["15m", "1h"]
    await update.message.reply_text(f"Analysing {symbol} on {' & '.join(timeframes)}...")
    try:
        any_sent = False
        for tf in timeframes:
            sig = await long_term_engine.generate_signal(_provider, symbol, tf)
            await save_signal(sig, sent_telegram=True)
            if sig.confidence < settings.min_signal_confidence:
                await update.message.reply_text(_fmt_hold(sig))
            else:
                await update.message.reply_text(_fmt(sig))
                any_sent = True
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from app.config import settings
    from app.scanner import multi_market_scanner
    from app.storage.signal_repo import save_signal

    user = await _require_auth(update)
    if not user:
        return
    tf = "1h"
    await update.message.reply_text(f"Scanning all markets ({tf})...")
    try:
        signals = await multi_market_scanner.scan(
            _provider, settings.default_watchlist,
            engine="long_term", timeframe=tf, only_actionable=False,
        )
        strong = [s for s in signals if s.confidence >= settings.min_signal_confidence
                  and s.direction.value != "HOLD"]
        for s in strong:
            await save_signal(s, sent_telegram=True)
            await update.message.reply_text(_fmt(s))
        if not strong:
            await update.message.reply_text(
                "⚪ HOLD — No markets are showing 65%+ confidence right now.\n"
                "The engine is watching. Strong signals will be pushed when they appear."
            )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# ── Auto-push (called by background scanner) ───────────────────────────────────

async def push_signal_to_subscribers(signal: Signal) -> int:
    """
    Push a signal to all active standard+ subscribers.
    Returns the number of messages sent.
    """
    if _app is None:
        return 0
    from app.storage.user_repo import get_active_subscribers
    tids = await get_active_subscribers(tier="standard")
    text = _fmt(signal)
    sent = 0
    for tid in tids:
        try:
            await _app.bot.send_message(chat_id=tid, text=text)
            sent += 1
        except Exception as e:
            logger.warning(f"Could not send to {tid}: {e}")
    return sent


async def push_text_to_subscribers(text: str, tier: str = "standard") -> int:
    """Push a plain text message to all active subscribers."""
    if _app is None:
        return 0
    from app.storage.user_repo import get_active_subscribers
    tids = await get_active_subscribers(tier=tier)
    sent = 0
    for tid in tids:
        try:
            await _app.bot.send_message(chat_id=tid, text=text, parse_mode="Markdown")
            sent += 1
        except Exception as e:
            logger.warning(f"push_text failed for {tid}: {e}")
    return sent


async def push_signal(signal: Signal) -> None:
    """Legacy single-chat push (admin/testing)."""
    if _app is None:
        return
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return
    try:
        await _app.bot.send_message(chat_id=chat_id, text=_fmt(signal))
    except Exception as e:
        logger.error(f"Telegram push failed: {e}")


# ── Error handler & watchdog ───────────────────────────────────────────────────

async def _error_handler(update: object, context) -> None:
    """Log all telegram errors without crashing the polling loop."""
    logger.error(f"Telegram error: {context.error}", exc_info=context.error)


async def _polling_watchdog() -> None:
    """
    Runs every 30 s. If the updater has stopped polling (network drop,
    Telegram API hiccup, etc.) it restarts it automatically so the bot
    never goes silent without a full process restart.
    """
    import asyncio
    while True:
        await asyncio.sleep(30)
        if _app is None:
            continue
        try:
            if not _app.updater.running:
                logger.warning("Watchdog: polling stopped — restarting...")
                print("⚠️  Watchdog: polling stopped. Restarting...", flush=True)
                await _app.updater.start_polling(drop_pending_updates=False)
                print("✅ Watchdog: polling restored.", flush=True)
        except Exception as e:
            logger.error(f"Watchdog restart failed: {e}")


# ── Bot lifecycle ──────────────────────────────────────────────────────────────

async def start_bot(provider: DataProvider) -> None:
    import asyncio
    global _app, _provider
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        return

    _provider = provider
    _app = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Global error handler — catches all handler exceptions, keeps polling alive
    _app.add_error_handler(_error_handler)

    # Auth conversation handler (covers /start)
    from app.telegram.auth_flow import build_auth_conversation
    _app.add_handler(build_auth_conversation())

    # MT5 conversation handlers (must be added before plain CommandHandlers)
    from app.telegram.mt5_flow import (
        build_mt5_connect_conversation, build_risk_conversation,
        cmd_mt5, cmd_mt5status, cmd_scalping,
        cmd_trades, cmd_history, cmd_performance,
        _handle_callback,
    )
    from telegram.ext import CallbackQueryHandler as CQH
    _app.add_handler(build_mt5_connect_conversation())
    _app.add_handler(build_risk_conversation())

    # Authenticated commands
    _app.add_handler(CommandHandler("menu",        _cmd_menu))
    _app.add_handler(CommandHandler("status",      _cmd_status))
    _app.add_handler(CommandHandler("symbols",     _cmd_symbols))
    _app.add_handler(CommandHandler("signal",      _cmd_signal))
    _app.add_handler(CommandHandler("scan",        _cmd_scan))
    # MT5 commands
    _app.add_handler(CommandHandler("mt5",         cmd_mt5))
    _app.add_handler(CommandHandler("mt5status",   cmd_mt5status))
    _app.add_handler(CommandHandler("scalping",    cmd_scalping))
    _app.add_handler(CommandHandler("trades",      cmd_trades))
    _app.add_handler(CommandHandler("history",     cmd_history))
    _app.add_handler(CommandHandler("performance", cmd_performance))
    _app.add_handler(CQH(_handle_callback, pattern="^mt5_"))

    # Plain-text chatbot handler (must be last — lowest priority)
    from app.telegram.chat_handler import handle_text
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    print("✅ Raina AI Telegram bot is live and polling.", flush=True)

    # Launch watchdog in the background
    asyncio.create_task(_polling_watchdog())

    # Send startup notice to admin chat
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if chat_id:
        try:
            await _app.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🤖 Raina AI is online!\n\n"
                    "Auth-gated bot active. Users must login via /start.\n"
                    "Signals fire at 65%+ confidence only.\n"
                    "Watching: M15 and H1-H4 timeframes."
                ),
            )
            print(f"✅ Startup message sent to admin chat {chat_id}", flush=True)
        except Exception as e:
            print(f"⚠️ Could not send startup message: {e}", flush=True)


async def stop_bot() -> None:
    global _app
    if _app:
        try:
            await _app.updater.stop()
            await _app.stop()
            await _app.shutdown()
        except Exception as e:
            logger.warning(f"Error during bot shutdown: {e}")
        _app = None
