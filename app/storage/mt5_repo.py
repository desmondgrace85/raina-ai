"""
MT5 account, settings, and trade storage — Supabase backend.

Replaces the SQLite-backed version. All data is stored in Supabase
Postgres via the PostgREST REST API so it survives Railway redeploys.

Tables required (run migrations/001_mt5_tables.sql once in Supabase SQL editor):
  mt5_accounts   — per-user MT5 EA / MetaAPI connection state
  mt5_settings   — per-user risk/scalping settings (JSON blob)
  mt5_trades     — trade orders, open positions, closed history
"""
import json
import uuid
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from app.models.mt5 import AccountMode, RiskSettings, TradeOrder, TradeStatus

logger = logging.getLogger(__name__)

_SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fsndqkacfizulovhfldz.supabase.co")
_SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# We never import get_db — this module is entirely Supabase-backed.


def _headers() -> dict:
    return {
        "apikey": _SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {_SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table: str) -> str:
    return f"{_SUPABASE_URL}/rest/v1/{table}"


def _new_api_key() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.utcnow().isoformat()


def _is_table_missing(r: httpx.Response) -> bool:
    """Return True when Supabase PostgREST can't find the table (PGRST205)."""
    if r.status_code == 404:
        try:
            return r.json().get("code") == "PGRST205"
        except Exception:
            return True
    return False


async def _get(table: str, params: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(_url(table), headers=_headers(), params=params)
        if _is_table_missing(r):
            logger.warning(
                "Supabase table '%s' not found — run migrations/001_mt5_tables.sql", table
            )
            return []
        r.raise_for_status()
        return r.json()


async def _post(table: str, data: dict, upsert: bool = False) -> list[dict]:
    headers = _headers()
    if upsert:
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(_url(table), headers=headers, json=data)
        if _is_table_missing(r):
            logger.warning(
                "Supabase table '%s' not found — run migrations/001_mt5_tables.sql", table
            )
            return []
        r.raise_for_status()
        return r.json()


async def _patch(table: str, params: dict, data: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.patch(_url(table), headers=_headers(), params=params, json=data)
        if _is_table_missing(r):
            logger.warning(
                "Supabase table '%s' not found — run migrations/001_mt5_tables.sql", table
            )
            return []
        r.raise_for_status()
        return r.json()


# ── Account ────────────────────────────────────────────────────────────────────

async def get_mt5_account(user_id: str) -> dict | None:
    rows = await _get("mt5_accounts", {"user_id": f"eq.{user_id}", "limit": "1"})
    return rows[0] if rows else None


async def get_account_by_key(api_key: str) -> dict | None:
    rows = await _get("mt5_accounts", {"api_key": f"eq.{api_key}", "limit": "1"})
    return rows[0] if rows else None


async def upsert_mt5_account(user_id: str, account_mode: str = "demo") -> str:
    existing = await get_mt5_account(user_id)
    if existing:
        await _patch("mt5_accounts", {"user_id": f"eq.{user_id}"},
                     {"account_mode": account_mode})
        return existing["api_key"]
    api_key = _new_api_key()
    await _post("mt5_accounts", {
        "user_id": user_id,
        "api_key": api_key,
        "account_mode": account_mode,
        "is_connected": False,
    })
    return api_key


async def upsert_mt5_account_full(user_id: str, account_mode: str,
                                   metaapi_id: str, account_number: str,
                                   broker_name: str) -> str:
    existing = await get_mt5_account(user_id)
    now = _now()
    if existing:
        await _patch("mt5_accounts", {"user_id": f"eq.{user_id}"}, {
            "account_mode": account_mode,
            "metaapi_id": metaapi_id,
            "account_number": account_number,
            "broker_name": broker_name,
            "is_connected": True,
            "last_heartbeat": now,
        })
        return existing["api_key"]
    api_key = _new_api_key()
    await _post("mt5_accounts", {
        "user_id": user_id,
        "api_key": api_key,
        "metaapi_id": metaapi_id,
        "account_mode": account_mode,
        "account_number": account_number,
        "broker_name": broker_name,
        "is_connected": True,
        "last_heartbeat": now,
    })
    return api_key


async def update_heartbeat(api_key: str, broker: str | None, account_number: str | None,
                           balance: float | None, equity: float | None, account_mode: str) -> bool:
    rows = await _patch("mt5_accounts", {"api_key": f"eq.{api_key}"}, {
        "is_connected": True,
        "last_heartbeat": _now(),
        "broker_name": broker,
        "account_number": account_number,
        "balance": balance,
        "equity": equity,
        "account_mode": account_mode,
    })
    return len(rows) > 0


async def update_metaapi_heartbeat(metaapi_id: str, broker: str | None,
                                    account_number: str | None, balance: float | None,
                                    equity: float | None, account_mode: str) -> bool:
    rows = await _patch("mt5_accounts", {"metaapi_id": f"eq.{metaapi_id}"}, {
        "is_connected": True,
        "last_heartbeat": _now(),
        "broker_name": broker,
        "account_number": account_number,
        "balance": balance,
        "equity": equity,
        "account_mode": account_mode,
    })
    return len(rows) > 0


async def mark_disconnected_stale(minutes: int = 5) -> None:
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    async with httpx.AsyncClient(timeout=10) as client:
        headers = _headers()
        r1 = await client.patch(_url("mt5_accounts"), headers=headers,
                                params={"last_heartbeat": f"lt.{cutoff}"},
                                json={"is_connected": False})
        if not _is_table_missing(r1):
            r1.raise_for_status()
        r2 = await client.patch(_url("mt5_accounts"), headers=headers,
                                params={"last_heartbeat": "is.null"},
                                json={"is_connected": False})
        if not _is_table_missing(r2):
            r2.raise_for_status()


async def set_ea_mode(user_id: str) -> None:
    await _patch("mt5_accounts", {"user_id": f"eq.{user_id}"},
                 {"metaapi_id": None, "is_connected": False})


# ── Settings ───────────────────────────────────────────────────────────────────

async def get_settings(user_id: str) -> dict:
    rows = await _get("mt5_settings", {"user_id": f"eq.{user_id}", "limit": "1"})
    if not rows:
        return RiskSettings().model_dump()
    return json.loads(rows[0]["settings_json"])


async def upsert_settings(user_id: str, settings: dict) -> None:
    await _post("mt5_settings",
                {"user_id": user_id, "settings_json": json.dumps(settings)},
                upsert=True)


# ── Scalping users ─────────────────────────────────────────────────────────────

async def get_scalping_users() -> list[dict]:
    """Return connected users with scalping_enabled=true in their settings."""
    async with httpx.AsyncClient(timeout=10) as client:
        acc_r = await client.get(
            _url("mt5_accounts"),
            headers=_headers(),
            params={"is_connected": "eq.true",
                    "select": "user_id,api_key,metaapi_id,balance,account_mode"},
        )
        if _is_table_missing(acc_r):
            return []
        acc_r.raise_for_status()
        accounts = {a["user_id"]: a for a in acc_r.json()}

        if not accounts:
            return []

        user_ids = ",".join(accounts.keys())
        set_r = await client.get(
            _url("mt5_settings"),
            headers=_headers(),
            params={"user_id": f"in.({user_ids})",
                    "select": "user_id,settings_json"},
        )
        if _is_table_missing(set_r):
            return []
        set_r.raise_for_status()
        settings_map = {s["user_id"]: s["settings_json"] for s in set_r.json()}

    result = []
    for user_id, acc in accounts.items():
        raw = settings_map.get(user_id, "{}")
        try:
            s = json.loads(raw)
        except Exception:
            s = {}
        if s.get("scalping_enabled"):
            acc["settings"] = s
            result.append(acc)
    return result


# ── Trades ─────────────────────────────────────────────────────────────────────

async def insert_trade_order(order: TradeOrder) -> int:
    rows = await _post("mt5_trades", {
        "user_id": order.user_id,
        "api_key": order.api_key,
        "asset": order.asset,
        "direction": order.direction.value,
        "lot_size": order.lot_size,
        "entry_price": order.entry_price,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "confidence": order.confidence,
        "timeframe": order.timeframe,
        "status": TradeStatus.PENDING.value,
        "comment": order.comment,
        "created_at": _now(),
    })
    return rows[0]["id"] if rows else 0


async def get_pending_orders(api_key: str) -> list[dict]:
    rows = await _get("mt5_trades", {
        "api_key": f"eq.{api_key}",
        "status": "eq.pending",
        "order": "created_at.asc",
    })
    if rows:
        ids = ",".join(str(r["id"]) for r in rows)
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                _url("mt5_trades"),
                headers=_headers(),
                params={"id": f"in.({ids})"},
                json={"status": "sent"},
            )
    return rows


async def update_trade_opened(api_key: str, order_id: int,
                               ticket: int, open_price: float) -> bool:
    rows = await _patch("mt5_trades",
                        {"id": f"eq.{order_id}", "api_key": f"eq.{api_key}"},
                        {"status": "open", "mt5_ticket": ticket,
                         "open_price": open_price, "opened_at": _now()})
    return len(rows) > 0


async def close_trade(api_key: str, ticket: int,
                      close_price: float, profit: float) -> bool:
    rows = await _patch("mt5_trades",
                        {"api_key": f"eq.{api_key}",
                         "mt5_ticket": f"eq.{ticket}",
                         "status": "eq.open"},
                        {"status": "closed", "close_price": close_price,
                         "profit": profit, "closed_at": _now()})
    return len(rows) > 0


async def get_open_trades(user_id: str) -> list[dict]:
    return await _get("mt5_trades", {
        "user_id": f"eq.{user_id}",
        "status": "in.(open,pending,sent)",
        "order": "created_at.desc",
    })


async def get_trade_history(user_id: str, limit: int = 20) -> list[dict]:
    return await _get("mt5_trades", {
        "user_id": f"eq.{user_id}",
        "status": "in.(closed,failed,cancelled)",
        "order": "created_at.desc",
        "limit": str(limit),
    })


async def get_performance_summary(user_id: str) -> dict:
    rows = await _get("mt5_trades", {
        "user_id": f"eq.{user_id}",
        "status": "eq.closed",
        "select": "profit",
    })
    total = len(rows)
    wins = sum(1 for r in rows if (r.get("profit") or 0) > 0)
    losses = sum(1 for r in rows if (r.get("profit") or 0) < 0)
    total_profit = sum((r.get("profit") or 0) for r in rows)
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total * 100) if total else 0, 1),
        "total_profit": total_profit,
    }


