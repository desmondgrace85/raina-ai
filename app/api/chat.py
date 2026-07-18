"""
Raina chat endpoint — powered entirely by the Raina-AI signal engine.
No external LLM. Pulls a fresh signal for the instrument, then builds
a natural-language response from the actual technical analysis data.
"""
from fastapi import APIRouter
from app.models.signal import Direction

router = APIRouter()

# Populated by app.main at startup (same pattern as routes.py)
_provider = None

def set_provider(provider):
    global _provider
    _provider = provider


def _fmt(val, digits=5):
    if val is None:
        return "N/A"
    return f"{val:.{digits}f}"


def _build_reply(question: str, symbol: str, signal, context: str) -> str:
    """
    Build a focused, natural reply from real signal data.
    No hallucination possible — every number comes from the engine.
    """
    q = question.lower()
    d = signal.direction
    conf = signal.confidence
    entry = (signal.entry_zone[0] + signal.entry_zone[1]) / 2 if signal.entry_zone else None
    sl = signal.stop_loss
    tp = signal.take_profit or []
    rl = signal.risk_level.value if hasattr(signal.risk_level, "value") else str(signal.risk_level)
    explanation = signal.explanation or ""
    rr = signal.risk_reward_ratio

    # ── Should I enter / buy / sell / trade? ─────────────────────────────────
    if any(w in q for w in ["enter", "buy", "sell", "trade", "open", "position", "go long", "go short", "should i"]):
        if d == Direction.HOLD or conf < 65:
            return (
                f"My read on {symbol} right now is to stand aside. "
                f"Confidence is {conf:.0f}% — below the 65% threshold I need before calling a setup. "
                f"{explanation} "
                f"Wait for clearer conditions before putting capital at risk."
            )
        verb = "long" if d == Direction.BUY else "short"
        tp1 = _fmt(tp[0]) if tp else "N/A"
        tp2 = _fmt(tp[1]) if len(tp) > 1 else "N/A"
        rr_str = f" Risk/reward is {rr:.1f}R." if rr else ""
        return (
            f"My engine is reading a potential {verb} setup on {symbol} — {conf:.0f}% confidence, risk {rl}. "
            f"Entry zone: {_fmt(signal.entry_zone[0])}–{_fmt(signal.entry_zone[1])}. "
            f"Stop loss: {_fmt(sl)}. Targets: TP1 {tp1}, TP2 {tp2}.{rr_str} "
            f"Reasoning: {explanation} "
            f"Position sizing is your call — I surface the setup, you manage the risk."
        )

    # ── Stop loss ─────────────────────────────────────────────────────────────
    if any(w in q for w in ["stop", "sl", "stop loss", "stoploss", "where to stop"]):
        return (
            f"For {symbol} my suggested stop loss is {_fmt(sl)}. "
            f"Risk level on this setup: {rl}. "
            f"{explanation}"
        )

    # ── Take profit / targets ─────────────────────────────────────────────────
    if any(w in q for w in ["target", "tp", "take profit", "profit", "exit"]):
        tp1 = _fmt(tp[0]) if tp else "N/A"
        tp2 = _fmt(tp[1]) if len(tp) > 1 else "N/A"
        rr_str = f" Risk/reward ratio: {rr:.1f}R." if rr else ""
        return (
            f"Take-profit targets for {symbol}: TP1 → {tp1}, TP2 → {tp2}.{rr_str} "
            f"These are derived from key support/resistance and ATR-based levels in the engine. "
            f"{explanation}"
        )

    # ── Why / reason / explain / analysis ────────────────────────────────────
    if any(w in q for w in ["why", "reason", "explain", "analysis", "because", "basis", "how", "what makes"]):
        return (
            f"Here's my full read on {symbol}: {explanation} "
            f"Direction: {d.value}, confidence: {conf:.0f}%, risk: {rl}."
        )

    # ── Confidence / strong / weak ────────────────────────────────────────────
    if any(w in q for w in ["confidence", "strong", "weak", "reliable", "accurate", "certain"]):
        quality = "high" if conf >= 75 else "moderate" if conf >= 65 else "low"
        return (
            f"Current confidence on {symbol} is {conf:.0f}% — that's a {quality}-quality signal. "
            f"Confidence is computed from {9} technical factors (trend, MACD, ADX, momentum, volume, "
            f"candlestick patterns, support/resistance, multi-timeframe confluence, and news sentiment). "
            f"{explanation}"
        )

    # ── Risk ──────────────────────────────────────────────────────────────────
    if any(w in q for w in ["risk", "safe", "dangerous", "volatile"]):
        return (
            f"Risk on {symbol} is rated {rl} by my volatility engine. "
            f"The setup has {conf:.0f}% confidence. "
            f"{explanation} Always size positions so a single loss doesn't hurt your account badly."
        )

    # ── General / fallback ────────────────────────────────────────────────────
    direction_str = d.value
    tp1 = _fmt(tp[0]) if tp else "N/A"
    return (
        f"Current signal for {symbol}: {direction_str} at {conf:.0f}% confidence. "
        f"Entry zone {_fmt(signal.entry_zone[0])}–{_fmt(signal.entry_zone[1])}, "
        f"SL {_fmt(sl)}, TP1 {tp1}, risk {rl}. "
        f"{explanation}"
    )


@router.post("/chat")
async def chat(payload: dict):
    """
    Raina AI chat — answers questions about a specific instrument
    using the live signal from the Raina-AI engine.

    Expected payload:
    {
        "symbol": "EURUSD",
        "messages": [{"role": "user"|"assistant", "text": "..."}],
        "context": "optional pre-built context string from the frontend"
    }
    """
    from app.engines import long_term_engine

    symbol = (payload.get("symbol") or "").strip().upper() or "EURUSD"
    messages = payload.get("messages") or []
    context = payload.get("context") or ""

    # Last user message
    question = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            question = (m.get("text") or "").strip()
            break

    if not question:
        return {"reply": "What would you like to know about the market?"}

    # Special: who made you / who created you
    creator_words = ["who made", "who created", "who built", "who is behind", "who developed", "ceo", "founder", "creator"]
    if any(w in question.lower() for w in creator_words):
        return {"reply": "I'm Raina, built by a dedicated team of developers and traders. RainX's CEO is Desmond Banful."}

    # Greeting
    greetings = ["hello", "hi", "hey", "good morning", "good evening", "what's up", "sup"]
    if any(question.lower().strip().startswith(g) for g in greetings):
        return {"reply": f"Hey! I'm Raina, your trading companion. What would you like to know about {symbol}?"}

    if _provider is None:
        return {"reply": "Signal engine is still warming up — try again in a moment."}

    try:
        signal = await long_term_engine.generate_signal(_provider, symbol, "1h")
        reply = _build_reply(question, symbol, signal, context)
    except Exception as e:
        reply = (
            f"I couldn't fetch fresh data for {symbol} right now ({type(e).__name__}). "
            f"Make sure the symbol is supported (e.g. EURUSD, XAUUSD, BTCUSD) and try again."
        )

    return {"reply": reply}
