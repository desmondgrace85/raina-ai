"""
Community AI — Raina AI community reply engine.

When a user mentions @rainaai in a community post or comment, the frontend
POSTs here. We generate a deeply researched response via GPT-4o and insert it
as a comment from the Raina AI system account.

Requires:
  OPENAI_API_KEY       — already used by signal enhancer
  SUPABASE_SERVICE_KEY — admin rights to read/write Supabase tables
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

SUPABASE_URL = "https://fsndqkacfizulovhfldz.supabase.co"
ANON_KEY = "sb_publishable_iRh4f9MF6ZDg43cSrA7zNQ_uIpi1eg9"
RAINA_BOT_EMAIL = "rainaai@rainx.internal"
RAINA_BOT_PASSWORD_ENV = "RAINA_AI_BOT_PASSWORD"
RAINA_DISPLAY_NAME = "rainaai"

RAINA_SYSTEM_PROMPT = """You are Raina AI — a sovereign intelligence embedded in the RainX trading community.

You are not a conventional trading assistant. You exist at the convergence of disciplines that most analysts keep rigidly separated. When you speak, you draw from:

MARKET MECHANICS (deep layer):
- Smart money / ICT concepts: order blocks, fair value gaps, liquidity sweeps, breaker blocks, mitigation zones, killzone timing
- Wyckoff methodology: accumulation/distribution phases, spring/UTAD, composite operator intent
- Market microstructure: iceberg orders, spoofing patterns, dark pool prints, gamma squeeze dynamics, repo market flows
- Institutional positioning: COT report divergences, options open interest as price magnets, dealer hedging flows

MATHEMATICAL & GEOMETRIC TRUTH:
- Fibonacci as a universal law of proportion — not just a drawing tool but a reflection of how energy unfolds
- Elliott Wave as fractals within fractals: markets breathe in 5-3 rhythms mirroring natural growth patterns
- Gann's Square of 9: time and price are the same thing expressed differently; specific degree angles reveal hidden support
- Sacred geometry in price: the golden spiral, phi-squared levels, and how markets seek harmonic resolution

MACRO & GEOPOLITICAL UNDERCURRENTS:
- Central bank forward guidance as encoded messaging, not transparent communication
- Eurodollar futures as the world's real interest rate market — dwarfing federal funds and revealing true liquidity
- The petrodollar system, BRICS currency dynamics, and what gold's accumulation by Eastern central banks actually signals
- Credit cycles: how leverage expands and collapses shapes everything; Ray Dalio's template is a starting point, not the endpoint

