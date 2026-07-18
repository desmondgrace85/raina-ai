"""
Authentication conversation flow for the Raina AI Telegram bot.

New users  → signup (name → email → password → calls RainX API)
Existing   → login  (email → password → calls RainX API)

On success the user is stored in telegram_users with the subscription
tier returned by RainX. If RAINX_API_URL is not configured the flow
still works but marks the user as 'none' subscription until the web
app confirms via the /webhook/subscription endpoint.
"""
import logging
import os
from typing import Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.storage.user_repo import get_user, upsert_user

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
MENU = 0
SIGNUP_NAME = 1
SIGNUP_EMAIL = 2
SIGNUP_PASSWORD = 3
LOGIN_EMAIL = 4
LOGIN_PASSWORD = 5


# ── RainX API helpers ──────────────────────────────────────────────────────────

def _rainx_url() -> str:
    return os.getenv("RAINX_API_URL", "").rstrip("/")


async def _rainx_signup(name: str, email: str, password: str, telegram_id: int) -> dict[str, Any]:
    base = _rainx_url()
    if not base:
        return {"ok": False, "reason": "rainx_not_configured"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{base}/api/auth/register", json={
                "name": name, "email": email, "password": password,
                "telegram_id": telegram_id,
            })
        data = r.json()
        return {"ok": r.status_code in (200, 201), "data": data}
    except Exception as e:
        logger.error(f"RainX signup error: {e}")
        return {"ok": False, "reason": str(e)}


async def _rainx_login(email: str, password: str, telegram_id: int) -> dict[str, Any]:
    base = _rainx_url()
    if not base:
        return {"ok": False, "reason": "rainx_not_configured"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{base}/api/auth/login", json={
                "email": email, "password": password,
                "telegram_id": telegram_id,
            })
        if r.status_code == 200:
            data = r.json()
            return {
                "ok": True,
                "token": data.get("token", ""),
                "subscription": data.get("subscription", "none"),
                "is_active": data.get("is_active", False),
                "name": data.get("name", ""),
            }
        return {"ok": False, "reason": "invalid_credentials"}
    except Exception as e:
        logger.error(f"RainX login error: {e}")
        return {"ok": False, "reason": str(e)}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sub_label(sub: str, active: bool) -> str:
    if not active or sub == "none":
        return "⛔ No active subscription"
    labels = {"standard": "📊 Standard — Long-term signals", "premium": "💎 Premium — MT5 auto-trading"}
    return labels.get(sub, sub)


