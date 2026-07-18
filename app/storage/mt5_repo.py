"""
MT5 account, settings, and trade storage.
"""
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.models.mt5 import AccountMode, RiskSettings, TradeOrder, TradeStatus
from app.storage.database import get_db

logger = logging.getLogger(__name__)


# ── API Key helpers ────────────────────────────────────────────────────────────

def _new_api_key() -> str:
    return uuid.uuid4().hex


# ── Account ────────────────────────────────────────────────────────────────────

async def get_mt5_account(telegram_id: int) -> dict | None:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_accounts WHERE telegram_id=?", (telegram_id,)
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


async def upsert_mt5_account(telegram_id: int, account_mode: str = "demo") -> str:
    db = get_db()
    existing = await get_mt5_account(telegram_id)
    if existing:
        await db.execute("UPDATE mt5_accounts SET account_mode=? WHERE telegram_id=?",
                         (account_mode, telegram_id))
        await db.commit()
        return existing["api_key"]
    api_key = _new_api_key()
    await db.execute(
        "INSERT INTO mt5_accounts (telegram_id, api_key, account_mode, is_connected) VALUES (?,?,?,0)",
        (telegram_id, api_key, account_mode),
    )
    await db.commit()
    return api_key


async def upsert_mt5_account_full(telegram_id: int, account_mode: str,
                                   metaapi_id: str, account_number: str,
                                   broker_name: str) -> str:
    db = get_db()
    existing = await get_mt5_account(telegram_id)
    now = datetime.utcnow().isoformat()
    if existing:
        await db.execute(
            """UPDATE mt5_accounts SET account_mode=?, metaapi_id=?,
               account_number=?, broker_name=?, is_connected=1, last_heartbeat=?
               WHERE telegram_id=?""",
            (account_mode, metaapi_id, account_number, broker_name, now, telegram_id),
        )
        await db.commit()
        return existing["api_key"]
    api_key = _new_api_key()
    await db.execute(
        """INSERT INTO mt5_accounts
           (telegram_id, api_key, metaapi_id, account_mode, account_number,
            broker_name, is_connected, last_heartbeat)
           VALUES (?,?,?,?,?,?,1,?)""",
        (telegram_id, api_key, metaapi_id, account_mode, account_number, broker_name, now),
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


async def set_ea_mode(telegram_id: int) -> None:
    """Mark account as EA (desktop) mode — clears metaapi_id."""
    db = get_db()
    await db.execute(
        "UPDATE mt5_accounts SET metaapi_id=NULL, is_connected=0 WHERE telegram_id=?",
        (telegram_id,),
    )
    await db.commit()


async def update_heartbeat_meta(metaapi_id: str, broker: str | None, account_number: str | None,
                                 balance: float | None, equity: float | None, account_mode: str) -> bool:
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
    """Mark accounts as disconnected if no heartbeat in `minutes`."""
    db = get_db()
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    await db.execute(
        "UPDATE mt5_accounts SET is_connected=0 WHERE last_heartbeat < ? OR last_heartbeat IS NULL",
        (cutoff,),
    )
    await db.commit()


# ── Settings ───────────────────────────────────────────────────────────────────

async def get_settings(telegram_id: int) -> dict:
    db = get_db()
    cur = await db.execute(
        "SELECT settings_json FROM mt5_settings WHERE telegram_id=?", (telegram_id,)
    )
    row = await cur.fetchone()
    if not row:
        return RiskSettings().model_dump()
    return json.loads(row[0])


async def upsert_settings(telegram_id: int, settings: dict) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO mt5_settings (telegram_id, settings_json)
           VALUES (?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET settings_json=excluded.settings_json""",
        (telegram_id, json.dumps(settings)),
    )
    await db.commit()


# ── Scalping users ─────────────────────────────────────────────────────────────

async def get_scalping_users() -> list[dict]:
    db = get_db()
    cur = await db.execute(
        """SELECT a.telegram_id, a.api_key, a.metaapi_id, a.balance, a.account_mode, s.settings_json
           FROM mt5_accounts a
           JOIN mt5_settings s ON a.telegram_id = s.telegram_id
           JOIN telegram_users u ON a.telegram_id = u.telegram_id
           WHERE u.subscription = 'premium'
             AND u.is_active = 1
             AND json_extract(s.settings_json, '$.scalping_enabled') = 1
             AND (a.metaapi_id IS NOT NULL OR a.is_connected = 1)"""
    )
    rows = await cur.fetchall()
    result = []
    for row in rows:
        result.append({
            "telegram_id": row[0],
            "api_key": row[1],
            "metaapi_id": row[2],
            "balance": row[3],
            "account_mode": row[4],
            "settings": json.loads(row[5]),
        })
    return result


# ── Trade orders ───────────────────────────────────────────────────────────────

async def insert_trade_order(order: TradeOrder) -> int:
    db = get_db()
    cur = await db.execute(
        """INSERT INTO mt5_trades
           (telegram_id, api_key, asset, direction, lot_size,
            entry_price, stop_loss, take_profit, confidence,
            timeframe, status, comment, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            order.telegram_id, order.api_key, order.asset,
            order.direction.value, order.lot_size,
            order.entry_price, order.stop_loss, order.take_profit,
            order.confidence, order.timeframe,
            TradeStatus.PENDING.value, order.comment,
            datetime.utcnow().isoformat(),
        ),
    )
    await db.commit()
    return cur.lastrowid


async def get_pending_orders(api_key: str) -> list[dict]:
    db = get_db()
    cur = await db.execute(
        """SELECT * FROM mt5_trades
           WHERE api_key=? AND status=?
           ORDER BY created_at ASC""",
        (api_key, TradeStatus.PENDING.value),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    # Mark as sent
    ids = [dict(zip(cols, r))["id"] for r in rows]
    if ids:
        placeholders = ",".join("?" * len(ids))
        await db.execute(
            f"UPDATE mt5_trades SET status=? WHERE id IN ({placeholders})",
            [TradeStatus.SENT.value] + ids,
        )
        await db.commit()
    return [dict(zip(cols, r)) for r in rows]


async def update_trade_opened(api_key: str, order_id: int, ticket: int, open_price: float) -> bool:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """UPDATE mt5_trades
           SET status=?, mt5_ticket=?, open_price=?, opened_at=?
           WHERE id=? AND api_key=?""",
        (TradeStatus.OPEN.value, ticket, open_price, now, order_id, api_key),
    )
    await db.commit()
    return cur.rowcount > 0


async def update_trade_closed(api_key: str, ticket: int, close_price: float, profit: float) -> bool:
    db = get_db()
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        """UPDATE mt5_trades
           SET status=?, close_price=?, profit=?, closed_at=?
           WHERE mt5_ticket=? AND api_key=?""",
        (TradeStatus.CLOSED.value, close_price, profit, now, ticket, api_key),
    )
    await db.commit()
    return cur.rowcount > 0


