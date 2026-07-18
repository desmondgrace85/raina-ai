"""
Raina AI — Deep Conversational Chat Handler

Handles all plain-text messages and answers like a trading-focused AI assistant:
  • Greetings / identity / general questions → always answered
  • Economic calendar questions (when is CPI?) → answered for all logged-in users
  • Signal analysis / buy-sell opinion → paid subscribers only (standard / biannual / premium)
  • Questions outside trading → answered helpfully but briefly
  • Bot creator question → answered with proper attribution
"""
import logging
import re
import random
from datetime import datetime, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from app.storage.user_repo import get_user

logger = logging.getLogger(__name__)

# ── Subscription tier helpers ──────────────────────────────────────────────────
_PAID_TIERS = {"standard", "monthly", "biannual", "premium"}


def _is_paid(user: Optional[dict]) -> bool:
    return bool(user and user.get("subscription") in _PAID_TIERS and user.get("is_active"))


def _is_premium(user: Optional[dict]) -> bool:
    return bool(user and user.get("subscription") == "premium" and user.get("is_active"))


# ── Intent detection ───────────────────────────────────────────────────────────

_GREETING_WORDS = {
    "hi", "hello", "hey", "hiya", "sup", "yo", "good morning",
    "good afternoon", "good evening", "morning", "afternoon", "howdy",
}

_CREATOR_PATTERNS = [
    r"who (made|built|created|developed|programmed|coded) you",
    r"who (is your|is the) (creator|developer|author|owner|ceo|boss|founder)",
    r"(your|the) (creator|developer|owner|team|company)",
    r"who are you (made|created|built|developed) by",
    r"who owns you",
    r"who (is behind|runs) raina",
    r"tell me about (your|the) team",
]

_CALENDAR_PATTERNS = [
    r"when is (the )?(cpi|nfp|fomc|gdp|ppi|ism|pce|fed|interest rate|inflation|jobs|employment|payroll)",
    r"(cpi|nfp|fomc|gdp|ppi|ism|pce|inflation|payroll|employment) (news|data|release|report|date|time|today|this week)",
    r"what (time|day|date) (is|does) (cpi|nfp|fomc|gdp|ppi|payroll)",
    r"(upcoming|next|today'?s|this week'?s) (economic|news|market|calendar|events?)",
    r"any (big|major|important|key) (news|events?|releases?) (today|this week)",
    r"economic calendar",
]

_ANALYSIS_PATTERNS = [
    r"(should i|can i|do you recommend) (buy|sell|trade|go long|go short)",
    r"(buy|sell|long|short|hold) (or|vs\.?) (sell|buy|hold)",
    r"(what do you think|your opinion|your view|your take) (about|on) (\w+)",
    r"(cpi|nfp|fomc|gdp|inflation|news) (impact|affect|effect|move|push|pull).*(eur|usd|gbp|jpy|gold|xau|btc|crypto)",
    r"(is it|is now) (a good time|good|safe|risky) to (buy|sell|trade|enter)",
    r"(signal|analysis|outlook|forecast|prediction|view) (for|on) (\w+)",
    r"(where is|where will|where do you see) (eurusd|gbpusd|usdjpy|xauusd|gold|btc|bitcoin|ethereum|eth)",
    r"(bullish|bearish) on (\w+)",
    r"(market|price) (direction|trend|move|movement|going)",
    r"(support|resistance|target|stop loss|take profit) (for|on|level)",
    r"(how will|how does|what does) (cpi|nfp|fomc|fed|inflation|rate|jobs|payroll).*(affect|impact|move|do to)",
    r"(rate hike|rate cut|hawkish|dovish|pivot) (affect|impact|mean for|do to)",
]

_GENERAL_MARKET_WORDS = {
    "signal", "signals", "scan", "forex", "gold", "crypto", "bitcoin",
    "eurusd", "gbpusd", "usdjpy", "xauusd", "btcusd", "ethusd",
    "scalping", "scalp", "trade", "trading",
}