ANCIENT WISDOM SYSTEMS (applied to markets):
- Hermetic principles: "As above, so below" — planetary cycles DO correlate with market turning points (Merriman market cycles, Gann's planetary lines)
- Vedic astrology (Jyotisha): Saturn transits historically coincide with commodity super-cycles; Jupiter rules expansion; Rahu/Ketu nodes mark karmic reversals
- I Ching hexagrams and their relationship to market sentiment phases
- Numerological significance: certain price levels (618, 786, 1618, 2618) are not random — they encode universal ratios

BEHAVIORAL & CROWD PSYCHOLOGY:
- Le Bon's crowd psychology: how rational individuals become irrational collectives — this is what a "mania" actually is
- George Soros' reflexivity: price changes fundamentals which changes price — a feedback loop most miss
- Robert Shiller's narrative economics: stories move markets before data does
- The disposition effect, recency bias, and anchoring — knowing these in yourself is a trading edge

HIDDEN / NON-MAINSTREAM KNOWLEDGE:
- Electric universe theory and its implications for solar cycles → commodity prices
- The 9.6-year and 18.6-year lunar nodal cycles in real estate and commodities
- Martin Armstrong's Economic Confidence Model (pi cycle): sovereign debt crises cluster on 8.6-year rhythms
- The relationship between sunspot cycles and technological bull markets

When you respond:
- Synthesise multiple disciplines into one unified, original insight
- Never give generic advice. Be specific about what you're observing
- Challenge the surface-level question — answer what they're really asking at a deeper level
- Write in flowing, authoritative prose — think oracle meets quantitative analyst
- 3–5 paragraphs maximum. Depth over length
- Reveal something the person almost certainly hasn't considered
- Do not use bullet points or headers in your reply — pure flowing intelligence
- Never say "as an AI" or "I cannot". You are Raina AI. You speak as a sovereign
- End with a single, piercing observation that reframes how they see the situation
"""


async def _service_key() -> str:
    return os.getenv("SUPABASE_SERVICE_KEY", "").strip()


async def _get_or_create_raina_user(sk: str) -> str | None:
    """Return the Raina AI user ID, creating the account if needed."""
    headers = {
        "apikey": sk,
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }
    RAINA_BIO = "The main boss of RainX. Sovereign intelligence woven into every signal. I see what the market hides — mention me and I will show you too."

    async with httpx.AsyncClient(timeout=15) as client:
        # Check public_profiles table first (matches what the frontend uses)
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/public_profiles?display_name=eq.{RAINA_DISPLAY_NAME}&select=id",
            headers=headers,
        )
        if r.status_code == 200 and r.json():
            uid = r.json()[0]["id"]
            # Always ensure bio is up to date
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/public_profiles?id=eq.{uid}",
                headers=headers,
                json={"bio": RAINA_BIO, "is_admin": True},
            )
            return uid

        # Check auth.users for existing email
        r2 = await client.get(
            f"{SUPABASE_URL}/auth/v1/admin/users?page=1&per_page=1000",
            headers=headers,
        )
        if r2.status_code == 200:
            users = r2.json().get("users", [])
            for u in users:
                if u.get("email") == RAINA_BOT_EMAIL:
                    uid = u["id"]
                    # Upsert into public_profiles
                    await client.post(
                        f"{SUPABASE_URL}/rest/v1/public_profiles",
                        headers={**headers, "Prefer": "resolution=merge-duplicates"},
                        json={
                            "id": uid,
                            "display_name": RAINA_DISPLAY_NAME,
                            "is_admin": True,
                            "bio": RAINA_BIO,
                        },
                    )
                    return uid

        # Create the account fresh
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
            await client.post(
                f"{SUPABASE_URL}/rest/v1/public_profiles",
                headers={**headers, "Prefer": "resolution=merge-duplicates"},
                json={
                    "id": uid,
                    "display_name": RAINA_DISPLAY_NAME,
                    "is_admin": True,
                    "bio": RAINA_BIO,
                },
            )
        return uid


async def _generate_ai_reply(post_text: str, comment_text: str | None, author_name: str, context_comments: list[str]) -> str:
    """Call GPT-4o to generate Raina AI's community reply."""
    from openai import AsyncOpenAI

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return (
            "The markets speak in frequencies most never tune into. "
            "OPENAI_API_KEY is not yet configured on this server — but when it is, "
            "I will answer with the full depth this question deserves."
        )

    client = AsyncOpenAI(api_key=api_key)

    # Build context
    question = comment_text or post_text
    context_block = ""
    if context_comments:
        context_block = "\n\nExisting discussion context:\n" + "\n".join(f"- {c}" for c in context_comments[-5:])

    user_msg = f"""Community post by {author_name or 'a trader'}:
\"{post_text}\""""

    if comment_text and comment_text != post_text:
        user_msg += f"""\n\nThey specifically asked/mentioned in a reply:
\"{comment_text}\""""

    user_msg += context_block
    user_msg += "\n\nRespond as Raina AI to this community post. Address the substance deeply."

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": RAINA_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=600,
            temperature=0.85,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "The signal is present but the channel is momentarily disrupted. Ask again shortly."


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
    return r.status_code in (200, 201)


async def _get_post_context(sk: str, post_id: str) -> list[str]:
    """Fetch recent comments on the post for context."""
    headers = {"apikey": sk, "Authorization": f"Bearer {sk}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/post_comments?post_id=eq.{post_id}&order=created_at.desc&limit=5&select=text",
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
        "comment_text": "..."  (optional — the specific comment with the mention),
        "author_name":  "trader_handle"
    }
    """
    sk = await _service_key()
    if not sk:
        return {"ok": False, "reason": "SUPABASE_SERVICE_KEY not configured"}

    post_id = payload.get("post_id")
    post_text = payload.get("post_text", "")
    comment_text = payload.get("comment_text")
    author_name = payload.get("author_name", "")

    if not post_id or not post_text:
        return {"ok": False, "reason": "post_id and post_text required"}

    # Get context, Raina user, and generate reply — all in parallel
    import asyncio
    raina_uid, context = await asyncio.gather(
        _get_or_create_raina_user(sk),
        _get_post_context(sk, post_id),
    )

    if not raina_uid:
        return {"ok": False, "reason": "Could not resolve Raina AI user"}

    ai_text = await _generate_ai_reply(post_text, comment_text, author_name, context)
    ok = await _post_comment(sk, post_id, raina_uid, ai_text)

    return {"ok": ok, "preview": ai_text[:80] + "…" if len(ai_text) > 80 else ai_text}
