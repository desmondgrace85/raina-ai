"""
Telegram user repository.

Subscription tiers:
  'none'      — not subscribed
  'standard'  — receives long-term (M15 and H1-H4) signals
  'premium'   — standard + MT5 scalp execution
"""
import logging
from datetime import datetime, timezone
from typing import Any

from app.storage.database import get_db

logger = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat()


async def upsert_user(
    telegram_id: int,
    telegram_name: str = "",
    email: str = "",
    subscription: str = "none",
    is_active: bool = False,
    rainx_token: str = "",
) -> None:
    db = get_db()
    existing = await get_user(telegram_id)
    now = _NOW()
    if existing:
        await db.execute(
            """UPDATE telegram_users
               SET telegram_name=?, email=?, subscription=?, is_active=?,
                   rainx_token=?, last_seen=?
               WHERE telegram_id=?""",
            (telegram_name, email, subscription, 1 if is_active else 0,
             rainx_token, now, telegram_id),
        )
    else:
        await db.execute(
            """INSERT INTO telegram_users
               (telegram_id, telegram_name, email, subscription, is_active,
                rainx_token, created_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (telegram_id, telegram_name, email, subscription,
             1 if is_active else 0, rainx_token, now, now),
        )
    await db.commit()


async def get_user(telegram_id: int) -> dict[str, Any] | None:
    db = get_db()
    cur = await db.execute(
        "SELECT * FROM telegram_users WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def touch_user(telegram_id: int) -> None:
    """Update last_seen timestamp."""
    db = get_db()
    await db.execute(
        "UPDATE telegram_users SET last_seen=? WHERE telegram_id=?",
        (_NOW(), telegram_id),
    )
    await db.commit()


async def set_subscription(telegram_id: int, subscription: str, is_active: bool) -> None:
    db = get_db()
    await db.execute(
        "UPDATE telegram_users SET subscription=?, is_active=? WHERE telegram_id=?",
        (subscription, 1 if is_active else 0, telegram_id),
    )
    await db.commit()


async def get_active_subscribers(tier: str = "standard") -> list[int]:
    """
    Return telegram_ids of all active users who qualify for the given tier.
    tier='standard' → subscription IN ('standard','premium') AND is_active=1
    tier='premium'  → subscription='premium' AND is_active=1
    """
    db = get_db()
    if tier == "premium":
        cur = await db.execute(
            "SELECT telegram_id FROM telegram_users WHERE subscription='premium' AND is_active=1"
        )
    else:
        cur = await db.execute(
            """SELECT telegram_id FROM telegram_users
               WHERE subscription IN ('standard','premium') AND is_active=1"""
        )
    rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_all_users_count() -> dict[str, int]:
    db = get_db()

    async def scalar(sql: str) -> int:
        cur = await db.execute(sql)
        row = await cur.fetchone()
        return row[0] if row else 0

    return {
        "total": await scalar("SELECT COUNT(*) FROM telegram_users"),
        "active_standard": await scalar(
            "SELECT COUNT(*) FROM telegram_users WHERE subscription IN ('standard','premium') AND is_active=1"
        ),
        "active_premium": await scalar(
            "SELECT COUNT(*) FROM telegram_users WHERE subscription='premium' AND is_active=1"
        ),
    }