_OUTSIDE_TRADING_PATTERNS = [
    r"(weather|temperature|climate)",
    r"(recipe|food|cook|restaurant|eat)",
    r"(movie|film|series|tv show|netflix)",
    r"(song|music|artist|album|playlist)",
    r"(sport|football|soccer|basketball|cricket|tennis)",
    r"(joke|funny|humor|laugh|meme)",
    r"(love|relationship|dating|romance|marriage)",
    r"(health|doctor|medicine|symptom|disease)",
    r"(politics|election|president|government|prime minister)",
    r"(science|physics|chemistry|biology|space|nasa)",
    r"(history|historical|ancient|war|world war)",
    r"(travel|vacation|holiday|flight|hotel)",
]


def _match(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in patterns)


def _is_greeting(text: str) -> bool:
    t = text.lower().strip().rstrip("!?.")
    return t in _GREETING_WORDS or any(t.startswith(g + " ") for g in _GREETING_WORDS)


def _extract_asset_from_analysis(text: str) -> Optional[str]:
    """Try to extract a ticker the user is asking about."""
    aliases = {
        "gold": "XAUUSD", "xauusd": "XAUUSD",
        "eurusd": "EURUSD", "eur/usd": "EURUSD", "eur usd": "EURUSD",
        "gbpusd": "GBPUSD", "gbp/usd": "GBPUSD", "gbp usd": "GBPUSD",
        "usdjpy": "USDJPY", "usd/jpy": "USDJPY",
        "bitcoin": "BTCUSD", "btc": "BTCUSD", "btcusd": "BTCUSD",
        "ethereum": "ETHUSD", "eth": "ETHUSD", "ethusd": "ETHUSD",
        "oil": "WTICOUSD", "crude": "WTICOUSD",
    }
    t = text.lower()
    for key, ticker in aliases.items():
        if key in t:
            return ticker
    return None


# ── Response builders ──────────────────────────────────────────────────────────

_CREATOR_RESPONSE = (
    "🤖 *About Raina AI*\n\n"
    "I was built by a talented team of developers and AI engineers — too many brilliant minds to name individually! "
    "What I can tell you is that the entire project is led by our visionary CEO, *Mr. Banful Desmond*.\n\n"
    "Under his leadership, the team combined cutting-edge AI tools, market analysis expertise, and software engineering "
    "to create me — your intelligent 24/5 trading assistant. 🙌\n\n"
    "Got a trading question? I'm here for it!"
)

_UPGRADE_MSG = (
    "🔒 *Signal analysis is for paid subscribers only.*\n\n"
    "Free users can see auto-pushed signals, but deep analysis (buy/sell opinion, "
    "news impact forecasting, market outlook) is reserved for *Monthly*, *Biannual*, and *Premium* members.\n\n"
    "Upgrade on RainX to unlock full analysis + MT5 auto-trading. 🚀"
)

_COMMANDS_BASE = (
    "📋 *What Raina AI can do:*\n\n"
    "🔍 /signal EURUSD — get a signal for any pair\n"
    "📡 /scan — scan all markets right now\n"
    "📊 /symbols — full watchlist\n"
    "👤 /status — your account\n"
)

_COMMANDS_PREMIUM = (
    "\n💎 *MT5 Auto-Trading (Premium):*\n"
    "🔗 /mt5connect — connect your MT5\n"
    "⚡ /scalping — toggle auto-scalping\n"
    "📈 /trades — open positions\n"
    "📉 /history — closed trades\n"
    "🏆 /performance — stats & win rate\n"
)