async def open_trade_count(user_id: str) -> int:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _url("mt5_trades"),
            headers={**_headers(), "Prefer": "count=exact",
                     "Range-Unit": "items", "Range": "0-0"},
            params={"user_id": f"eq.{user_id}",
                    "status": "in.(open,pending,sent)"},
        )
        if _is_table_missing(r):
            return 0
        content_range = r.headers.get("content-range", "0/0")
        try:
            return int(content_range.split("/")[1])
        except Exception:
            return len(r.json()) if r.status_code == 200 else 0


async def open_trade_count_for_symbol(user_id: str, symbol: str) -> int:
    """Count open/pending/sent trades for a specific symbol (canonical form)."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _url("mt5_trades"),
            headers={**_headers(), "Prefer": "count=exact",
                     "Range-Unit": "items", "Range": "0-0"},
            params={"user_id": f"eq.{user_id}",
                    "asset": f"eq.{symbol}",
                    "status": "in.(open,pending,sent)"},
        )
        if _is_table_missing(r):
            return 0
        content_range = r.headers.get("content-range", "0/0")
        try:
            return int(content_range.split("/")[1])
        except Exception:
            return len(r.json()) if r.status_code == 200 else 0


async def daily_loss_exceeded(user_id: str, settings: RiskSettings) -> bool:
    today = datetime.utcnow().date().isoformat()
    rows = await _get("mt5_trades", {
        "user_id": f"eq.{user_id}",
        "status": "eq.closed",
        "closed_at": f"gte.{today}T00:00:00",
        "select": "profit",
    })
    daily_pnl = sum((r.get("profit") or 0) for r in rows)
    if daily_pnl >= 0:
        return False
    acc = await get_mt5_account(user_id)
    balance = (acc.get("balance") or 1000.0) if acc else 1000.0
    max_loss = balance * (settings.max_daily_loss_percent / 100)
    return abs(daily_pnl) >= max_loss


async def mark_trade_failed(order_id: int, error: str = "") -> None:
    await _patch("mt5_trades", {"id": f"eq.{order_id}"},
                 {"status": "failed", "comment": f"failed: {error}"[:200]})


async def cancel_user_pending_trades(user_id: str) -> int:
    rows = await _patch("mt5_trades",
                        {"user_id": f"eq.{user_id}",
                         "status": "in.(pending,sent)"},
                        {"status": "cancelled"})
    return len(rows)
