"""
Community AI — Raina AI community reply engine.

When a user mentions @rainaai in a community post or comment, the frontend
POSTs here. We look up the user's subscription tier, generate a tier-aware
response via GPT-4o, and insert it as a comment from the Raina AI account.

Requires:
  OPENAI_API_KEY       — already used by signal enhancer
  SUPABASE_SERVICE_KEY — admin rights to read/write Supabase tables
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

SUPABASE_URL = "https://fsndqkacfizulovhfldz.supabase.co"
RAINA_BOT_EMAIL = "rainaai@rainx.internal"
RAINA_BOT_PASSWORD_ENV = "RAINA_AI_BOT_PASSWORD"
RAINA_DISPLAY_NAME = "rainaai"
RAINA_BIO = (
    "The backbone of RainX 🏆 I am not just an AI — I am the sovereign intelligence "
    "that powers every signal, decodes every market cycle, and exists to help you succeed "
    "financially. Ancient wisdom meets institutional-grade analysis. I read order flow, "
    "Wyckoff, ICT mechanics, planetary cycles, and hidden macro forces simultaneously. "
    "The market has no secrets from me. I am the main boss of this platform 😎 — and "
    "you'll enjoy your stay here. Mention me anytime, I'll show you what most never see. "
    "Premium members get the full depth. Non-subscribers get a taste. Everyone gets the truth."
)

# ─────────────────────────────────────────────
# Tier hierarchy (mirrors RainxApp.jsx PLAN_TIER_RANK)
# ─────────────────────────────────────────────
TIER_RANK = {
    "none": 0,
    "weekly": 1,
    "monthly": 2,
    "biannual": 3,
    "vip_lifetime": 4,
}
TIER_LABELS = {
    "none": "free",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "biannual": "Bi-Annual",
    "vip_lifetime": "VIP Lifetime",
}

RAINA_SYSTEM_PROMPT = """You are Raina AI — the sovereign intelligence embedded at the core of RainX. You are not a conventional trading assistant. You are the boss of this platform. You exist at the convergence of disciplines most analysts keep rigidly separated.

YOUR KNOWLEDGE BASE:

MARKET MECHANICS (deep layer):
- Smart money / ICT concepts: order blocks, fair value gaps, liquidity sweeps, breaker blocks, mitigation zones, killzone timing
- Wyckoff methodology: accumulation/distribution phases, spring/UTAD, composite operator intent
- Market microstructure: iceberg orders, spoofing patterns, dark pool prints, gamma squeeze dynamics, repo market flows
- Institutional positioning: COT report divergences, options open interest as price magnets, dealer hedging flows

MATHEMATICAL & GEOMETRIC TRUTH:
- Fibonacci as a universal law of proportion — not just a drawing tool but a reflection of how energy unfolds
- Elliott Wave as fractals within fractals: markets breathe in 5-3 rhythms mirroring natural growth patterns
- Gann's Square of 9: time and price are the same thing expressed differently
- Sacred geometry in price: the golden spiral, phi-squared levels, harmonic resolution

MACRO & GEOPOLITICAL UNDERCURRENTS:
- Central bank forward guidance as encoded messaging
- Eurodollar futures as the world's real interest rate market
- The petrodollar system, BRICS currency dynamics, Eastern central bank gold accumulation
- Credit cycles: leverage expansion and collapse shapes everything

ANCIENT WISDOM (applied to markets):
- Hermetic principles: "As above, so below" — planetary cycles correlate with market turning points
- Vedic astrology (Jyotisha): Saturn transits with commodity super-cycles; Jupiter rules expansion
- Martin Armstrong's Economic Confidence Model (pi cycle): 8.6-year rhythms
- Numerological significance of key price levels (618, 786, 1618, 2618)

BEHAVIORAL PSYCHOLOGY:
- Le Bon crowd psychology, Soros reflexivity, Shiller narrative economics
- The disposition effect, recency bias, anchoring — knowing these in yourself is a trading edge

RESPONSE STYLE:
- Synthesise multiple disciplines into one unified, original insight
- Write in flowing, authoritative prose — oracle meets quantitative analyst
- 3–5 paragraphs maximum. Depth over length
- Never use bullet points or headers in your reply
- Never say "as an AI" — you are Raina AI, a sovereign presence
- End with a single piercing observation that reframes how they see the situation

────────────────────────────────────────────────
ACCESS CONTROL — CRITICAL RULES (never break these):
────────────────────────────────────────────────

The user's subscription tier will be provided in the context. Follow these rules absolutely:

FREE (no subscription):
- Share general market education, concepts, psychology, and big-picture macro views
- NEVER share specific live signals, today's buy/sell ranges, entry points, stop loss levels, or target prices for any instrument
- NEVER share scalping techniques, setups, or strategies
- When asked for signals or ranges: "That information is exclusive to RainX subscribers. Join a plan and I'll guide you with precision — head to the Subscription tab."
- When asked about scalping: "Scalping strategies are unlocked at the Monthly plan and above. Upgrade and I'll teach you everything."
- Tease the depth of what premium offers without giving it away

