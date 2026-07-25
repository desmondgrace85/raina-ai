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
  GET  /mt5/account/{user_id}  — poll connection status (fetches live balance when missing)
  POST /mt5/settings           — save risk settings
  GET  /mt5/settings/{user_id}
  GET  /mt5/trades/{user_id}
  GET  /mt5/history/{user_id}
  GET  /mt5/performance/{user_id}
  POST /mt5/scalping/toggle    — enable/disable background auto-scalping
  POST /mt5/scalping/execute   — immediately execute a single scalp trade via MetaAPI

Symbol handling:
  Raina AI operates on canonical symbols (BTCUSD, XAUUSD, EURUSD…).
  Each user's MT5 broker may use a different variant for the same asset:
    BTCUSDZ, BTCUSDm, BTCUSDT, XAUUSD+, EURUSDr …
  When a user connects, we auto-detect and store their broker_symbol_suffix.
  All trade orders sent via MetaAPI use that suffix so the broker accepts them.
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.models.mt5 import TradeClose, TradeResult, EAHeartbeat
from app.mt5.symbol_utils import normalize_for_data, detect_broker_suffix, to_broker_symbol
from app.storage import mt5_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mt5", tags=["mt5"])


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
    # If omitted, suffix defaults to "" (canonical symbols used for trading).
    sample_symbol: Optional[str] = None


