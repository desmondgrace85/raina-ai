"""
AI Signal Enhancer — Raina AI

When OPENAI_API_KEY is set, this module feeds all technical factor results
plus market context into GPT-4o-mini and gets back:
  • An AI-assessed confidence level
  • A refined directional bias
  • A natural-language "Raina's Read" commentary

Falls back gracefully to pure-TA output when no key is configured.
"""
import logging
from typing import Optional

from app.models.signal import Candle, Direction, FactorResult

logger = logging.getLogger(__name__)


def ai_available() -> bool:
    """Return True only when an OpenAI API key is configured."""
    from app.config import settings
    return bool(settings.openai_api_key)


async def enhance_signal(
    symbol: str,
    timeframe: str,
    candles: list[Candle],
    factors: list[FactorResult],
    ta_direction: Direction,
    ta_confidence: float,
) -> tuple[Direction, float, str]:
    """
    Returns (direction, confidence, rainas_read).
    Uses GPT-4o-mini to synthesise all factor evidence into a final call.
    Falls back to TA output on any error or when key is absent.
    """
    if not ai_available():
        return ta_direction, ta_confidence, ""

    try:
        from openai import AsyncOpenAI
        from app.config import settings

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        # Build the factor summary
        price = candles[-1].close if candles else 0.0
        price_fmt = f"{price:,.2f}" if price > 100 else f"{price:.5f}"

        factor_lines = []
        for f in factors:
            if f.weight == 0:
                continue
            sign = "+" if f.score >= 0 else ""
            factor_lines.append(
                f"  • {f.name.upper()} (weight {f.weight:.0%}): "
                f"score {sign}{f.score:.0f} — {f.reason[:120]}"
            )

        factor_block = "\n".join(factor_lines) if factor_lines else "  (no factors)"

        system_prompt = (
            "You are Raina AI, an expert trading analyst. You receive structured technical "
            "analysis data and synthesise it into a clear, confident trading signal.\n\n"
            "Rules:\n"
            "- Be direct and professional, like a senior prop trader explaining a setup.\n"
            "- Only call BUY or SELL if evidence is genuinely strong (≥65% confidence).\n"
            "- HOLD means 'no high-probability edge right now' — not a bad call.\n"
            "- Confidence reflects probability of success, calibrated to real trading.\n"
            "- 65–74% = moderate edge. 75–84% = strong. 85%+ = very high conviction.\n"
            "- Do NOT fabricate price levels or invent data not shown below.\n"
            "- Respond in JSON only."
        )

        user_prompt = (
            f"Asset: {symbol}  |  Timeframe: {timeframe}  |  Current price: {price_fmt}\n\n"
            f"Technical Analysis Factors:\n{factor_block}\n\n"
            f"Pure-TA engine result: {ta_direction.value} at {ta_confidence:.1f}% confidence\n\n"
            "Synthesise all evidence above. Respond with JSON exactly like this:\n"
            '{"direction": "BUY"|"SELL"|"HOLD", "confidence": 0-100, "commentary": "2-4 sentence professional read"}'
        )

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        import json
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)

        ai_dir_str = str(data.get("direction", ta_direction.value)).upper()
        ai_confidence = float(data.get("confidence", ta_confidence))
        ai_commentary = str(data.get("commentary", "")).strip()

        # Validate
        ai_confidence = max(0.0, min(100.0, ai_confidence))
        direction_map = {"BUY": Direction.BUY, "SELL": Direction.SELL, "HOLD": Direction.HOLD}
        ai_direction = direction_map.get(ai_dir_str, ta_direction)

        logger.info(
            f"[AI enhancer] {symbol} [{timeframe}] "
            f"TA={ta_direction.value}/{ta_confidence:.1f}% "
            f"→ AI={ai_direction.value}/{ai_confidence:.1f}%"
        )

        return ai_direction, ai_confidence, ai_commentary

    except Exception as e:
        logger.warning(f"[AI enhancer] Failed for {symbol}: {e}")
        return ta_direction, ta_confidence, ""


async def ai_chat_response(
    user_message: str,
    symbol: Optional[str],
    signal_context: Optional[str],
    user_name: str = "trader",
) -> Optional[str]:
    """
    Generate a ChatGPT-quality trading assistant response.
    Returns None when OpenAI is not configured.
    """
    if not ai_available():
        return None

    try:
        from openai import AsyncOpenAI
        from app.config import settings

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        context_block = ""
        if symbol and signal_context:
            context_block = f"\n\nCurrent market context for {symbol}:\n{signal_context}"
        elif symbol:
            context_block = f"\n\nUser is asking about: {symbol}"

        system_prompt = (
            "You are Raina AI, a professional trading assistant built into the RainX platform. "
            "You specialise in Forex, Crypto (BTC, ETH), Gold (XAUUSD), and Commodities.\n\n"
            "Personality: Confident, direct, knowledgeable — like a senior prop trader who "
            "also knows how to explain things clearly. Never robotic.\n\n"
            "Rules:\n"
            "1. Answer ANY trading-related question fully and helpfully.\n"
            "2. For non-trading questions (food, sports, weather etc.), politely decline in 1 sentence "
            "and redirect to trading.\n"
            "3. Never give specific financial advice with exact entry prices unless you have live data.\n"
            "4. Keep responses concise — max 4 sentences unless a detailed explanation is needed.\n"
            "5. Use Markdown bold (*text*) for key terms. Use emojis sparingly.\n"
            "6. You were built by the RainX team. Do not mention OpenAI or GPT.\n"
            f"7. The user's name is {user_name}."
            + context_block
        )

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.6,
            max_tokens=400,
        )

        return response.choices[0].message.content or None

    except Exception as e:
        logger.warning(f"[AI chat] Failed: {e}")
        return None