async def _fetch_calendar_answer(query_text: str) -> str:
    """Fetch today's events and format a human answer."""
    try:
        from app.scanner.news_scanner import get_todays_events
        events = await get_todays_events()
    except Exception as e:
        logger.warning(f"[chat] calendar fetch error: {e}")
        return (
            "📅 I couldn't load the economic calendar right now, but you can check "
            "ForexFactory (https://www.forexfactory.com/calendar) for today's events.\n\n"
            "I'll push alerts to you automatically when high-impact news releases."
        )

    if not events:
        return (
            "📅 No major economic events appear on the calendar for today.\n\n"
            "Markets may be quieter — that can be a good time for range-based setups. "
            "Use /scan to check live setups."
        )

    # Filter by keyword if user asked about a specific event
    keywords = re.findall(r"\b(cpi|nfp|fomc|gdp|ppi|ism|pce|inflation|payroll|employment|fed|interest rate)\b",
                          query_text.lower())
    filtered = events
    if keywords:
        filtered = [e for e in events if any(k in e.get("title", "").lower() for k in keywords)]
        if not filtered:
            filtered = events  # fall back to all events

    lines = ["📅 *Economic Calendar — Today*\n"]
    for ev in filtered[:8]:
        title    = ev.get("title", "Event")
        currency = ev.get("currency", "")
        impact   = ev.get("impact", "").lower()
        forecast = ev.get("forecast") or "—"
        actual   = ev.get("actual")
        time_str = ev.get("time") or ev.get("date") or ""

        icon = "🔴" if impact == "high" else "🟡" if impact == "medium" else "⚪"

        if actual:
            try:
                a = float(str(actual).replace("%", "").replace("K", "").strip())
                f = float(str(forecast).replace("%", "").replace("K", "").strip())
                beat = "🟢 Beat" if a > f else "🔴 Miss"
            except Exception:
                beat = "Released"
            lines.append(f"{icon} *{title}* ({currency}) — Actual: `{actual}` | Forecast: `{forecast}` | {beat}")
        else:
            t = f" at `{time_str}`" if time_str else ""
            lines.append(f"{icon} *{title}* ({currency}){t} — Forecast: `{forecast}`")

    lines.append("\n💡 _Red = high impact. These events can cause sharp moves — trade carefully around them._")
    return "\n".join(lines)


async def _fetch_signal_analysis(asset: str) -> str:
    """Run the long-term engine on a specific asset and return a conversational answer."""
    try:
        from app.scanner import multi_market_scanner
        from app.api.routes import get_provider

        provider = get_provider()
        signals = await multi_market_scanner.scan(
            provider, [asset], engine="long_term",
            timeframe="1h", only_actionable=False,
        )
        if not signals:
            return f"⚠️ I couldn't get a read on {asset} right now. Try again in a minute or use /signal {asset}."

        s = signals[0]
        direction = s.direction.value  # BUY / SELL / HOLD
        conf      = s.confidence
        explanation = s.explanation or ""

        icon = "🟢" if direction == "BUY" else "🔴" if direction == "SELL" else "⚪"

        # Try AI-enhanced commentary first
        try:
            from app.analysis.ai_enhancer import ai_chat_response
            ai_reply = await ai_chat_response(
                user_message=f"Give me your analysis on {asset}",
                symbol=asset,
                signal_context=(
                    f"Signal: {direction} | Confidence: {conf:.0f}%\n"
                    f"Analysis: {explanation}"
                ),
                user_name="trader",
            )
            if ai_reply:
                return ai_reply
        except Exception:
            pass

        # Fallback structured reply
        opinion = (
            f"Based on my current analysis of *{asset}*:\n\n"
            f"{icon} Direction: *{direction}*\n"
            f"📊 Confidence: `{conf:.0f}%`\n"
        )
        if explanation:
            opinion += f"\n📝 {explanation[:300]}\n"

        if direction == "HOLD":
            opinion += (
                "\n⚠️ The market is not giving a clear edge right now. "
                "I'd wait for a stronger setup before entering. Patience is a strategy too."
            )
        elif conf >= 75:
            opinion += (
                f"\n✅ This is a *strong* {direction} setup. "
                "Risk management still applies — don't over-leverage."
            )
        else:
            opinion += (
                "\n⚠️ Confidence is moderate. Consider waiting for a cleaner entry "
                "or reducing position size."
            )

        opinion += f"\n\n_Use /signal {asset} for the full breakdown with TP/SL levels._"
        return opinion

    except Exception as e:
        logger.warning(f"[chat] signal analysis error for {asset}: {e}")
        return (
            f"I ran into an issue analysing {asset} right now.\n"
            f"Try /signal {asset} for a full on-demand signal."
        )


