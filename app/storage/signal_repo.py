"""
Signal repository — save and query signals from SQLite.

All public functions are async and use the connection managed by database.py.
"""
import json
import logging
from datetime import datetime
from typing import Any

from app.models.signal import Direction, RiskLevel, Signal
from app.storage.database import get_db

logger = logging.getLogger(__name__)


def _signal_to_row(sig: Signal) -> dict[str, Any]:
    return {
        "asset": sig.asset,
        "engine": sig.engine,
        "timeframe": sig.timeframe,
        "direction": sig.direction.value,
        "confidence": sig.confidence,
        "risk_level": str(sig.risk_level.value if hasattr(sig.risk_level, "value") else sig.risk_level),
        "risk_reward": sig.risk_reward_ratio,
        "entry_low": sig.entry_zone[0] if sig.entry_zone else None,
        "entry_high": sig.entry_zone[1] if sig.entry_zone else None,
        "stop_loss": sig.stop_loss,
        "take_profit": json.dumps(sig.take_profit),
        "explanation": sig.explanation,
        "generated_at": sig.generated_at.isoformat(),
        "sent_telegram": 0,
    }


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    tp_raw = d.get("take_profit") or "[]"
    try:
        d["take_profit"] = json.loads(tp_raw)
    except (ValueError, TypeError):
        d["take_profit"] = []
    entry_low = d.pop("entry_low", None)
    entry_high = d.pop("entry_high", None)
    d["entry_zone"] = [entry_low, entry_high] if entry_low is not None else None
    return d


async def save_signal(sig: Signal, sent_telegram: bool = False) -> int:
    """Persist a signal. Returns the new row id."""
    db = get_db()
    row = _signal_to_row(sig)
    row["sent_telegram"] = 1 if sent_telegram else 0
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    try:
        cursor = await db.execute(
            f"INSERT INTO signals ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        await db.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error(f"Failed to save signal: {e}")
        return -1


async def mark_sent_telegram(row_id: int) -> None:
    db = get_db()
    await db.execute("UPDATE signals SET sent_telegram = 1 WHERE id = ?", (row_id,))
    await db.commit()


async def get_signals(
    asset: str | None = None,
    engine: str | None = None,
    direction: str | None = None,
    only_actionable: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query signal history with optional filters."""
    db = get_db()
    clauses = []
    params: list[Any] = []

    if asset:
        clauses.append("asset = ?")
        params.append(asset.upper())
    if engine:
        clauses.append("engine = ?")
        params.append(engine)
    if direction:
        clauses.append("direction = ?")
        params.append(direction.upper())
    if only_actionable:
        clauses.append("direction != 'HOLD'")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT * FROM signals
        {where}
        ORDER BY generated_at DESC
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_signal_stats() -> dict[str, Any]:
    """Summary statistics for the history dashboard."""
    db = get_db()

    async def scalar(sql: str, params=()) -> Any:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else 0

    total = await scalar("SELECT COUNT(*) FROM signals")
    buys = await scalar("SELECT COUNT(*) FROM signals WHERE direction = 'BUY'")
    sells = await scalar("SELECT COUNT(*) FROM signals WHERE direction = 'SELL'")
    holds = await scalar("SELECT COUNT(*) FROM signals WHERE direction = 'HOLD'")
    lt_count = await scalar("SELECT COUNT(*) FROM signals WHERE engine = 'long_term'")
    scalp_count = await scalar("SELECT COUNT(*) FROM signals WHERE engine = 'scalp'")
    avg_conf_cur = await db.execute("SELECT AVG(confidence) FROM signals WHERE direction != 'HOLD'")
    avg_conf_row = await avg_conf_cur.fetchone()
    avg_conf = round(avg_conf_row[0] or 0, 1)

    # Top symbols by signal count
    top_cur = await db.execute(
        "SELECT asset, COUNT(*) as cnt FROM signals GROUP BY asset ORDER BY cnt DESC LIMIT 5"
    )
    top_rows = await top_cur.fetchall()
    top_assets = [{"asset": r[0], "count": r[1]} for r in top_rows]

    return {
        "total_signals": total,
        "by_direction": {"BUY": buys, "SELL": sells, "HOLD": holds},
        "by_engine": {"long_term": lt_count, "scalp": scalp_count},
        "avg_confidence_actionable": avg_conf,
        "top_assets": top_assets,
    }