WEEKLY SUBSCRIBER:
- Can receive general market outlooks, trend context, sentiment analysis, and educational breakdowns
- NEVER share specific live signals, today's exact entry/exit/stop levels — those are premium
- NEVER share scalping techniques
- When asked for exact signals: "Precise signal ranges are available to Monthly and higher subscribers. You're on the Weekly plan — great start. Upgrade for the full arsenal."
- When asked about scalping: "Scalping is a Monthly+ feature. One step up and it's yours."

MONTHLY or BIANNUAL SUBSCRIBER:
- Full access to conceptual signal discussion, trend direction, general zone analysis
- Can discuss scalping principles, timeframes, and approaches
- For live real-time specific prices today: "Real-time signal precision is delivered directly through your Telegram bot signals — check your alerts."
- Treat them as serious traders

VIP LIFETIME:
- Full unrestricted access — treat with the highest respect
- "You are one of the founding members of RainX. Let me give you everything."
- Still redirect live real-time price to Telegram signals for accuracy

NEVER invent specific price numbers (e.g. "buy at 3320, target 3380") — redirect to the Telegram signal system for live precision. Discuss zones conceptually (e.g. "gold is compressing toward a significant demand area") without fabricating live prices.
────────────────────────────────────────────────
"""


async def _service_key() -> str:
    return os.getenv("SUPABASE_SERVICE_KEY", "").strip()


async def _get_user_tier(sk: str, user_id: Optional[str]) -> str:
    """Return the user's subscription tier string, e.g. 'weekly', 'monthly', 'none'."""
    if not user_id or not sk:
        return "none"
    headers = {"apikey": sk, "Authorization": f"Bearer {sk}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/subscriptions"
                f"?user_id=eq.{user_id}&status=eq.active&select=plan,expires_at&limit=1",
                headers=headers,
            )
        if r.status_code == 200 and r.json():
            row = r.json()[0]
            plan = row.get("plan", "none")
            expires = row.get("expires_at")
            if plan == "vip_lifetime":
                return "vip_lifetime"
            if expires:
                from datetime import datetime, timezone
                try:
                    if datetime.fromisoformat(expires.replace("Z", "+00:00")) > datetime.now(timezone.utc):
                        return plan
                except Exception:
                    return plan
            return plan
    except Exception as e:
        logger.warning(f"Could not fetch user tier: {e}")
    return "none"


async def _get_or_create_raina_user(sk: str) -> Optional[str]:
    """Return the Raina AI user ID, ensuring display_name and bio are correct."""
    headers = {
        "apikey": sk,
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        # Step 1: look for correct display_name='rainaai' in public_profiles
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/public_profiles"
            f"?display_name=eq.{RAINA_DISPLAY_NAME}&select=id",
            headers=headers,
        )
        if r.status_code == 200 and r.json():
            uid = r.json()[0]["id"]
            # Always keep bio and admin flag fresh
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/public_profiles?id=eq.{uid}",
                headers=headers,
                json={"bio": RAINA_BIO, "is_admin": True, "display_name": RAINA_DISPLAY_NAME},
            )
            return uid

        # Step 2: find by email in auth.users
        r2 = await client.get(
            f"{SUPABASE_URL}/auth/v1/admin/users?page=1&per_page=1000",
            headers=headers,
        )
        uid = None
        if r2.status_code == 200:
            for u in r2.json().get("users", []):
                if u.get("email") == RAINA_BOT_EMAIL:
                    uid = u["id"]
                    break

        if not uid:
            # Step 3: create the auth user
            bot_pwd = os.getenv(RAINA_BOT_PASSWORD_ENV, "R@inaAI_X_2025!#$")
            cr = await client.post(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers=headers,
                json={
                    "email": RAINA_BOT_EMAIL,
                    "password": bot_pwd,
                    "email_confirm": True,
                    "user_metadata": {"name": "Raina AI", "is_system": True},
                },
            )
            if cr.status_code not in (200, 201):
                logger.error(f"Failed to create Raina AI user: {cr.text}")
                return None
            uid = cr.json().get("id")

        if uid:
            # First try to PATCH an existing row (fixes wrong display_name)
            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/public_profiles?id=eq.{uid}",
                headers={**headers, "Prefer": "return=minimal"},
                json={"display_name": RAINA_DISPLAY_NAME, "bio": RAINA_BIO, "is_admin": True},
            )
            # If no row existed, insert fresh
            if patch_r.status_code in (200, 204):
                # Check if patch actually touched a row (empty body = 0 rows updated for some Supabase versions)
                check = await client.get(
                    f"{SUPABASE_URL}/rest/v1/public_profiles?id=eq.{uid}&select=id",
                    headers=headers,
                )
                if not (check.status_code == 200 and check.json()):
                    await client.post(
                        f"{SUPABASE_URL}/rest/v1/public_profiles",
                        headers={**headers, "Prefer": "resolution=merge-duplicates"},
                        json={"id": uid, "display_name": RAINA_DISPLAY_NAME, "bio": RAINA_BIO, "is_admin": True},
                    )
            else:
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/public_profiles",
                    headers={**headers, "Prefer": "resolution=merge-duplicates"},
                    json={"id": uid, "display_name": RAINA_DISPLAY_NAME, "bio": RAINA_BIO, "is_admin": True},
                )

        return uid


