"""
MT5 REST API — supports both EA (desktop) and MetaAPI (mobile) modes.
user_id = mt5_login (broker account number) — no Telegram dependency.

EA endpoints (keyed by api_key, called by the MQL5 EA):
  GET  /mt5/ea/poll/{api_key}  — EA polls for pending orders
  POST /mt5/ea/confirm         — EA confirms trade opened
  POST /mt5/ea/close           — EA reports trade closed
  POST /mt5/ea/heartbeat       — EA sends account state

Website sync endpoints:
  POST /mt5/connect/metaapi    — provision MetaAPI cloud account
  GET  /mt5/account/{user_id}  — poll connection status (fast — returns stored value)
  POST /mt5/balance/refresh/{user_id} — force a background balance re-fetch from MetaAPI
  POST /mt5/settings           — save risk settings
  GET  /mt5/settings/{user_id}
  GET  /mt5/trades/{user_id}
  GET  /mt5/history/{user_id}
  GET  /mt5/performance/{user_id}
  POST /mt5/scalping/toggle    — enable/disable background auto-scalping
  POST /mt5/scalping/execute   — immediately execute a single scalp trade via MetaAPI

Balance sync strategy
---------------------
Exness (and other brokers) take 60-180 seconds for a freshly deployed MetaAPI
terminal to fully synchronise with the broker.  We must NOT block the API
response waiting for that sync.  Instead:

  • GET /mt5/account/{id}   → always returns the stored value immediately.
  • A background coroutine  → retries with a generous (90 s) wait_synchronized
    timeout until the balance is persisted.  Retries up to 8 times with
    exponential-ish back-off (total window ~90 minutes).
  • POST /mt5/balance/refresh/{id} → lets the frontend trigger a fresh
    background sync on demand (e.g. when user taps "Sync Balance").
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.mt5 import TradeClose, TradeResult, EAHeartbeat
from app.mt5.symbol_utils import normalize_for_data, detect_broker_suffix, to_broker_symbol
from app.storage import mt5_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mt5", tags=["mt5"])


# ── Balance sync helper ────────────────────────────────────────────────────────

async def _sync_balance_with_retry(
    metaapi_id: str,
    user_id: str,
    account_mode: str,
    broker_name: str,
    login: str,
    max_attempts: int = 8,
) -> None:
    """
    Background coroutine: keep trying to fetch the live balance from MetaAPI
    until it succeeds, then persist it to Supabase.

    Retry schedule (initial delay before first attempt, then between attempts):
      90s → 120s → 180s → 300s → 300s → 600s → 600s → 600s
    This gives a ~50-minute window — long enough for most broker deployments.
    """
    from app.mt5.metaapi_client import get_account_info

    delays = [90, 120, 180, 300, 300, 600, 600, 600]

    for attempt, delay in enumerate(delays[:max_attempts], 1):
        await asyncio.sleep(delay)
        try:
            # Use a 90 s wait_synchronized — Exness often needs 60-90 s on first deploy
            info = await get_account_info(metaapi_id, sync_timeout_seconds=90)

            if info.get("connected") and info.get("balance") is not None:
                await mt5_repo.update_metaapi_heartbeat(
                    metaapi_id=metaapi_id,
                    broker=info.get("broker") or broker_name,
                    account_number=info.get("login") or login,
                    balance=info["balance"],
                    equity=info.get("equity"),
                    account_mode=account_mode,
                )

                # Store currency in settings (flexible JSON — no schema change needed)
                currency = info.get("currency", "")
                if currency:
                    existing = await mt5_repo.get_settings(user_id)
                    if existing.get("account_currency") != currency:
                        existing["account_currency"] = currency
                        await mt5_repo.upsert_settings(user_id, existing)

                logger.info(
                    f"[balance-sync] ✓ user={user_id} attempt={attempt} "
                    f"balance={info['balance']} {currency} broker={info.get('broker')}"
                )
                return  # success — stop retrying

            logger.warning(
                f"[balance-sync] attempt {attempt}/{max_attempts} for user={user_id}: "
                f"connected={info.get('connected')} error={str(info.get('error',''))[:120]}"
            )

        except Exception as ex:
            logger.warning(
                f"[balance-sync] attempt {attempt}/{max_attempts} for user={user_id} raised: {ex}"
            )

    logger.error(
        f"[balance-sync] all {max_attempts} attempts failed for user={user_id} "
        f"metaapi_id={metaapi_id} — balance stays 0 until next refresh"
    )


# ── EA endpoints ───────────────────────────────────────────────────────────────

@router.get("/ea/poll/{api_key}")
async def ea_poll(api_key: str):
    account = await mt5_repo.get_account_by_key(api_key)
    if not account:
        raise HTTPException(status_code=404, detail="Unknown api_key")
    orders = await mt5_repo.get_pending_orders(api_key)
    return {"orders": orders}


@router.post("/ea/confirm")
async def ea_confirm(payload: TradeResult):
    if not payload.success:
        await mt5_repo.mark_trade_failed(payload.order_id, payload.error or "EA rejected")
        return {"ok": False}
    await mt5_repo.update_trade_opened(
        payload.api_key, payload.order_id,
        payload.ticket, payload.open_price or 0.0,
    )
    return {"ok": True}


@router.post("/ea/close")
async def ea_close(payload: TradeClose):
    await mt5_repo.close_trade(
        payload.api_key, payload.mt5_ticket,
        payload.close_price, payload.profit,
    )
    return {"ok": True}


@router.post("/ea/heartbeat")
async def ea_heartbeat(payload: EAHeartbeat):
    ok = await mt5_repo.update_heartbeat(
        payload.api_key, payload.broker_name,
        payload.account_number, payload.balance,
        payload.equity, payload.account_mode,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown api_key")
    return {"ok": True}


# ── MetaAPI (cloud) connect ───────────────────────────────────────────────────

class MetaApiConnectPayload(BaseModel):
    mt5_login: str
    mt5_password: str
    mt5_server: str
    account_mode: str = 'demo'
    name: str = 'RainaAI User'
    # Optional: the exact symbol as it appears on the user's MT5 terminal.
    # Used to auto-detect their broker's symbol suffix (e.g. "BTCUSDZ" → suffix "Z").
    sample_symbol: Optional[str] = None


@router.post('/connect/metaapi')
async def connect_metaapi(payload: MetaApiConnectPayload):
    """
    Provision the MetaAPI cloud account and return immediately.
    A background coroutine starts retrying balance sync right away —
    balance will appear on the dashboard within 1-3 minutes.
    """
    from app.mt5.metaapi_client import provision_account

    user_id = payload.mt5_login

    # Detect broker symbol suffix from sample symbol (e.g. BTCUSDZ → "Z")
    broker_suffix = ""
    if payload.sample_symbol:
        broker_suffix = detect_broker_suffix(payload.sample_symbol)
        logger.info(
            f"Detected broker suffix '{broker_suffix}' from '{payload.sample_symbol}' "
            f"for user {user_id}"
        )

    try:
        metaapi_id = await provision_account(
            mt5_login=payload.mt5_login,
            mt5_password=payload.mt5_password,
            mt5_server=payload.mt5_server,
            account_mode=payload.account_mode,
            name=payload.name,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'MetaAPI provisioning failed: {str(e)}')

    api_key = await mt5_repo.upsert_mt5_account_full(
        user_id=user_id,
        account_mode=payload.account_mode,
        metaapi_id=metaapi_id,
        account_number=payload.mt5_login,
        broker_name=payload.mt5_server,
    )

    # Persist broker suffix and initial settings
    initial_settings = await mt5_repo.get_settings(user_id)
    if broker_suffix:
        initial_settings["broker_symbol_suffix"] = broker_suffix
    initial_settings["metaapi_id"] = metaapi_id
    await mt5_repo.upsert_settings(user_id, initial_settings)

    # Start background balance sync — retries for up to ~90 min
    asyncio.create_task(_sync_balance_with_retry(
        metaapi_id=metaapi_id,
        user_id=user_id,
        account_mode=payload.account_mode,
        broker_name=payload.mt5_server,
        login=payload.mt5_login,
    ))

    return {
        'connected': True,
        'user_id': user_id,
        'api_key': api_key,
        'metaapi_id': metaapi_id,
        'broker_name': payload.mt5_server,
        'account_number': payload.mt5_login,
        'account_mode': payload.account_mode,
        'broker_symbol_suffix': broker_suffix,
        'balance_status': 'syncing',  # frontend shows a spinner until balance arrives
    }


# ── Account status ─────────────────────────────────────────────────────────────

@router.get('/account/{user_id}')
async def get_account(user_id: str):
    """
    Return the stored MT5 account record immediately (fast path).
    Balance is populated asynchronously by the background sync coroutine.
    Use POST /mt5/balance/refresh/{user_id} to force a fresh sync.
    """
    account = await mt5_repo.get_mt5_account(user_id)
    if not account:
        raise HTTPException(status_code=404, detail='Account not found')

    # Enrich with account_currency from settings (stored during balance sync)
    try:
        settings_data = await mt5_repo.get_settings(user_id)
        currency = settings_data.get("account_currency", "")
        if currency and not account.get("currency"):
            account = {**account, "currency": currency}
    except Exception:
        pass

    return account


# ── Balance refresh (on-demand trigger) ───────────────────────────────────────

@router.post('/balance/refresh/{user_id}')
async def refresh_balance(user_id: str):
    """
    Trigger an immediate background balance re-fetch from MetaAPI.
    Returns instantly — balance will update within 60-120 seconds.
    The frontend should poll GET /mt5/account/{user_id} to see the updated value.
    """
    account = await mt5_repo.get_mt5_account(user_id)
    if not account:
        raise HTTPException(status_code=404, detail='Account not found')

    metaapi_id = account.get("metaapi_id")
    if not metaapi_id:
        raise HTTPException(
            status_code=400,
            detail="MetaAPI not connected — balance sync only works for MetaAPI accounts"
        )

    # Fire-and-forget — balance will appear on next account poll
    asyncio.create_task(_sync_balance_with_retry(
        metaapi_id=metaapi_id,
        user_id=user_id,
        account_mode=account.get("account_mode", "demo"),
        broker_name=account.get("broker_name", ""),
        login=account.get("account_number", user_id),
        max_attempts=3,  # On-demand: 3 quick attempts (90s → 120s → 180s)
    ))

    return {
        "ok": True,
        "message": "Balance sync started. Check back in 60-120 seconds.",
        "metaapi_id": metaapi_id,
    }


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsPayload(BaseModel):
    user_id: str
    risk_percent: float = 1.0
    max_open_trades: int = 3
    scalping_enabled: bool = False
    min_confidence: float = 70.0
    daily_loss_limit: float = 5.0
    # Broker-specific symbol suffix — set once so trades use the right symbol.
    # Example: if your MT5 shows "BTCUSDZ", set suffix to "Z".
    broker_symbol_suffix: str = ""


@router.post("/settings")
async def save_settings(payload: SettingsPayload):
    settings = payload.model_dump(exclude={"user_id"})
    await mt5_repo.upsert_settings(payload.user_id, settings)
    return {"ok": True}


@router.get("/settings/{user_id}")
async def get_settings(user_id: str):
    return await mt5_repo.get_settings(user_id)


# ── Scalping toggle ───────────────────────────────────────────────────────────

class ScalpToggle(BaseModel):
    user_id: str


@router.post("/scalping/toggle")
async def toggle_scalping(payload: ScalpToggle):
    settings = await mt5_repo.get_settings(payload.user_id)
    settings["scalping_enabled"] = not settings.get("scalping_enabled", False)
    await mt5_repo.upsert_settings(payload.user_id, settings)
    return {"scalping_enabled": settings["scalping_enabled"]}


# ── Scalping execute ──────────────────────────────────────────────────────────

class ScalpExecutePayload(BaseModel):
    user_id: str
    symbol: str
    direction: str      # "BUY" or "SELL"
    confidence: float = 70.0
    broker_symbol_override: Optional[str] = None
    # "quick" skips the min_confidence gate — Quick Scalp always enters immediately
    mode: str = "smart"


@router.post("/scalping/execute")
async def execute_scalp_trade(payload: ScalpExecutePayload):
    """
    Execute a scalp trade via MetaAPI.
    Symbol is normalised to canonical then re-suffixed with the user's stored
    broker suffix, so the broker receives its exact symbol name.
    """
    from app.mt5.metaapi_client import place_trade
    from app.mt5.risk_calculator import calculate_lot_size
    from app.models.mt5 import TradeDirection, TradeOrder, RiskSettings

    direction = payload.direction.upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="direction must be BUY or SELL")

    account = await mt5_repo.get_mt5_account(payload.user_id)
    if not account:
        raise HTTPException(status_code=404, detail="MT5 account not found — connect first")

    metaapi_id = account.get("metaapi_id")
    if not metaapi_id:
        raise HTTPException(
            status_code=400,
            detail="MetaAPI not connected — use EA mode or reconnect via MetaAPI"
        )

    settings_raw = await mt5_repo.get_settings(payload.user_id)
    risk_kwargs = {k: v for k, v in settings_raw.items() if k in RiskSettings.model_fields}
    settings = RiskSettings(**risk_kwargs)

    # Resolve broker symbol
    if payload.broker_symbol_override:
        broker_symbol = payload.broker_symbol_override
    else:
        canonical = normalize_for_data(payload.symbol.upper())
        stored_suffix = settings_raw.get("broker_symbol_suffix", "")
        broker_symbol = to_broker_symbol(canonical, stored_suffix)

    logger.info(
        f"[execute] user={payload.user_id} "
        f"input={payload.symbol!r} → broker_symbol={broker_symbol!r}"
    )

    # Quick Scalp always enters immediately — confidence gate is for Smart Scalp only
    if payload.mode != "quick" and payload.confidence < settings.min_confidence:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Signal confidence {payload.confidence:.0f}% is below your minimum "
                f"{settings.min_confidence:.0f}% — adjust Risk Settings to lower the threshold, "
                f"or switch to Quick Scalp mode which enters immediately"
            )
        )

    open_count = await mt5_repo.open_trade_count(payload.user_id)
    if open_count >= settings.max_open_trades:
        raise HTTPException(
            status_code=400,
            detail=f"Max open trades ({settings.max_open_trades}) already reached"
        )

    if await mt5_repo.daily_loss_exceeded(payload.user_id, settings):
        raise HTTPException(status_code=400, detail="Daily loss limit reached — trading paused")

    balance = account.get("balance") or 1000.0
    lot = calculate_lot_size(broker_symbol, balance, settings, None)

    canonical_for_record = normalize_for_data(payload.symbol.upper())
    api_key = account.get("api_key", "")
    order = TradeOrder(
        user_id=payload.user_id,
        api_key=api_key,
        asset=canonical_for_record,
        direction=TradeDirection(direction),
        lot_size=lot,
        confidence=payload.confidence,
        timeframe="5m",
        comment=f"RainX | {broker_symbol}",
    )
    order_id = await mt5_repo.insert_trade_order(order)

    try:
        result = await place_trade(
            metaapi_id=metaapi_id,
            symbol=broker_symbol,
            direction=direction,
            lot_size=lot,
            stop_loss=None,
            take_profit=None,
        )
    except Exception as e:
        await mt5_repo.mark_trade_failed(order_id, str(e))
        raise HTTPException(status_code=502, detail=f"MetaAPI trade error: {e}")

    if result.get("success"):
        await mt5_repo.update_trade_opened(
            api_key, order_id,
            result["ticket"], result.get("open_price", 0),
        )
        logger.info(
            f"[execute] Trade opened: {broker_symbol} {direction} "
            f"lot={lot} ticket={result['ticket']} user={payload.user_id}"
        )
        return {
            "ok": True,
            "ticket": result["ticket"],
            "lot_size": lot,
            "open_price": result.get("open_price"),
            "broker_symbol": broker_symbol,
        }
    else:
        await mt5_repo.mark_trade_failed(order_id, result.get("error", ""))
        raise HTTPException(
            status_code=502,
            detail=f"Trade rejected by broker: {result.get('error', 'Unknown error')}"
        )


# ── Trades ────────────────────────────────────────────────────────────────────

@router.get("/trades/{user_id}")
async def get_trades(user_id: str):
    return {"trades": await mt5_repo.get_open_trades(user_id)}


@router.get("/history/{user_id}")
async def get_history(user_id: str, limit: int = 20):
    return {"history": await mt5_repo.get_trade_history(user_id, limit=limit)}


@router.get("/performance/{user_id}")
async def get_performance(user_id: str):
    return await mt5_repo.get_performance_summary(user_id)
