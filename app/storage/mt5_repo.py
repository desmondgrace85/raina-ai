"""
MT5 account, settings, and trade storage.
user_id = mt5_login (broker account number) — no Telegram dependency.
"""
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.models.mt5 import AccountMode, RiskSettings, TradeOrder, TradeStatus
from app.storage.database import get_db

logger = logging.getLogger(__name__)


def _new_api_key() -> str:
    return uuid.uuid4().hex


# ── Account ────────────────────────────────────────────────────────────────────

async def get_mt5_account(user_id: str) -> dict | None:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_accounts WHERE user_id=?", (user_id,)
    )
    row = await cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


async def get_account_by_key(api_key: str) -> dict | None:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_accounts WHERE api_key=?", (api_key,)
    )
    row = await cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


async def upsert_mt5_account(user_id: str, account_mode: str = "demo") -> str:
    db = get_db()
    existing = await get_mt5_account(user_id)
    if existing:
        await db.execute(
            "UPDATE mt5_accounts SET account_mode=? WHERE user_id=?",
            (account_mode, user_id),
        )
        await db.commit()
        return existing["api_key"]
    api_key = _new_api_key()
    await db.execute(
        "INSERT INTO mt5_accounts (user_id, api_key, account_mode, is_connected) VALUES (?,?,?,0)",
        (user_id, api_key, account_mode),
    )
    await db.commit()
    return api_key


async def upsert_mt5_account_full(user_id: str, account_mode: str,
                                   metaapi_id: str, account_number: str,
                                   broker_name: str) -> str:
    db = get_db()
    existing = await get_mt5_account(user_id)
    now = datetime.utcnow().isoformat()
    if existing:
        await db.execute(
            """UPDATE mt5_accounts SET account_mode=?, metaapi_id=?,
               account_number=?, broker_name=?, is_connected=1, last_heartbeat=?
               WHERE user_id=?""",
            (account_mode, metaapi_id, account_number, broker_name, now, user_id),
        )
        await db.commit()
        return existing["api_key"]
    api_key = _new_api_key()
    await db.execute(
        """INSERT INTO mt5_accounts
           (user_id, api_key, metaapi_id, account_mode, account_number,
            broker_name, is_connected, last_heartbeat)
           VALUES (?,?,?,?,?,?,1,?)""",
        (user_id, api_key, metaapi_id, account_mode, account_number, broker_name, now),
    )
    await db.commit()
    return api_key


async def update_heartbeat(api_key: str, broker: str | None, account_number: str | None,
                           balance: float | None, equity: float | None, account_mode: str) -> bool:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """UPDATE mt5_accounts SET is_connected=1, last_heartbeat=?, broker_name=?,
           account_number=?, balance=?, equity=?, account_mode=? WHERE api_key=?""",
        (now, broker, account_number, balance, equity, account_mode, api_key),
    )
    await db.commit()
    return cur.rowcount > 0


async def update_metaapi_heartbeat(metaapi_id: str, broker: str | None,
                                    account_number: str | None, balance: float | None,
                                    equity: float | None, account_mode: str) -> bool:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """UPDATE mt5_accounts SET is_connected=1, last_heartbeat=?, broker_name=?,
           account_number=?, balance=?, equity=?, account_mode=? WHERE metaapi_id=?""",
        (now, broker, account_number, balance, equity, account_mode, metaapi_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def mark_disconnected_stale(minutes: int = 5) -> None:
    db = get_db()
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    await db.execute(
        "UPDATE mt5_accounts SET is_connected=0 WHERE last_heartbeat < ? OR last_heartbeat IS NULL",
        (cutoff,),
    )
    await db.commit()


async def set_ea_mode(user_id: str) -> None:
    db = get_db()
    await db.execute(
        "UPDATE mt5_accounts SET metaapi_id=NULL, is_connected=0 WHERE user_id=?",
        (user_id,),
    )
    await db.commit()


# ── Settings ───────────────────────────────────────────────────────────────────

async def get_settings(user_id: str) -> dict:
    db = get_db()
    cur = await db.execute(
        "SELECT settings_json FROM mt5_settings WHERE user_id=?", (user_id,)
    )
    row = await cur.fetchone()
    if not row:
        return RiskSettings().model_dump()
    return json.loads(row[0])


async def upsert_settings(user_id: str, settings: dict) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO mt5_settings (user_id, settings_json)
           VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json""",
        (user_id, json.dumps(settings)),
    )
    await db.commit()


# ── Scalping users ─────────────────────────────────────────────────────────────

async def get_scalping_users() -> list[dict]:
    """Return all connected users who have scalping enabled — no Telegram required."""
    db = get_db()
    cur = await db.execute(
        """SELECT a.user_id, a.api_key, a.metaapi_id, a.balance, a.account_mode,
                  s.settings_json
           FROM mt5_accounts a
           JOIN mt5_settings s ON a.user_id = s.user_id
           WHERE json_extract(s.settings_json, '$.scalping_enabled') = 1
             AND (a.metaapi_id IS NOT NULL OR a.is_connected = 1)"""
    )
    rows = await cur.fetchall()
    result = []
    for row in rows:
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
        try:
            d["settings"] = json.loads(d.pop("settings_json", "{}"))
        except Exception:
            d["settings"] = {}
        result.append(d)
    return result


# ── Trades ─────────────────────────────────────────────────────────────────────

async def insert_trade_order(order: TradeOrder) -> int:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """INSERT INTO mt5_trades
           (user_id, api_key, asset, direction, lot_size, entry_price,
            stop_loss, take_profit, confidence, timeframe, status, comment, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            order.user_id, order.api_key, order.asset, order.direction.value,
            order.lot_size, order.entry_price, order.stop_loss, order.take_profit,
            order.confidence, order.timeframe, TradeStatus.PENDING.value,
            order.comment, now,
        ),
    )
    await db.commit()
    return cur.lastrowid