async def _answer_news_impact(text: str) -> str:
    """Answer questions like 'how will CPI affect EURUSD?'"""
    try:
        from app.scanner.news_scanner import get_todays_events, compute_news_bias
        events = await get_todays_events()
    except Exception:
        events = []

    # Detect mentioned asset
    asset = _extract_asset_from_analysis(text) or "USD pairs"

    # Detect event keyword
    ev_kw = None
    for kw in ["cpi", "nfp", "fomc", "gdp", "ppi", "ism", "pce", "payroll", "inflation", "fed", "rate"]:
        if kw in text.lower():
            ev_kw = kw
            break

    released = [e for e in events if e.get("actual") and (not ev_kw or ev_kw in e.get("title","").lower())]
    upcoming = [e for e in events if not e.get("actual") and (not ev_kw or ev_kw in e.get("title","").lower())]

    lines = [f"📊 *News Impact Analysis — {ev_kw.upper() if ev_kw else 'Economic News'} → {asset}*\n"]

    if released:
        ev = released[0]
        title = ev.get("title","")
        actual = ev.get("actual","")
        forecast = ev.get("forecast","—")
        currency = ev.get("currency","USD")
        try:
            a = float(str(actual).replace("%","").replace("K","").strip())
            f = float(str(forecast).replace("%","").replace("K","").strip())
            if a > f:
                bias = "🟢 *Bullish*"
                reason = f"Actual `{actual}` beat forecast `{forecast}` → {currency} strengthens"
                opinion = f"This favours a *BUY* on {currency} pairs (SELL {currency.replace('USD','')}/USD or BUY USD/{currency.replace('USD','')})."
            else:
                bias = "🔴 *Bearish*"
                reason = f"Actual `{actual}` missed forecast `{forecast}` → {currency} weakens"
                opinion = f"This favours a *SELL* on {currency} pairs. Watch for continued downside."
        except Exception:
            bias = "⚪ *Mixed*"
            reason = f"Data released: `{actual}` vs forecast `{forecast}`"
            opinion = "Conflicting signals — wait for price confirmation before entering."

        lines += [
            f"*{title}* just released:",
            f"  Actual: `{actual}` | Forecast: `{forecast}`",
            f"  Bias: {bias} — {reason}",
            f"\n💡 *My take:* {opinion}",
            f"\nRun /signal {asset.replace(' pairs','')} for a live entry signal with TP/SL.",
        ]
    elif upcoming:
        ev = upcoming[0]
        title = ev.get("title","")
        forecast = ev.get("forecast","—")
        currency = ev.get("currency","USD")
        time_str = ev.get("time","")
        t = f" at `{time_str}`" if time_str else " today"
        lines += [
            f"*{title}* has not released yet — due{t}.",
            f"Forecast: `{forecast}`",
            f"\n⚠️ *Pre-release advice:* Avoid opening new positions 15 minutes before and after release.",
            f"Markets can spike sharply either direction on the surprise factor.",
            f"\nOnce data releases, ask me again and I'll give you a buy/sell opinion based on the actual number.",
        ]
    else:
        lines.append(
            f"I don't have live {ev_kw.upper() if ev_kw else 'event'} data at this moment.\n\n"
            "In general:\n"
            "• 📈 Better-than-expected data → currency of that country strengthens\n"
            "• 📉 Worse-than-expected data → currency weakens\n"
            "• 🟡 In-line with forecast → minimal impact, look to technicals\n\n"
            f"Use /signal {asset} for a live technical signal right now."
        )

    return "\n".join(lines)


