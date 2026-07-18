"""
MT5 Telegram commands — dual mode.
  Mobile users  → MetaAPI cloud (no EA needed)
  Desktop users → MT5 EA (classic, runs on user's PC)

Commands:
  /mt5         — dashboard
  /mt5connect  — connect (asks mode then method)
  /mt5status   — connection status + live balance
  /scalping    — toggle auto-scalping
  /risk        — set risk %
  /trades      — open positions
  /history     — closed trades
  /performance — stats
"""
import logging, os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)
from app.storage import mt5_repo

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
CHOOSE_MODE   = 0   # demo / real
CHOOSE_METHOD = 1   # mobile (MetaAPI) / desktop (EA)
ASK_SERVER    = 2
ASK_LOGIN     = 3
ASK_PASSWORD  = 4
SET_RISK      = 10
SET_MAX_TRADES = 11


async def _require_premium(update: Update) -> dict | None:
    from app.storage.user_repo import get_user
    tid = update.effective_user.id
    user = await get_user(tid)
    if not user or not user.get("email"):
        await update.message.reply_text("Please /start and log in first.")
        return None
    if user.get("subscription") != "premium" or not user.get("is_active"):
        await update.message.reply_text(
            "⚠️ MT5 auto-trading is a *Premium* feature.\nUpgrade on RainX to unlock it.",
            parse_mode="Markdown",
        )
        return None
    return user


def _status_text(account: dict | None, settings: dict) -> str:
    if not account:
        return "❌ No MT5 account connected.\nUse /mt5connect to get started."
    connected = "🟢 Connected" if account.get("is_connected") else "🔴 Disconnected"
    mode   = account.get("account_mode", "demo").upper()
    method = "📱 MetaAPI (Mobile)" if account.get("metaapi_id") else "🖥 EA (Desktop)"
    broker = account.get("broker_name") or "—"
    acc    = account.get("account_number") or "—"
    bal    = f"${account['balance']:.2f}" if account.get("balance") else "—"
    eq     = f"${account['equity']:.2f}"  if account.get("equity")  else "—"
    scalp  = "✅ ON" if settings.get("scalping_enabled") else "⛔ OFF"
    return (
        f"*MT5 Account*\n\n"
        f"Status: {connected} | {method}\n"
        f"Mode: `{mode}` | Broker: `{broker}`\n"
        f"Account: `{acc}`\n"
        f"Balance: `{bal}` | Equity: `{eq}`\n\n"
        f"Auto-Scalping: {scalp}\n"
        f"Risk/trade: `{settings.get('risk_percent',1.0)}%` | "
        f"Max trades: `{settings.get('max_open_trades',3)}`"
    )


# ── /mt5 dashboard ────────────────────────────────────────────────────────────

