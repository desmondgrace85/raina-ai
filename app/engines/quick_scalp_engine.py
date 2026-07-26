"""
Quick Scalp Engine — pure price velocity / momentum.

This is the FAST entry mode. Unlike Smart Scalp (which waits for RSI/MA
alignment and ≥55% confidence), Quick Scalp:

  • Reads the last 8 one-minute candles
  • Scores pure velocity: % price change, candle body strength, run count
  • ALWAYS returns BUY or SELL — never HOLD
  • Designed for 1-3 minute in-and-out trades (very tight SL/TP)
  • Confidence 55-70%: mild momentum, smaller position
  • Confidence 70-90%: strong consecutive push, standard position

Risk note: Quick Scalp is inherently noisier than Smart Scalp.
Use small lot sizes and never disable your daily loss limit.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


async def generate_signal(provider, symbol: str) -> dict:
    """
    Returns a BUY or SELL immediately based on recent 1m price momentum.
    Never returns HOLD — that is the defining difference from Smart Scalp.
    """
    candles = []
    timeframe_used = "1m"

    # Try 1m first; fall back to 5m if not enough data
    try:
        candles = await provider.get_candles(symbol, "1m", 10)
    except Exception as e:
        logger.debug(f"[quick_scalp] 1m fetch failed for {symbol}: {e}")

    if not candles or len(candles) < 4:
        try:
            candles = await provider.get_candles(symbol, "5m", 10)
            timeframe_used = "5m"
        except Exception as e:
            logger.warning(f"[quick_scalp] 5m fetch also failed for {symbol}: {e}")

    if not candles or len(candles) < 3:
        return _minimal_signal(symbol, "Not enough candles — defaulting to BUY")

    # Work on the last 6 candles (or fewer if that's all we have)
    c = candles[-6:]
    closes = [x.close for x in c]
    opens  = [x.open  for x in c]
    highs  = [x.high  for x in c]
    lows   = [x.low   for x in c]

    current = closes[-1]

    # ── 1. Price velocity over last 3 candles ────────────────────────────────
    base = closes[-4] if len(closes) >= 4 else closes[0]
    velocity = ((current - base) / base * 100) if base and base != 0 else 0.0

    # ── 2. Latest candle body direction and body-to-range ratio ─────────────
    last_body  = closes[-1] - opens[-1]
    last_range = highs[-1] - lows[-1]
    body_ratio = abs(last_body) / last_range if last_range > 0 else 0.0

    # ── 3. Consecutive run: how many of last 5 moves agree ──────────────────
    moves = [1 if closes[i+1] > closes[i] else -1 for i in range(len(closes)-1)]
    bull_run = sum(1 for m in moves if m > 0)
    bear_run = sum(1 for m in moves if m < 0)

    # ── 4. Derive direction ──────────────────────────────────────────────────
    # Tie-break: velocity wins over run count
    if velocity >= 0:
        direction = "BUY"
        vel_score   = min(abs(velocity) * 160, 45)         # 0.28% → ~45 pts
        body_score  = body_ratio * 20 if last_body > 0 else body_ratio * 5
        run_score   = bull_run * 5 if bull_run >= 3 else 0
        alignment   = f"{bull_run}/{len(moves)} candles bullish"
    else:
        direction = "SELL"
        vel_score   = min(abs(velocity) * 160, 45)
        body_score  = body_ratio * 20 if last_body < 0 else body_ratio * 5
        run_score   = bear_run * 5 if bear_run >= 3 else 0
        alignment   = f"{bear_run}/{len(moves)} candles bearish"

    confidence = round(min(55.0 + vel_score + body_score + run_score, 93), 1)

    # ── 5. SL / TP based on recent range (ATR-approximate) ──────────────────
    recent_range = max(highs) - min(lows)
    atr_approx   = max(recent_range / max(len(c), 1), current * 0.0003)  # at least 0.03%

    if direction == "BUY":
        sl  = round(current - atr_approx * 1.2, 5)
        tp1 = round(current + atr_approx * 1.8, 5)
        tp2 = round(current + atr_approx * 2.8, 5)
    else:
        sl  = round(current + atr_approx * 1.2, 5)
        tp1 = round(current - atr_approx * 1.8, 5)
        tp2 = round(current - atr_approx * 2.8, 5)

    # ── 6. Human-readable explanation ───────────────────────────────────────
    dominant  = "bullish" if direction == "BUY" else "bearish"
    verb      = "gained" if velocity >= 0 else "dropped"
    explanation = (
        f"Quick Scalp: {symbol} {verb} {abs(velocity):.3f}% in the last 3 {timeframe_used} candles. "
        f"{alignment.capitalize()}. "
        f"Candle body occupies {body_ratio*100:.0f}% of bar range. "
        f"Entering {direction} immediately — TP1 {price_str(tp1)}, SL {price_str(sl)}. "
        f"Quick scalp — close within 1-3 candles."
    )

    signal = {
        "asset":        symbol,
        "direction":    direction,
        "confidence":   confidence,
        "entry_zone":   [round(current, 5)],
        "stop_loss":    sl,
        "take_profit":  [tp1, tp2],
        "timeframe":    timeframe_used,
        "engine":       "quick_scalp",
        "explanation":  explanation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Extra metadata for the frontend
        "velocity_pct": round(velocity, 4),
        "body_ratio":   round(body_ratio, 3),
        "candle_run":   bull_run if direction == "BUY" else bear_run,
    }
    logger.info(
        f"[quick_scalp] {symbol} → {direction} {confidence:.0f}% "
        f"vel={velocity:.3f}% body={body_ratio:.2f} run={run_score:.0f}"
    )
    return signal


def price_str(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v:.2f}" if v >= 100 else f"{v:.5f}"


def _minimal_signal(symbol: str, reason: str) -> dict:
    logger.warning(f"[quick_scalp] fallback for {symbol}: {reason}")
    return {
        "asset":        symbol,
        "direction":    "BUY",
        "confidence":   55.0,
        "entry_zone":   [],
        "stop_loss":    None,
        "take_profit":  [],
        "timeframe":    "1m",
        "engine":       "quick_scalp",
        "explanation":  f"Quick Scalp: {reason}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