async def get_pending_orders(api_key: str) -> list[dict]:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_trades WHERE api_key=? AND status='pending'",
        (api_key,),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    orders = [dict(zip(cols, r)) for r in rows]
    # Mark as sent
    if orders:
        ids = ",".join(str(o["id"]) for o in orders)
        await db.execute(f"UPDATE mt5_trades SET status='sent' WHERE id IN ({ids})")
        await db.commit()
    return orders


async def update_trade_opened(api_key: str, order_id: int,
                               ticket: int, open_price: float) -> bool:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """UPDATE mt5_trades SET status='open', mt5_ticket=?, open_price=?, opened_at=?
           WHERE id=? AND api_key=?""",
        (ticket, open_price, now, order_id, api_key),
    )
    await db.commit()
    return cur.rowcount > 0


async def close_trade(api_key: str, ticket: int,
                      close_price: float, profit: float) -> bool:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """UPDATE mt5_trades SET status='closed', close_price=?, profit=?, closed_at=?
           WHERE api_key=? AND mt5_ticket=? AND status='open'""",
        (close_price, profit, now, api_key, ticket),
    )
    await db.commit()
    return cur.rowcount > 0


async def get_open_trades(user_id: str) -> list[dict]:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_trades WHERE user_id=? AND status IN ('open','pending','sent') ORDER BY created_at DESC",
        (user_id,),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_trade_history(user_id: str, limit: int = 20) -> list[dict]:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_trades WHERE user_id=? AND status IN ('closed','failed','cancelled') ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_performance_summary(user_id: str) -> dict:
    db = get_db()
    cur = await db.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as losses,
                  SUM(profit) as total_profit
           FROM mt5_trades WHERE user_id=? AND status='closed'""",
        (user_id,),
    )
    row = await cur.fetchone()
    cols = [d[0] for d in cur.description]
    data = dict(zip(cols, row)) if row else {}
    total = data.get("total") or 0
    wins = data.get("wins") or 0
    total_profit = data.get("total_profit") or 0.0
    return {
        "total_trades": total,
        "wins": wins,
        "losses": data.get("losses") or 0,
        "win_rate": round((wins / total * 100) if total else 0, 1),
        "total_profit": total_profit,
    }


async def open_trade_count(user_id: str) -> int:
    db = get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM mt5_trades WHERE user_id=? AND status IN ('open','pending','sent')",
        (user_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def daily_loss_exceeded(user_id: str, settings: RiskSettings) -> bool:
    db = get_db()
    today = datetime.utcnow().date().isoformat()
    cur = await db.execute(
        "SELECT SUM(profit) FROM mt5_trades WHERE user_id=? AND status='closed' AND date(closed_at)=?",
        (user_id, today),
    )
    row = await cur.fetchone()
    daily_pnl = row[0] or 0.0
    if daily_pnl >= 0:
        return False
    acc_cur = await db.execute(
        "SELECT balance FROM mt5_accounts WHERE user_id=?", (user_id,)
    )
    acc_row = await acc_cur.fetchone()
    balance = acc_row[0] if acc_row and acc_row[0] else 1000.0
    max_loss = balance * (settings.max_daily_loss_percent / 100)
    return abs(daily_pnl) >= max_loss


async def mark_trade_failed(order_id: int, error: str = "") -> None:
    db = get_db()
    await db.execute(
        "UPDATE mt5_trades SET status='failed', comment=? WHERE id=?",
        (f"failed: {error}"[:200], order_id),
    )
    await db.commit()


async def cancel_user_pending_trades(user_id: str) -> int:
    db = get_db()
    cur = await db.execute(
        "UPDATE mt5_trades SET status='cancelled' WHERE user_id=? AND status IN ('pending','sent')",
        (user_id,),
    )
    await db.commit()
    return cur.rowcount