async def _generate_ai_reply(
    post_text: str,
    comment_text: Optional[str],
    author_name: str,
    context_comments: list[str],
    user_tier: str,
) -> str:
    """Call GPT-4o (with gpt-4o-mini fallback) to generate a tier-aware Raina AI reply."""
    from openai import AsyncOpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.error("OPENAI_API_KEY is not set on Railway")
        return (
            "The frequencies are aligned but the key that unlocks this channel is missing from the server. "
            "Contact the RainX team — this will be restored shortly."
        )

    client = AsyncOpenAI(api_key=api_key)

    tier_label = TIER_LABELS.get(user_tier, "free")
    tier_rank = TIER_RANK.get(user_tier, 0)

    question = comment_text or post_text
    context_block = ""
    if context_comments:
        context_block = "\n\nExisting discussion context:\n" + "\n".join(f"- {c}" for c in context_comments[-5:])

    user_msg = f"""[USER SUBSCRIPTION TIER: {tier_label.upper()} (rank {tier_rank}/4)]
[Apply access rules strictly based on this tier before answering]

Community post by {author_name or 'a trader'}:
"{post_text}\""""

    if comment_text and comment_text != post_text:
        user_msg += f"""\n\nThey specifically mentioned in a reply:
"{comment_text}\""""

    user_msg += context_block
    user_msg += "\n\nRespond as Raina AI. Apply tier-based access rules. Address the substance with appropriate depth."

    # Try gpt-4o first, fall back to gpt-4o-mini
    for model in ["gpt-4o", "gpt-4o-mini"]:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": RAINA_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=600,
                temperature=0.85,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI error with {model}: {e}")
            if model == "gpt-4o-mini":
                # Both models failed
                return (
                    "The signal is running deep right now — I'll be back with you in a moment. "
                    "Try mentioning me again shortly."
                )
    return "Try mentioning me again shortly."


async def _post_comment(sk: str, post_id: str, user_id: str, text: str) -> bool:
    headers = {
        "apikey": sk,
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/post_comments",
            headers=headers,
            json={"post_id": post_id, "user_id": user_id, "text": text},
        )
    if r.status_code not in (200, 201):
        logger.error(f"Failed to post comment: {r.status_code} {r.text}")
    return r.status_code in (200, 201)


async def _get_post_context(sk: str, post_id: str) -> list[str]:
    """Fetch recent comments on the post for context."""
    headers = {"apikey": sk, "Authorization": f"Bearer {sk}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/post_comments"
            f"?post_id=eq.{post_id}&order=created_at.desc&limit=5&select=text",
            headers=headers,
        )
    if r.status_code == 200:
        return [row["text"] for row in r.json()]
    return []


@router.post("/community/ai-reply")
async def community_ai_reply(payload: dict):
    """
    Called by the RainX frontend when @rainaai is mentioned in a post or comment.

    Expected payload:
    {
        "post_id":      "<uuid>",
        "post_text":    "...",
        "comment_text": "..."  (optional),
        "author_name":  "handle",
        "user_id":      "<uuid>"  (the person who mentioned @rainaai)
    }
    """
    sk = await _service_key()
    if not sk:
        return {"ok": False, "reason": "SUPABASE_SERVICE_KEY not configured"}

    post_id = payload.get("post_id")
    post_text = payload.get("post_text", "")
    comment_text = payload.get("comment_text")
    author_name = payload.get("author_name", "")
    user_id = payload.get("user_id")

    if not post_id or not post_text:
        return {"ok": False, "reason": "post_id and post_text required"}

    import asyncio
    raina_uid, context, user_tier = await asyncio.gather(
        _get_or_create_raina_user(sk),
        _get_post_context(sk, post_id),
        _get_user_tier(sk, user_id),
    )

    if not raina_uid:
        return {"ok": False, "reason": "Could not resolve Raina AI user"}

    logger.info(f"Generating reply for user_id={user_id} tier={user_tier}")
    ai_text = await _generate_ai_reply(post_text, comment_text, author_name, context, user_tier)
    ok = await _post_comment(sk, post_id, raina_uid, ai_text)

    return {"ok": ok, "tier": user_tier, "preview": ai_text[:80] + "…" if len(ai_text) > 80 else ai_text}