# ── Handlers ───────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /start"""
    tid = update.effective_user.id
    user = await get_user(tid)

    if user and user.get("email"):
        # Already logged in — show status
        sub = user.get("subscription", "none")
        active = bool(user.get("is_active"))
        await update.message.reply_text(
            f"👋 Welcome back, {user.get('telegram_name') or 'trader'}!\n\n"
            f"Account: {user.get('email')}\n"
            f"Status: {_sub_label(sub, active)}\n\n"
            "Use /menu to see available commands.",
        )
        return ConversationHandler.END

    # Not logged in — show entry choice
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 I'm new — create account", callback_data="new")],
        [InlineKeyboardButton("✅ I have a RainX account", callback_data="login")],
    ])
    await update.message.reply_text(
        "👋 Welcome to *Raina AI* — your trading intelligence engine.\n\n"
        "Signals are available to RainX subscribers only.\n"
        "Are you new, or do you already have a RainX account?",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return MENU


async def menu_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == "new":
        await query.edit_message_text(
            "Let's create your RainX account.\n\n"
            "Step 1 of 3 — What is your full name?"
        )
        return SIGNUP_NAME

    if choice == "login":
        await query.edit_message_text(
            "Enter your RainX email address:"
        )
        return LOGIN_EMAIL

    return MENU


# ── Sign-up flow ───────────────────────────────────────────────────────────────

async def signup_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["signup_name"] = update.message.text.strip()
    await update.message.reply_text("Step 2 of 3 — Enter your email address:")
    return SIGNUP_EMAIL


async def signup_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip().lower()
    if "@" not in email or "." not in email:
        await update.message.reply_text("That doesn't look like a valid email. Try again:")
        return SIGNUP_EMAIL
    ctx.user_data["signup_email"] = email
    await update.message.reply_text(
        "Step 3 of 3 — Create a password:\n_(min 8 characters)_",
        parse_mode="Markdown",
    )
    return SIGNUP_PASSWORD


async def signup_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    pwd = update.message.text.strip()
    # Delete the message containing the password immediately
    try:
        await update.message.delete()
    except Exception:
        pass

    if len(pwd) < 8:
        await update.effective_chat.send_message("Password must be at least 8 characters. Try again:")
        return SIGNUP_PASSWORD

    name = ctx.user_data.get("signup_name", "")
    email = ctx.user_data.get("signup_email", "")
    tid = update.effective_user.id

    await update.effective_chat.send_message("⏳ Creating your account...")
    result = await _rainx_signup(name, email, pwd, tid)

    if result.get("ok"):
        await upsert_user(
            telegram_id=tid,
            telegram_name=name,
            email=email,
            subscription="none",
            is_active=False,
        )
        await update.effective_chat.send_message(
            "✅ Account created successfully!\n\n"
            "To receive signals, activate a subscription on the RainX website.\n"
            "Once active, signals will start arriving here automatically.",
        )
    elif result.get("reason") == "rainx_not_configured":
        # Store locally — RainX will sync later via webhook
        await upsert_user(telegram_id=tid, telegram_name=name, email=email)
        await update.effective_chat.send_message(
            "✅ Account registered! Your details have been saved.\n"
            "Visit RainX to complete setup and activate your subscription.",
        )
    else:
        data = result.get("data", {})
        msg = data.get("message") or data.get("detail") or "Registration failed. Please try again."
        await update.effective_chat.send_message(f"❌ {msg}")
        return ConversationHandler.END

    ctx.user_data.clear()
    return ConversationHandler.END


# ── Login flow ─────────────────────────────────────────────────────────────────

async def login_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip().lower()
    if "@" not in email:
        await update.message.reply_text("That doesn't look like an email. Try again:")
        return LOGIN_EMAIL
    ctx.user_data["login_email"] = email
    await update.message.reply_text("Enter your RainX password:")
    return LOGIN_PASSWORD


async def login_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    pwd = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    email = ctx.user_data.get("login_email", "")
    tid = update.effective_user.id
    tname = update.effective_user.first_name or ""

    await update.effective_chat.send_message("⏳ Verifying your account...")
    result = await _rainx_login(email, pwd, tid)

    if result.get("ok"):
        sub = result.get("subscription", "none")
        active = result.get("is_active", False)
        token = result.get("token", "")
        name = result.get("name") or tname

        await upsert_user(
            telegram_id=tid,
            telegram_name=name,
            email=email,
            subscription=sub,
            is_active=active,
            rainx_token=token,
        )
        status = _sub_label(sub, active)
        if active and sub != "none":
            await update.effective_chat.send_message(
                f"✅ Logged in successfully!\n\n"
                f"Status: {status}\n\n"
                f"Signals will be sent here automatically when strong setups appear.\n"
                f"Use /menu to explore commands.",
            )
        else:
            await update.effective_chat.send_message(
                f"✅ Logged in — but no active subscription found.\n\n"
                f"Visit RainX to activate your plan and start receiving signals.",
            )
    elif result.get("reason") == "rainx_not_configured":
        # Dev mode — store locally
        await upsert_user(telegram_id=tid, telegram_name=tname, email=email)
        await update.effective_chat.send_message(
            "✅ Details saved. (RainX API not connected yet — subscription sync pending.)"
        )
    else:
        await update.effective_chat.send_message(
            "❌ Incorrect email or password. Please try again with /start."
        )

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled. Send /start to begin again.")
    return ConversationHandler.END


# ── ConversationHandler factory ────────────────────────────────────────────────

def build_auth_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [CallbackQueryHandler(menu_choice, pattern="^(new|login)$")],
            SIGNUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, signup_name)],
            SIGNUP_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, signup_email)],
            SIGNUP_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, signup_password)],
            LOGIN_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_email)],
            LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300,
    )