@router.post('/connect/metaapi')
async def connect_metaapi(payload: MetaApiConnectPayload):
    """
    Provision the MetaAPI cloud account and return immediately.
    user_id is derived from mt5_login — no Telegram ID needed.
    The frontend polls GET /mt5/account/{user_id} for live status.

    broker_symbol_suffix is auto-detected from sample_symbol if provided,
    and stored so all trade orders use the user's exact broker symbol format.
    """
    from app.mt5.metaapi_client import provision_account, get_account_info
    user_id = payload.mt5_login  # broker account number is unique per user

    # Detect broker symbol suffix from the sample symbol (e.g. BTCUSDZ → "Z")
    broker_suffix = ""
    if payload.sample_symbol:
        broker_suffix = detect_broker_suffix(payload.sample_symbol)
        logger.info(
            f"Detected broker suffix '{broker_suffix}' from sample_symbol "
            f"'{payload.sample_symbol}' for user {user_id}"
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

    # Persist broker suffix in settings so trade executor can look it up
    if broker_suffix:
        existing_settings = await mt5_repo.get_settings(user_id)
        existing_settings["broker_symbol_suffix"] = broker_suffix
        await mt5_repo.upsert_settings(user_id, existing_settings)

    # Background task: wait for broker sync then update balance/equity
    async def _sync_later():
        try:
            await asyncio.sleep(90)
            info = await get_account_info(metaapi_id)
            if info and info.get("connected"):
                await mt5_repo.update_metaapi_heartbeat(
                    metaapi_id=metaapi_id,
                    broker=info.get("broker") or payload.mt5_server,
                    account_number=info.get("login") or payload.mt5_login,
                    balance=info.get("balance"),
                    equity=info.get("equity"),
                    account_mode=payload.account_mode,
                )
                logger.info(
                    f"MetaAPI account {metaapi_id} synced for user {user_id} — "
                    f"balance={info.get('balance')}"
                )
        except Exception as ex:
            logger.warning(f"Background MetaAPI sync failed for {user_id}: {ex}")

    asyncio.create_task(_sync_later())

    return {
        'connected': True,
        'user_id': user_id,
        'api_key': api_key,
        'metaapi_id': metaapi_id,
        'broker_name': payload.mt5_server,
        'account_number': payload.mt5_login,
        'account_mode': payload.account_mode,
        'broker_symbol_suffix': broker_suffix,
    }


# ── Account status ─────────────────────────────────────────────────────────────

@router.get('/account/{user_id}')
async def get_account(user_id: str):
    account = await mt5_repo.get_mt5_account(user_id)
    if not account:
        raise HTTPException(status_code=404, detail='Account not found')

    # If balance is missing or zero and MetaAPI is provisioned, fetch it live.
    # Hard cap at 12 s so the endpoint always returns quickly on Railway.
    metaapi_id = account.get("metaapi_id")
    stored_balance = account.get("balance")
    if metaapi_id and (stored_balance is None or stored_balance == 0):
        try:
            from app.mt5.metaapi_client import get_account_info
            info = await asyncio.wait_for(get_account_info(metaapi_id), timeout=12.0)
            if info.get("connected") and info.get("balance"):
                account = {**account, "balance": info["balance"], "equity": info.get("equity")}
                # Persist so the next poll is instant
                await mt5_repo.update_metaapi_heartbeat(
                    metaapi_id=metaapi_id,
                    broker=info.get("broker") or account.get("broker_name"),
                    account_number=info.get("login") or account.get("account_number"),
                    balance=info["balance"],
                    equity=info.get("equity"),
                    account_mode=account.get("account_mode", "demo"),
                )
        except asyncio.TimeoutError:
            logger.warning(f"Live balance fetch timed out for {user_id} — returning stored value")
        except Exception as e:
            logger.warning(f"Live balance fetch failed for {user_id}: {e}")

    return account


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
    # Leave blank if your broker uses standard symbols (BTCUSD, XAUUSD, etc.).
    broker_symbol_suffix: str = ""


@router.post("/settings")
async def save_settings(payload: SettingsPayload):
    settings = payload.model_dump(exclude={"user_id"})
    await mt5_repo.upsert_settings(payload.user_id, settings)
    return {"ok": True}


@router.get("/settings/{user_id}")
async def get_settings(user_id: str):
    return await mt5_repo.get_settings(user_id)


# ── Scalping toggle (enable/disable background auto-scalping) ─────────────────

class ScalpToggle(BaseModel):
    user_id: str


@router.post("/scalping/toggle")
async def toggle_scalping(payload: ScalpToggle):
    settings = await mt5_repo.get_settings(payload.user_id)
    settings["scalping_enabled"] = not settings.get("scalping_enabled", False)
    await mt5_repo.upsert_settings(payload.user_id, settings)
    return {"scalping_enabled": settings["scalping_enabled"]}


# ── Scalping execute — immediately place a single trade via MetaAPI ────────────

class ScalpExecutePayload(BaseModel):
    user_id: str
    symbol: str         # canonical OR broker-specific — we normalize either way
    direction: str      # "BUY" or "SELL"
    confidence: float = 70.0
    # Optional override: exact broker symbol to use for this trade.
    # If omitted, Raina AI looks up the user's stored broker_symbol_suffix
    # and constructs the broker symbol automatically.
    broker_symbol_override: Optional[str] = None


@router.post("/scalping/execute")
async def execute_scalp_trade(payload: ScalpExecutePayload):
    """
    Immediately execute a scalp trade for a specific user+signal via MetaAPI.
    Called by the RainX app for Quick Scalp and Smart Scalp signal execution.

    Symbol resolution:
      1. If broker_symbol_override is set, use it directly.
      2. Otherwise normalize the incoming symbol to canonical (BTCUSDZ → BTCUSD),
         then re-apply the user's stored broker suffix (BTCUSD + Z → BTCUSDZ).
      This ensures MetaAPI sends the exact symbol name the broker recognises,
      regardless of what variant the frontend passes.
    """
    from app.mt5.metaapi_client import place_trade
    from app.mt5.risk_calculator import calculate_lot_size
    from app.models.mt5 import TradeDirection, TradeOrder, RiskSettings

    direction = payload.direction.upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="direction must be BUY or SELL")

    # Look up the user's MT5 account
    account = await mt5_repo.get_mt5_account(payload.user_id)
    if not account:
        raise HTTPException(status_code=404, detail="MT5 account not found — connect first")

    metaapi_id = account.get("metaapi_id")
    if not metaapi_id:
        raise HTTPException(
            status_code=400,
            detail="MetaAPI not connected — use EA mode or reconnect via MetaAPI"
        )

    # Load risk settings (includes broker_symbol_suffix)
    settings_raw = await mt5_repo.get_settings(payload.user_id)
    risk_kwargs = {k: v for k, v in settings_raw.items() if k in RiskSettings.model_fields}
    settings = RiskSettings(**risk_kwargs)

    # ── Resolve the exact broker symbol for this trade ────────────────────────
    if payload.broker_symbol_override:
        # Explicit override from frontend (most precise)
        broker_symbol = payload.broker_symbol_override
    else:
        # Normalise the incoming symbol to canonical, then apply stored suffix
        canonical = normalize_for_data(payload.symbol.upper())
        stored_suffix = settings_raw.get("broker_symbol_suffix", "")
        broker_symbol = to_broker_symbol(canonical, stored_suffix)

    logger.info(
        f"[execute] Symbol resolution: "
        f"input={payload.symbol!r} → broker_symbol={broker_symbol!r} "
        f"(suffix={settings_raw.get('broker_symbol_suffix', '')!r})"
    )

    # Confidence gate
    if payload.confidence < settings.min_confidence:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Signal confidence {payload.confidence:.0f}% is below your minimum "
                f"{settings.min_confidence:.0f}% — adjust Risk Settings to lower the threshold"
            )
        )

    # Open trade count gate
    open_count = await mt5_repo.open_trade_count(payload.user_id)
    if open_count >= settings.max_open_trades:
        raise HTTPException(
            status_code=400,
            detail=f"Max open trades ({settings.max_open_trades}) already reached"
        )

    # Daily loss gate
    if await mt5_repo.daily_loss_exceeded(payload.user_id, settings):
        raise HTTPException(status_code=400, detail="Daily loss limit reached — trading paused")

    balance = account.get("balance") or 1000.0
    lot = calculate_lot_size(broker_symbol, balance, settings, None)

    # Insert a pending order record (store canonical symbol for internal tracking)
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

    # Place the trade via MetaAPI using the resolved broker symbol
    try:
        result = await place_trade(
            metaapi_id=metaapi_id,
            symbol=broker_symbol,   # ← broker's exact symbol name
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
            f"[execute] Trade opened for {payload.user_id}: "
            f"{broker_symbol} {direction} lot={lot} ticket={result['ticket']}"
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