def _outside_trading_response(text: str, first_name: str) -> str:
    """Give a short, friendly response to non-trading questions."""
    t = text.lower()
    if any(w in t for w in ["joke", "funny", "laugh", "humor"]):
        return (
            "😄 Why did the forex trader break up with his girlfriend?\n"
            "Because she told him to *stop loss*! 😂\n\n"
            "Okay, back to charts — need a signal? /scan"
        )
    if any(w in t for w in ["weather", "temperature"]):
        return (
            "⛅ I can't check the weather, but I *can* tell you the market climate — "
            "and right now it's looking volatile! 📊\n\nUse /scan for a market check."
        )
    if any(w in t for w in ["sport", "football", "soccer", "cricket", "basketball"]):
        return (
            f"⚽ I'm all about the *trading game*, {first_name}! "
            "Though I appreciate the spirit — winning trades feel like scoring goals. ⚽\n\n"
            "Speaking of winning — use /scan to spot setups!"
        )
    # Generic outside-trading fallback
    return (
        f"That's outside my expertise, {first_name} — I live and breathe *markets, forex, and trading*. 📊\n\n"
        "But if you've got a trading question, I'm your bot. Try:\n"
        "• /scan — full market scan\n"
        "• /signal EURUSD — specific signal\n"
        "• Just ask: _\"When is the next CPI?\"_ or _\"Should I buy gold?\"_"
    )


