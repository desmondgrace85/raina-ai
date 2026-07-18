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


# ── Supabase direct helpers ────────────────────────────────────────────────────
# The bot talks to Supabase directly instead of going through the Vercel
# frontend. When SUPABASE_SERVICE_KEY is set it uses the Admin API
# (no confirmation email, no rate limit). Otherwise it falls back to the
# anon signup endpoint (subject to Supabase's 3/hr SMTP rate limit on free plans).

_SUPABASE_URL = "https://fsndqkacfizulovhfldz.supabase.co"
_SUPABASE_ANON_KEY = "sb_publishable_iRh4f9MF6ZDg43cSrA7zNQ_uIpi1eg9"


def _service_key() -> str:
    return os.getenv("SUPABASE_SERVICE_KEY", "").strip()


async def _rainx_signup(name: str, email: str, password: str, telegram_id: int) -> dict[str, Any]:
    sk = _service_key()
    try:
        if sk:
            # ── Admin path: pre-confirmed, no email sent, no rate limit ──────
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{_SUPABASE_URL}/auth/v1/admin/users",
                    headers={
                        "apikey": sk,
                        "Authorization": f"Bearer {sk}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "email": email,
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": {"name": name, "telegram_id": telegram_id},
                    },
                )
            body = r.json()
            if r.status_code in (200, 201):
                user_id = body.get("id")
                if user_id:
                    # Upsert profile row so the web app sees the telegram_id
                    async with httpx.AsyncClient(timeout=10) as pc:
                        await pc.post(
                            f"{_SUPABASE_URL}/rest/v1/profiles",
                            headers={
                                "apikey": sk,
                                "Authorization": f"Bearer {sk}",
                                "Content-Type": "application/json",
                                "Prefer": "resolution=merge-duplicates",
                            },
                            json={
                                "id": user_id, "name": name,
                                "telegram_id": telegram_id,
                                "subscription": "none", "is_active": False,
                            },
                        )
                return {"ok": True, "subscription": "none", "is_active": False, "name": name}
            err_msg = (body.get("msg") or body.get("message") or
                       body.get("error_description") or str(body))
            if "already registered" in err_msg.lower() or r.status_code == 422:
                return {"ok": False, "error": "already_exists"}
            return {"ok": False, "error": err_msg}

        else:
            # ── Anon path: regular signup (may hit SMTP rate limit) ──────────
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{_SUPABASE_URL}/auth/v1/signup",
                    headers={
                        "apikey": _SUPABASE_ANON_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "email": email,
                        "password": password,
                        "data": {"name": name, "telegram_id": telegram_id},
                    },
                )
            body = r.json()
            if r.status_code in (200, 201) and body.get("id"):
                return {"ok": True, "subscription": "none", "is_active": False, "name": name}
            err_msg = body.get("msg") or body.get("message") or body.get("error") or "Signup failed"
            return {"ok": False, "error": err_msg}

    except Exception as e:
        logger.error(f"Supabase signup error: {e}")
        return {"ok": False, "error": str(e)}


async def _rainx_login(email: str, password: str, telegram_id: int) -> dict[str, Any]:
    sk = _service_key()
    try:
        # Sign in with password via Supabase token endpoint (works with anon key)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{_SUPABASE_URL}/auth/v1/token?grant_type=password",
                headers={
                    "apikey": _SUPABASE_ANON_KEY,
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
            )
        if r.status_code != 200:
            body = r.json()
            err = body.get("error_description") or body.get("msg") or "Invalid email or password"
            return {"ok": False, "error": err}

        session = r.json()
        access_token = session.get("access_token", "")
        user_id = session.get("user", {}).get("id")

        # Fetch profile for subscription info
        profile = {}
        hdr = {"apikey": sk or _SUPABASE_ANON_KEY,
               "Authorization": f"Bearer {access_token}",
               "Content-Type": "application/json"}
        if user_id:
            async with httpx.AsyncClient(timeout=10) as client:
                pr = await client.get(
                    f"{_SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=*",
                    headers=hdr,
                )
            if pr.status_code == 200 and pr.json():
                profile = pr.json()[0]
                # Update telegram_id in profile
                if sk:
                    async with httpx.AsyncClient(timeout=10) as pc:
                        await pc.patch(
                            f"{_SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                            headers={**hdr, "Authorization": f"Bearer {sk}",
                                     "Prefer": "return=minimal"},
                            json={"telegram_id": telegram_id},
                        )

        return {
            "ok": True,
            "token": access_token,
            "subscription": profile.get("subscription", "none"),
            "is_active": bool(profile.get("is_active", False)),
            "name": profile.get("name") or profile.get("username") or "",
        }

    except Exception as e:
        logger.error(f"Supabase login error: {e}")
        return {"ok": False, "error": str(e)}


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
    else:
        # This branch is no longer reached (signup goes direct to Supabase)
        # but kept as a safety fallback
        await update.effective_chat.send_message(
            "✅ Account registered! Your details have been saved.\n"
            "Visit RainX to complete setup and activate your subscription.",
        )
    else:
        err = result.get("error", "")
        if err == "already_exists":
            msg = "An account with that email already exists.\n\nUse /start and choose 'Log in' instead."
        elif "rate limit" in err.lower():
            msg = "⏳ Too many signup attempts right now. Please try again in a few minutes, or sign up at https://rainx-webapp.vercel.app and then log in here."
        else:
            msg = err or "Registration failed. Please try again."
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