async def cmd_mt5(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_premium(update)
    if not user: return
    tid = update.effective_user.id
    account  = await mt5_repo.get_mt5_account(tid)
    settings = await mt5_repo.get_settings(tid)
    kb = [
        [InlineKeyboardButton("🔗 Connect Account", callback_data="mt5_connect"),
         InlineKeyboardButton("🔄 Refresh", callback_data="mt5_refresh")],
        [InlineKeyboardButton("⚡ Toggle Scalping", callback_data="mt5_toggle"),
         InlineKeyboardButton("⚙️ Risk Settings", callback_data="mt5_risk")],
        [InlineKeyboardButton("📊 Open Trades", callback_data="mt5_trades"),
         InlineKeyboardButton("📈 Performance", callback_data="mt5_perf")],
    ]
    await update.message.reply_text(_status_text(account, settings),
                                    parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(kb))


# ── /mt5connect conversation ──────────────────────────────────────────────────

async def cmd_mt5connect(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await _require_premium(update)
    if not user: return ConversationHandler.END
    kb = [[InlineKeyboardButton("🧪 Demo", callback_data="mode_demo"),
           InlineKeyboardButton("💰 Real", callback_data="mode_real")]]
    await update.message.reply_text(
        "*Connect your MT5 account*\n\nChoose account type:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSE_MODE


async def _chose_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ctx.user_data["mode"] = "demo" if query.data == "mode_demo" else "real"
    kb = [
        [InlineKeyboardButton("📱 Mobile (MetaAPI)", callback_data="method_metaapi")],
        [InlineKeyboardButton("🖥 Desktop PC (EA)", callback_data="method_ea")],
    ]
    await query.edit_message_text(
        "*How will you run it?*\n\n"
        "📱 *Mobile* — RainaAI trades your account from the cloud. No setup needed.\n\n"
        "🖥 *Desktop* — Run the EA on your Windows MT5. Trades execute locally.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSE_METHOD


async def _chose_method(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    method = "metaapi" if query.data == "method_metaapi" else "ea"
    ctx.user_data["method"] = method

    if method == "ea":
        # Provision API key and send EA file immediately
        tid = query.from_user.id
        mode = ctx.user_data.get("mode", "demo")
        api_key = await mt5_repo.upsert_mt5_account(tid, mode)
        await mt5_repo.set_ea_mode(tid)

        await query.edit_message_text(
            f"✅ *MT5 {mode.upper()} — Desktop EA Mode*\n\n"
            f"Your API Key:\n`{api_key}`\n\n"
            f"Sending the EA file now...",
            parse_mode="Markdown",
        )
        ea_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "RainaAI_EA.mq5")
        )
        try:
            with open(ea_path, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename="RainaAI_EA.mq5",
                    caption=(
                        f"*RainaAI EA — Desktop Installation*\n\n"
                        f"1. Copy file to: MT5 → *File → Open Data Folder → MQL5 → Experts*\n"
                        f"2. Restart MT5 → drag *RainaAI EA* onto any chart\n"
                        f"3. Enter API Key: `{api_key}`\n"
                        f"4. Enable *Auto Trading* button in MT5\n\n"
                        f"⚠️ MT5 must stay open on your PC for trades to execute.\n"
                        f"Use /mt5status to check connection."
                    ),
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"EA file send error: {e}")
            await query.message.reply_text("Could not send EA file. Contact support.")
        return ConversationHandler.END

    # MetaAPI path — ask for credentials
    await query.edit_message_text(
        "Enter your MT5 *broker server name*:\n"
        "_(e.g. `Exness-MT5Trial10`, `ICMarkets-Demo01`, `XMGlobal-MT5Demo`)_\n\n"
        "Find it in MT5 → File → Open Account → server name.",
        parse_mode="Markdown",
    )
    return ASK_SERVER


async def _got_server(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["server"] = update.message.text.strip()
    await update.message.reply_text("Enter your MT5 *account number* (login):",
                                    parse_mode="Markdown")
    return ASK_LOGIN


async def _got_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["login"] = update.message.text.strip()
    await update.message.reply_text(
        "Enter your MT5 *password*:\n_(deleted from chat immediately after)_",
        parse_mode="Markdown",
    )
    return ASK_PASSWORD


async def _got_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text.strip()
    tid    = update.effective_user.id
    mode   = ctx.user_data.get("mode", "demo")
    server = ctx.user_data.get("server", "")
    login  = ctx.user_data.get("login", "")
    try:
        await update.message.delete()
    except Exception:
        pass

    msg = await update.effective_chat.send_message("⏳ Connecting via MetaAPI...")
    try:
        from app.mt5.metaapi_client import provision_account
        metaapi_id = await provision_account(
            mt5_login=login, mt5_password=password,
            mt5_server=server, account_mode=mode,
            name=f"RainaAI-{tid}",
        )
        await mt5_repo.upsert_mt5_account_full(
            telegram_id=tid, account_mode=mode,
            metaapi_id=metaapi_id, account_number=login, broker_name=server,
        )
        await msg.edit_text(
            f"✅ *MT5 {mode.upper()} Connected via MetaAPI!*\n\n"
            f"Broker: `{server}` | Account: `{login}`\n\n"
            f"RainaAI trades your account automatically when scalping is ON.\n"
            f"Use /scalping to enable.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"MetaAPI provision failed: {e}")
        await msg.edit_text(
            f"❌ MetaAPI connection failed.\n\n"
            f"`{e}`\n\n"
            f"Note: MetaAPI requires account credits at *metaapi.cloud*.\n"
            f"Alternatively use /mt5connect and choose *Desktop EA* — it's free.",
            parse_mode="Markdown",
        )
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── /mt5status ────────────────────────────────────────────────────────────────

async def cmd_mt5status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_premium(update)
    if not user: return
    tid = update.effective_user.id
    account  = await mt5_repo.get_mt5_account(tid)
    settings = await mt5_repo.get_settings(tid)
    if account and account.get("metaapi_id"):
        try:
            from app.mt5.metaapi_client import get_account_info
            info = await get_account_info(account["metaapi_id"])
            if info.get("connected"):
                await mt5_repo.update_heartbeat_meta(
                    account["metaapi_id"],
                    info.get("broker"), account.get("account_number"),
                    info.get("balance"), info.get("equity"),
                    account.get("account_mode", "demo"),
                )
                account = await mt5_repo.get_mt5_account(tid)
        except Exception:
            pass
    await update.message.reply_text(_status_text(account, settings), parse_mode="Markdown")


# ── /scalping ─────────────────────────────────────────────────────────────────

async def cmd_scalping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_premium(update)
    if not user: return
    tid = update.effective_user.id
    account = await mt5_repo.get_mt5_account(tid)
    if not account:
        await update.message.reply_text("Connect your MT5 account first with /mt5connect")
        return
    settings = await mt5_repo.get_settings(tid)
    settings["scalping_enabled"] = not settings.get("scalping_enabled", False)
    await mt5_repo.upsert_settings(tid, settings)
    status = "✅ *Enabled*" if settings["scalping_enabled"] else "⛔ *Disabled*"
    await update.message.reply_text(
        f"Auto-Scalping: {status}\n"
        + ("RainaAI will now trade your MT5 automatically." if settings["scalping_enabled"]
           else "Auto-trading paused."),
        parse_mode="Markdown",
    )


# ── /risk ─────────────────────────────────────────────────────────────────────

async def cmd_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await _require_premium(update)
    if not user: return ConversationHandler.END
    settings = await mt5_repo.get_settings(update.effective_user.id)
    await update.message.reply_text(
        f"Current risk: `{settings.get('risk_percent',1.0)}%` per trade\n\nEnter new risk % (0.1–10):",
        parse_mode="Markdown",
    )
    return SET_RISK


async def _set_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace("%", ""))
        assert 0.1 <= val <= 10
    except Exception:
        await update.message.reply_text("Invalid. Enter 0.1–10:"); return SET_RISK
    ctx.user_data["risk_percent"] = val
    await update.message.reply_text("Max open trades at once (1–20):")
    return SET_MAX_TRADES


async def _set_max_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text.strip()); assert 1 <= val <= 20
    except Exception:
        await update.message.reply_text("Invalid. Enter 1–20:"); return SET_MAX_TRADES
    tid = update.effective_user.id
    settings = await mt5_repo.get_settings(tid)
    settings["risk_percent"]    = ctx.user_data.get("risk_percent", settings.get("risk_percent", 1.0))
    settings["max_open_trades"] = val
    await mt5_repo.upsert_settings(tid, settings)
    await update.message.reply_text(
        f"✅ Risk: `{settings['risk_percent']}%` | Max trades: `{val}`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /trades /history /performance ─────────────────────────────────────────────

async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_premium(update)
    if not user: return
    trades = await mt5_repo.get_open_trades(update.effective_user.id)
    if not trades:
        await update.message.reply_text("No open trades right now."); return
    lines = ["*Open Trades*\n"]
    for t in trades:
        e = "🟢" if t["direction"] == "BUY" else "🔴"
        lines.append(f"{e} {t['asset']} {t['direction']} Lot:`{t['lot_size']}` Ticket:`{t.get('mt5_ticket','—')}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_premium(update)
    if not user: return
    history = await mt5_repo.get_trade_history(update.effective_user.id, limit=10)
    if not history:
        await update.message.reply_text("No closed trades yet."); return
    lines = ["*Last 10 Trades*\n"]
    for t in history:
        p = t.get("profit") or 0.0
        e = "✅" if p >= 0 else "🔴"
        lines.append(f"{e} {t['asset']} {t['direction']} `{'+' if p>=0 else ''}{p:.2f}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_performance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_premium(update)
    if not user: return
    perf = await mt5_repo.get_performance_summary(update.effective_user.id)
    p = perf["total_profit"]
    await update.message.reply_text(
        f"*Performance*\n\nWin rate: `{perf['win_rate']}%`\n"
        f"W/L: `{perf['wins']}/{perf['losses']}` | P/L: `{'+' if p>=0 else ''}{p}`",
        parse_mode="Markdown",
    )


# ── Inline callbacks ──────────────────────────────────────────────────────────

async def _handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tid  = query.from_user.id
    data = query.data

    if data == "mt5_connect":
        await query.edit_message_text("Use /mt5connect to connect your MT5 account.")
    elif data == "mt5_refresh":
        account  = await mt5_repo.get_mt5_account(tid)
        settings = await mt5_repo.get_settings(tid)
        await query.edit_message_text(_status_text(account, settings), parse_mode="Markdown")
    elif data == "mt5_toggle":
        account = await mt5_repo.get_mt5_account(tid)
        if not account:
            await query.edit_message_text(
                "❌ No MT5 account connected.\n\n"
                "You need to connect your MT5 account first before enabling auto-scalping.\n"
                "Use /mt5connect to get started."
            )
            return
        settings = await mt5_repo.get_settings(tid)
        settings["scalping_enabled"] = not settings.get("scalping_enabled", False)
        await mt5_repo.upsert_settings(tid, settings)
        enabled = settings["scalping_enabled"]
        status  = "✅ ON" if enabled else "⛔ OFF"
        extra   = "\n\n🤖 Raina AI will now execute trades on your MT5 automatically." if enabled else "\n\nAuto-trading paused. I'll still send you signals."
        await query.edit_message_text(f"Auto-Scalping: {status}{extra}")
    elif data == "mt5_trades":
        trades = await mt5_repo.get_open_trades(tid)
        if not trades:
            await query.edit_message_text("No open trades right now.")
        else:
            lines = ["*Open Trades*\n"]
            for t in trades:
                e = "🟢" if t["direction"] == "BUY" else "🔴"
                lines.append(f"{e} {t['asset']} {t['direction']} Lot:`{t['lot_size']}`")
            await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
    elif data == "mt5_perf":
        perf = await mt5_repo.get_performance_summary(tid)
        p = perf["total_profit"]
        await query.edit_message_text(
            f"*Performance*\nWin rate: `{perf['win_rate']}%`\nP/L: `{'+' if p>=0 else ''}{p}`",
            parse_mode="Markdown",
        )
    elif data == "mt5_risk":
        settings = await mt5_repo.get_settings(tid)
        await query.edit_message_text(
            f"Risk: `{settings.get('risk_percent',1.0)}%` | Max trades: `{settings.get('max_open_trades',3)}`\n\nUse /risk to change.",
            parse_mode="Markdown",
        )


# ── Conversation builders ─────────────────────────────────────────────────────

def build_mt5_connect_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("mt5connect", cmd_mt5connect)],
        states={
            CHOOSE_MODE:   [CallbackQueryHandler(_chose_mode,   pattern="^mode_")],
            CHOOSE_METHOD: [CallbackQueryHandler(_chose_method, pattern="^method_")],
            ASK_SERVER:    [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_server)],
            ASK_LOGIN:     [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_login)],
            ASK_PASSWORD:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_password)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )


def build_risk_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("risk", cmd_risk)],
        states={
            SET_RISK:       [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_risk)],
            SET_MAX_TRADES: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_max_trades)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
