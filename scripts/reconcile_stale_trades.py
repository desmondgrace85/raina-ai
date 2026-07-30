#!/usr/bin/env python3
"""
reconcile_stale_trades.py
--------------------------
One-time (and re-runnable) cleanup: finds mt5_trades rows stuck at
status = 'open', 'sent', or 'pending' for more than STALE_HOURS hours,
attempts to reconcile each against MetaAPI, and marks them closed or
cancelled so they stop occupying the global open-trade slots.

Usage:
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/reconcile_stale_trades.py

Optional env:
    STALE_HOURS        — trades older than this many hours are considered stale (default: 4)
    DRY_RUN=1          — print what would be changed without writing anything
    METAAPI_TOKEN=...  — if set, attempts live position lookup before marking stale
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fsndqkacfizulovhfldz.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN", "")
STALE_HOURS = int(os.getenv("STALE_HOURS", "4"))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

if not SUPABASE_SERVICE_KEY:
    sys.exit("ERROR: SUPABASE_SERVICE_KEY env var is required.")


def _headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


async def fetch_stale_trades(client: httpx.AsyncClient) -> list[dict]:
    """Return all open/sent/pending trades that were created more than STALE_HOURS ago."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)).isoformat()
    r = await client.get(
        _url("mt5_trades"),
        headers=_headers(),
        params={
            "status": "in.(open,sent,pending)",
            "created_at": f"lt.{cutoff}",
            "select": "id,user_id,asset,direction,mt5_ticket,status,created_at,api_key",
        },
    )
    r.raise_for_status()
    return r.json()


async def fetch_metaapi_positions(metaapi_id: str) -> list[dict]:
    """Fetch live open positions from MetaAPI for a given account."""
    if not METAAPI_TOKEN:
        return []
    url = f"https://mt-client-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts/{metaapi_id}/positions"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"auth-token": METAAPI_TOKEN})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print(f"  [metaapi] WARNING: could not fetch positions: {e}")
    return []


async def get_metaapi_id_for_user(client: httpx.AsyncClient, user_id: str) -> str | None:
    r = await client.get(
        _url("mt5_accounts"),
        headers=_headers(),
        params={"user_id": f"eq.{user_id}", "select": "metaapi_id"},
    )
    if r.status_code == 200:
        rows = r.json()
        if rows:
            return rows[0].get("metaapi_id")
    return None


async def mark_trade(client: httpx.AsyncClient, trade_id: int, new_status: str, note: str):
    payload = {
        "status": new_status,
        "comment": note[:200],
    }
    if new_status == "closed":
        payload["closed_at"] = datetime.now(timezone.utc).isoformat()

    if DRY_RUN:
        print(f"  [DRY RUN] Would update trade {trade_id} → status={new_status}  ({note})")
        return

    r = await client.patch(
        _url("mt5_trades"),
        headers={**_headers(), "Prefer": "return=minimal"},
        params={"id": f"eq.{trade_id}"},
        content=json.dumps(payload),
    )
    if r.status_code in (200, 204):
        print(f"  ✓ Trade {trade_id} → {new_status}  ({note})")
    else:
        print(f"  ✗ Failed to update trade {trade_id}: {r.status_code} {r.text}")


async def main():
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Reconciling stale trades older than {STALE_HOURS}h …")
    print(f"Supabase: {SUPABASE_URL}")
    print(f"MetaAPI reconciliation: {'enabled' if METAAPI_TOKEN else 'disabled (set METAAPI_TOKEN to enable)'}")
    print()

    async with httpx.AsyncClient(timeout=15) as client:
        stale = await fetch_stale_trades(client)

    if not stale:
        print("No stale trades found. Nothing to do.")
        return

    print(f"Found {len(stale)} stale trade(s).\n")

    # Group by user so we only hit MetaAPI once per user
    by_user: dict[str, list[dict]] = {}
    for t in stale:
        by_user.setdefault(t["user_id"], []).append(t)

    async with httpx.AsyncClient(timeout=15) as client:
        for user_id, trades in by_user.items():
            print(f"User {user_id} — {len(trades)} stale trade(s)")

            # Try to get live positions from MetaAPI for this user
            live_tickets: set[int] = set()
            if METAAPI_TOKEN:
                metaapi_id = await get_metaapi_id_for_user(client, user_id)
                if metaapi_id:
                    positions = await fetch_metaapi_positions(metaapi_id)
                    live_tickets = {p.get("id") for p in positions if p.get("id")}
                    print(f"  MetaAPI live positions: {sorted(live_tickets) or 'none'}")

            for trade in trades:
                trade_id = trade["id"]
                ticket = trade.get("mt5_ticket")
                age_str = trade.get("created_at", "?")
                print(f"  Trade #{trade_id} | {trade['asset']} {trade['direction']} | "
                      f"ticket={ticket} | status={trade['status']} | created={age_str}")

                if ticket and live_tickets:
                    # We have MetaAPI data — check if the position is truly still open
                    if ticket in live_tickets:
                        print(f"    → Still open in MetaAPI — leaving as-is (monitor will close it)")
                        continue
                    else:
                        # Ticket not in live positions — broker closed it without us knowing
                        await mark_trade(
                            client, trade_id, "closed",
                            "reconcile: position no longer in MetaAPI — marked closed"
                        )
                elif ticket:
                    # We have a broker ticket but no MetaAPI data — likely already settled
                    await mark_trade(
                        client, trade_id, "closed",
                        f"reconcile: stale open with ticket {ticket} — marked closed (no MetaAPI data to verify)"
                    )
                else:
                    # No ticket at all — trade never reached the broker; cancel it
                    await mark_trade(
                        client, trade_id, "cancelled",
                        "reconcile: stuck pending/sent with no broker ticket — cancelled"
                    )

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