async def get_open_trades(telegram_id: int) -> list[dict]:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM mt5_trades WHERE telegram_id=? AND status=? ORDER BY opened_at DESC",
        (telegram_id, TradeStatus.OPEN.value),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_trade_history(telegram_id: int, limit: int = 20) -> list[dict]:
    db = get_db()
    cur = await db.execute(
        """SELECT * FROM mt5_trades WHERE telegram_id=? AND status='closed'
           ORDER BY closed_at DESC LIMIT ?""",
        (telegram_id, limit),
    )
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


async def get_performance_summary(telegram_id: int) -> dict:
    db = get_db()

    async def scalar(sql, params=()):
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else 0

    total = await scalar("SELECT COUNT(*) FROM mt5_trades WHERE telegram_id=?", (telegram_id,))
    closed = await scalar(
        "SELECT COUNT(*) FROM mt5_trades WHERE telegram_id=? AND status='closed'", (telegram_id,)
    )
    wins = await scalar(
        "SELECT COUNT(*) FROM mt5_trades WHERE telegram_id=? AND status='closed' AND profit>0",
        (telegram_id,),
    )
    total_profit_cur = await db.execute(
        "SELECT SUM(profit) FROM mt5_trades WHERE telegram_id=? AND status='closed'", (telegram_id,)
    )
    total_profit_row = await total_profit_cur.fetchone()
    total_profit = round(total_profit_row[0] or 0.0, 2)

    win_rate = round((wins / closed * 100) if closed > 0 else 0.0, 1)
    return {
        "total_trades": total,
        "closed_trades": closed,
        "wins": wins,
        "losses": closed - wins,
        "win_rate": win_rate,
        "total_profit": total_profit,
    }


async def open_trade_count(telegram_id: int) -> int:
    db = get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM mt5_trades WHERE telegram_id=? AND status IN ('open','pending','sent')",
        (telegram_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def daily_loss_exceeded(telegram_id: int, settings: RiskSettings) -> bool:
    db = get_db()
    today = datetime.utcnow().date().isoformat()
    cur = await db.execute(
        "SELECT SUM(profit) FROM mt5_trades WHERE telegram_id=? AND status='closed' AND date(closed_at)=?",
        (telegram_id, today),
    )
    row = await cur.fetchone()
    daily_pnl = row[0] or 0.0
    if daily_pnl >= 0:
        return False
    # Get balance to compare
    acc_cur = await db.execute("SELECT balance FROM mt5_accounts WHERE telegram_id=?", (telegram_id,))
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


async def cancel_user_pending_trades(telegram_id: int) -> int:
    db = get_db()
    cur = await db.execute(
        "UPDATE mt5_trades SET status='cancelled' WHERE telegram_id=? AND status IN ('pending','sent')",
        (telegram_id,),
    )
    await db.commit()
    return cur.rowcount
