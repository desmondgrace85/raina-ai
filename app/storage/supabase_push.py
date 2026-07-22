"""
    supabase_push.py — Push signals and notifications to Supabase (replaces Telegram delivery).
    Signals go to: user_signals table
    Notifications go to: user_notifications table
    """
    import os
    import logging
    from datetime import datetime, timezone
    from typing import Any
    import httpx

    logger = logging.getLogger(__name__)

    SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fsndqkacfizulovhfldz.supabase.co")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


    def _headers() -> dict:
      return {
          "apikey": SUPABASE_SERVICE_KEY,
          "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
          "Content-Type": "application/json",
          "Prefer": "return=minimal",
      }


    async def get_symbol_to_users() -> dict[str, list[str]]:
      """Return {SYMBOL: [user_id, ...]} from user_active_markets table."""
      if not SUPABASE_SERVICE_KEY:
          logger.warning("SUPABASE_SERVICE_KEY not set — using default watchlist")
          return {}
      try:
          async with httpx.AsyncClient(timeout=10) as client:
              r = await client.get(
                  f"{SUPABASE_URL}/rest/v1/user_active_markets",
                  params={"select": "user_id,symbol"},
                  headers=_headers(),
              )
              rows = r.json() if r.status_code == 200 else []
      except Exception as e:
          logger.warning(f"get_symbol_to_users error: {e}")
          return {}

      result: dict[str, list[str]] = {}
      for row in rows:
          sym = (row.get("symbol") or "").upper()
          uid = row.get("user_id")
          if sym and uid:
              result.setdefault(sym, []).append(uid)
      return result


    async def push_signal_to_supabase(sig: Any, target_users: list[str], timeframe: str) -> int:
      """
      Insert signal row per user into user_signals table, and a notification row
      into user_notifications. Returns number of users notified.
      """
      if not SUPABASE_SERVICE_KEY:
          logger.warning("SUPABASE_SERVICE_KEY not set — cannot push to Supabase")
          return 0
      if not target_users:
          logger.debug(f"No users for {sig.asset} — skipping push")
          return 0

      direction = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
      now = datetime.now(timezone.utc).isoformat()

      signal_rows = []
      notif_rows = []
      for uid in target_users:
          signal_rows.append({
              "user_id": uid,
              "symbol": sig.asset,
              "timeframe": timeframe,
              "direction": direction,
              "entry": float(sig.entry) if sig.entry else None,
              "sl": float(sig.sl) if sig.sl else None,
              "tp": float(sig.tp) if sig.tp else None,
              "confidence": float(sig.confidence),
              "status": "active",
              "created_at": now,
          })
          tf_label = {"m15": "15 Min", "h1": "1 Hour", "h4": "4 Hour", "scalp": "Scalp"}.get(timeframe.lower(), timeframe.upper())
          arrow = "🟢" if direction == "BUY" else "🔴"
          notif_rows.append({
              "user_id": uid,
              "title": f"{arrow} {sig.asset} Signal — {tf_label}",
              "body": f"{direction} | Entry: {sig.entry} | SL: {sig.sl} | TP: {sig.tp} | {sig.confidence:.0f}% confidence",
              "type": "signal",
              "is_read": False,
              "created_at": now,
          })

      pushed = 0
      try:
          async with httpx.AsyncClient(timeout=15) as client:
              r1 = await client.post(f"{SUPABASE_URL}/rest/v1/user_signals", json=signal_rows, headers=_headers())
              r2 = await client.post(f"{SUPABASE_URL}/rest/v1/user_notifications", json=notif_rows, headers=_headers())
              if r1.status_code in (200, 201, 204):
                  pushed = len(target_users)
              else:
                  logger.warning(f"user_signals insert failed: {r1.status_code} {r1.text[:200]}")
              if r2.status_code not in (200, 201, 204):
                  logger.warning(f"user_notifications insert failed: {r2.status_code} {r2.text[:200]}")
      except Exception as e:
          logger.warning(f"Supabase push error: {e}")

      return pushed


    async def push_signal_outcome_to_supabase(user_id: str, symbol: str, result: str, pips: float) -> None:
      """
      Called when a signal hits TP (win) or SL (loss).
      Updates the signal status and fires a notification.
      """
      if not SUPABASE_SERVICE_KEY:
          return
      now = datetime.now(timezone.utc).isoformat()
      if result == "win":
          title = f"✅ {symbol} +{pips:.0f} PIPS"
          body  = f"{symbol} hit Take Profit! +{pips:.0f} pips 🚀"
          status = "win"
      else:
          title = f"🔴 {symbol} Trade Closed"
          body  = f"Stop loss hit. Analyzing next setup for {symbol}…"
          status = "loss"

      try:
          async with httpx.AsyncClient(timeout=10) as client:
              # Update signal status
              await client.patch(
                  f"{SUPABASE_URL}/rest/v1/user_signals",
                  params={"user_id": f"eq.{user_id}", "symbol": f"eq.{symbol}", "status": "eq.active"},
                  json={"status": status, "closed_at": now, "pips": pips},
                  headers=_headers(),
              )
              # Push notification
              await client.post(
                  f"{SUPABASE_URL}/rest/v1/user_notifications",
                  json={"user_id": user_id, "title": title, "body": body, "type": result, "is_read": False, "created_at": now},
                  headers=_headers(),
              )
      except Exception as e:
          logger.warning(f"push_signal_outcome error: {e}")
    