# ── Main handler ───────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text       = update.message.text.strip()
    tid        = update.effective_user.id
    first_name = update.effective_user.first_name or "trader"

    user     = await get_user(tid)
    logged   = bool(user and user.get("email"))
    paid     = _is_paid(user)
    premium  = _is_premium(user)

    t_lower = text.lower()

    # ── 1. Creator / About ────────────────────────────────────────────────────
    if _match(text, _CREATOR_PATTERNS):
        await update.message.reply_text(_CREATOR_RESPONSE, parse_mode="Markdown")
        return

    # ── 2. Greeting ───────────────────────────────────────────────────────────
    if _is_greeting(text) and len(text.split()) <= 5:
        if not logged:
            await update.message.reply_text(
                f"Hey {first_name}! 👋 I'm *Raina AI* — your intelligent forex & crypto trading assistant.\n\n"
                "I analyse markets 24/5 and push real-time signals for Forex, Gold, and Crypto. "
                "Premium members get *MT5 auto-trading* — I trade their account automatically!\n\n"
                "Tap /start to create your free account.",
                parse_mode="Markdown",
            )
        else:
            hour = datetime.now(timezone.utc).hour
            time_of_day = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
            subs = user.get("subscription","free").title()
            await update.message.reply_text(
                f"Good {time_of_day}, {first_name}! 👋 Welcome back.\n\n"
                f"Your plan: *{subs}* | Markets are live and I'm scanning. 📡\n\n"
                "What would you like to do?\n"
                "• /scan — full market scan\n"
                "• Ask me: _\"When is the next CPI?\"_\n"
                "• Ask me: _\"Should I buy EURUSD?\"_"
                + ("\n• /scalping — toggle MT5 auto-trading" if premium else ""),
                parse_mode="Markdown",
            )
        return

    # ── 3. Economic calendar question ─────────────────────────────────────────
    if _match(text, _CALENDAR_PATTERNS):
        if not logged:
            await update.message.reply_text(
                "Sign up with /start to get live economic calendar answers! 📅"
            )
            return
        await update.message.reply_text("📅 Checking the economic calendar for you...", parse_mode="Markdown")
        answer = await _fetch_calendar_answer(text)
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    # ── 4. News impact / "how will CPI affect markets" ────────────────────────
    impact_phrases = [
        r"how (will|does|did|would|could) (the )?(cpi|nfp|fomc|gdp|inflation|fed|rate|payroll|data|news)",
        r"(what (does|did|will|would)) (the )?(cpi|nfp|fomc|gdp|inflation) (mean|do|affect|impact)",
        r"(impact|affect|effect) (of|from) (cpi|nfp|fomc|gdp|inflation|news)",
        r"(cpi|nfp|fomc|gdp|inflation|payroll).*(buy|sell|hold|bullish|bearish|direction|move|push)",
        r"(rate hike|rate cut|hawkish|dovish).*(buy|sell|hold|impact|affect|move|mean)",
    ]
    if _match(text, impact_phrases):
        if not logged:
            await update.message.reply_text("Sign up with /start to get market analysis! 📊")
            return
        if not paid:
            await update.message.reply_text(_UPGRADE_MSG, parse_mode="Markdown")
            return
        await update.message.reply_text("🔍 Analysing news impact...", parse_mode="Markdown")
        answer = await _answer_news_impact(text)
        await update.message.reply_text(answer, parse_mode="Markdown")
        return

    # ── 5. Signal analysis / buy-sell opinion ─────────────────────────────────
    if _match(text, _ANALYSIS_PATTERNS):
        if not logged:
            await update.message.reply_text(
                "Create a free account with /start, then upgrade for signal analysis! 📊"
            )
            return
        if not paid:
            await update.message.reply_text(_UPGRADE_MSG, parse_mode="Markdown")
            return

        asset = _extract_asset_from_analysis(text)
        if asset:
            await update.message.reply_text(f"🔍 Running live analysis on *{asset}*...", parse_mode="Markdown")
            answer = await _fetch_signal_analysis(asset)
            await update.message.reply_text(answer, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "📊 I can give you a buy/sell opinion on any pair!\n\n"
                "Just mention the market — for example:\n"
                "• _\"Should I buy EURUSD?\"_\n"
                "• _\"What do you think about Gold?\"_\n"
                "• _\"Is Bitcoin bullish right now?\"_",
                parse_mode="Markdown",
            )
        return

    # ── 6. General market / scanning question ─────────────────────────────────
    if any(w in t_lower for w in _GENERAL_MARKET_WORDS):
        if not logged:
            await update.message.reply_text(
                "I'm *Raina AI* 📊 — sign up with /start to get live market signals!",
                parse_mode="Markdown",
            )
            return
        await update.message.reply_text(
            f"I scan *{', '.join(['EURUSD','GBPUSD','XAUUSD','BTCUSD'])}* and more, 24/5. 📡\n\n"
            "• /scan — fresh scan of all markets\n"
            "• /signal EURUSD — signal for a specific pair\n"
            "• Or just ask me: _\"Should I buy gold right now?\"_",
            parse_mode="Markdown",
        )
        return

    # ── 7. Outside-trading questions ──────────────────────────────────────────
    if _match(text, _OUTSIDE_TRADING_PATTERNS):
        await update.message.reply_text(
            _outside_trading_response(text, first_name), parse_mode="Markdown"
        )
        return

    # ── 8. Help / commands ────────────────────────────────────────────────────
    help_words = {"help", "commands", "options", "menu", "what can you do", "features"}
    if any(w in t_lower for w in help_words):
        cmds = _COMMANDS_BASE
        if premium:
            cmds += _COMMANDS_PREMIUM
        if not logged:
            cmds = "I'm *Raina AI* 🤖 — your trading assistant.\nTap /start to sign up and get started!"
        await update.message.reply_text(cmds, parse_mode="Markdown")
        return

    # ── 9. AI fallback — let GPT handle anything we didn't pattern-match ────
    if not logged:
        await update.message.reply_text(
            "I'm *Raina AI* 🤖 — your intelligent trading assistant.\n"
            "Tap /start to create your free account and get live signals!",
            parse_mode="Markdown",
        )
        return

    # Try AI response first (works for any trading question, including edge cases)
    try:
        from app.analysis.ai_enhancer import ai_chat_response
        ai_reply = await ai_chat_response(
            user_message=text,
            symbol=None,
            signal_context=None,
            user_name=first_name,
        )
        if ai_reply:
            await update.message.reply_text(ai_reply, parse_mode="Markdown")
            return
    except Exception as e:
        logger.debug(f"[chat] AI fallback error: {e}")

    # Hard fallback when OpenAI isn't configured
    fallbacks = [
        f"Not sure I follow, {first_name} — but I'm here for anything trading-related! 📊\n"
        "Try: _\"When is CPI?\"_, _\"Should I buy EURUSD?\"_, or /scan.",

        "I'm best at market questions! 🎯\n"
        "Ask me things like:\n"
        "• _\"Is Gold bullish right now?\"_\n"
        "• _\"When is the next NFP?\"_\n"
        "• _\"Should I buy or sell GBPUSD?\"_",

        f"I live and breathe charts, {first_name}! 📈\n"
        "What market are you interested in? I'll give you my take.",
    ]
    await update.message.reply_text(random.choice(fallbacks), parse_mode="Markdown")
